from __future__ import annotations

import hmac
import re
import time
from dataclasses import dataclass, replace
from typing import Callable, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .federation_relation import (
    HMAC_SHARED_SECRET_SCHEME,
    FederationRelation,
    RelationDecision,
    RelationIntegrityGate,
    RelationRequest,
    RelationStatus,
)

ED25519_PUBLIC_KEY_SCHEME = "ED25519-PUBLIC-KEY-V1"
_PUBLIC_KEY_RE = re.compile(r"^[0-9a-f]{64}$")
_SIGNATURE_RE = re.compile(r"^[0-9a-f]{128}$")


def _require_sha256(value: str, name: str) -> None:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")
    try:
        raw = bytes.fromhex(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest") from exc
    if len(raw) != 32 or value != value.lower():
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")


def _require_signature(value: str | None, name: str) -> None:
    if value is None:
        return
    if not isinstance(value, str) or _SIGNATURE_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase 64-byte Ed25519 signature hex")


def _public_key_bytes(key: Ed25519PublicKey) -> bytes:
    return key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def ed25519_public_key_hex(key: Ed25519PrivateKey | Ed25519PublicKey) -> str:
    """Return canonical raw Ed25519 public-key bytes as lowercase hex."""
    if isinstance(key, Ed25519PrivateKey):
        key = key.public_key()
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("key must be an Ed25519 private or public key")
    return _public_key_bytes(key).hex()


def _coerce_public_key(value: str | Ed25519PublicKey) -> Ed25519PublicKey:
    if isinstance(value, Ed25519PublicKey):
        return value
    if not isinstance(value, str) or _PUBLIC_KEY_RE.fullmatch(value) is None:
        raise ValueError("Ed25519 public key must be 32-byte lowercase raw hex")
    return Ed25519PublicKey.from_public_bytes(bytes.fromhex(value))


def _signing_message(payload_sha256: str, role: str) -> bytes:
    _require_sha256(payload_sha256, "payload_sha256")
    if role not in {"source", "target"}:
        raise ValueError("role must be source or target")
    return (
        "matverse.federation-relation.v1\n"
        f"{ED25519_PUBLIC_KEY_SCHEME}\n"
        f"{role}\n"
        f"{payload_sha256}"
    ).encode("utf-8")


@dataclass(frozen=True)
class Ed25519RelationWitness:
    """Bilateral signatures over the existing canonical relation payload.

    Signatures can be attached independently and in either order. A partial
    witness is intentionally representable so cross-domain signing workflows do
    not require co-locating private keys; verification remains fail-closed until
    both signatures are present and valid.
    """

    scheme: str
    payload_sha256: str
    source_signature_hex: str | None = None
    target_signature_hex: str | None = None

    def __post_init__(self) -> None:
        if self.scheme != ED25519_PUBLIC_KEY_SCHEME:
            raise ValueError("invalid Ed25519 witness scheme")
        _require_sha256(self.payload_sha256, "payload_sha256")
        _require_signature(self.source_signature_hex, "source_signature_hex")
        _require_signature(self.target_signature_hex, "target_signature_hex")


def _witness_for_signing(
    relation: FederationRelation,
) -> tuple[str, Ed25519RelationWitness]:
    if relation.witness_scheme != ED25519_PUBLIC_KEY_SCHEME:
        raise ValueError(f"unsupported witness scheme: {relation.witness_scheme}")
    digest = relation.payload_sha256()
    witness = relation.witness
    if witness is None:
        return digest, Ed25519RelationWitness(
            scheme=ED25519_PUBLIC_KEY_SCHEME,
            payload_sha256=digest,
        )
    if not isinstance(witness, Ed25519RelationWitness):
        raise ValueError("relation carries a witness from a different scheme")
    if not hmac.compare_digest(witness.payload_sha256, digest):
        raise ValueError("existing witness payload does not match current relation")
    return digest, witness


def sign_relation_ed25519_source(
    relation: FederationRelation,
    *,
    private_key: Ed25519PrivateKey,
) -> FederationRelation:
    """Attach the source signature without persisting or exporting the private key."""
    if not isinstance(private_key, Ed25519PrivateKey):
        raise ValueError("private_key must be an Ed25519PrivateKey")
    digest, witness = _witness_for_signing(relation)
    if witness.source_signature_hex is not None:
        raise ValueError("source authority has already signed this relation")
    return replace(
        relation,
        witness=replace(
            witness,
            source_signature_hex=private_key.sign(
                _signing_message(digest, "source")
            ).hex(),
        ),
    )


def sign_relation_ed25519_target(
    relation: FederationRelation,
    *,
    private_key: Ed25519PrivateKey,
) -> FederationRelation:
    """Attach the target signature without persisting or exporting the private key."""
    if not isinstance(private_key, Ed25519PrivateKey):
        raise ValueError("private_key must be an Ed25519PrivateKey")
    digest, witness = _witness_for_signing(relation)
    if witness.target_signature_hex is not None:
        raise ValueError("target authority has already signed this relation")
    return replace(
        relation,
        witness=replace(
            witness,
            target_signature_hex=private_key.sign(
                _signing_message(digest, "target")
            ).hex(),
        ),
    )


def _common_reasons(
    relation: FederationRelation,
    request: RelationRequest,
    timestamp: int,
) -> list[str]:
    reasons: list[str] = []
    if relation.status is not RelationStatus.ACTIVE:
        reasons.append(f"status:{relation.status.value}")
    if request.source_domain != relation.source_domain:
        reasons.append("source_domain_mismatch")
    if request.target_domain != relation.target_domain:
        reasons.append("target_domain_mismatch")
    if not hmac.compare_digest(request.contract_hash, relation.contract_hash):
        reasons.append("contract_hash_mismatch")
    if request.capability not in relation.capabilities:
        reasons.append("capability_out_of_scope")
    if timestamp < relation.valid_from:
        reasons.append("relation_not_yet_valid")
    if timestamp >= relation.valid_until:
        reasons.append("relation_expired")
    return reasons


class Ed25519RelationIntegrityGate:
    """Fail-closed verifier containing public material only."""

    def __init__(
        self,
        authority_public_keys: Mapping[str, str | Ed25519PublicKey],
        now: Callable[[], int] | None = None,
    ) -> None:
        public_keys: dict[str, Ed25519PublicKey] = {}
        for authority_id, public_key in dict(authority_public_keys).items():
            if not isinstance(authority_id, str) or not authority_id.strip():
                raise ValueError("authority ids must be non-empty strings")
            public_keys[authority_id] = _coerce_public_key(public_key)
        self._authority_public_keys = public_keys
        self._now = now or (lambda: int(time.time()))

    def evaluate(
        self,
        relation: FederationRelation,
        request: RelationRequest,
    ) -> RelationDecision:
        timestamp = int(self._now())
        digest = relation.payload_sha256()
        reasons = _common_reasons(relation, request, timestamp)

        if relation.witness_scheme != ED25519_PUBLIC_KEY_SCHEME:
            reasons.append("unsupported_witness_scheme")
            return RelationDecision(
                relation_id=relation.relation_id,
                admissible=False,
                reasons=tuple(reasons),
                relation_sha256=digest,
                evaluated_at=timestamp,
            )

        witness = relation.witness
        if witness is None:
            reasons.append("missing_bilateral_witness")
        elif not isinstance(witness, Ed25519RelationWitness):
            reasons.append("witness_type_mismatch")
        else:
            if witness.scheme != relation.witness_scheme:
                reasons.append("witness_scheme_mismatch")
            if not hmac.compare_digest(witness.payload_sha256, digest):
                reasons.append("witness_payload_hash_mismatch")

            source_public_key = self._authority_public_keys.get(
                relation.source_authority
            )
            target_public_key = self._authority_public_keys.get(
                relation.target_authority
            )
            if source_public_key is None:
                reasons.append("unknown_source_authority")
            if target_public_key is None:
                reasons.append("unknown_target_authority")
            if source_public_key is not None and target_public_key is not None:
                if hmac.compare_digest(
                    _public_key_bytes(source_public_key),
                    _public_key_bytes(target_public_key),
                ):
                    reasons.append("shared_authority_public_key")

            if witness.source_signature_hex is None:
                reasons.append("missing_source_witness")
            elif source_public_key is not None:
                try:
                    source_public_key.verify(
                        bytes.fromhex(witness.source_signature_hex),
                        _signing_message(digest, "source"),
                    )
                except InvalidSignature:
                    reasons.append("invalid_source_witness")

            if witness.target_signature_hex is None:
                reasons.append("missing_target_witness")
            elif target_public_key is not None:
                try:
                    target_public_key.verify(
                        bytes.fromhex(witness.target_signature_hex),
                        _signing_message(digest, "target"),
                    )
                except InvalidSignature:
                    reasons.append("invalid_target_witness")

        return RelationDecision(
            relation_id=relation.relation_id,
            admissible=not reasons,
            reasons=tuple(reasons),
            relation_sha256=digest,
            evaluated_at=timestamp,
        )


class HybridRelationIntegrityGate:
    """Migration gate supporting legacy HMAC and Ed25519 relations side by side.

    HMAC verification delegates to the original trust-domain gate. Ed25519
    verification uses only public keys. Unknown schemes are rejected without
    attempting cross-scheme interpretation.
    """

    def __init__(
        self,
        *,
        authority_secrets: Mapping[str, str] | None = None,
        authority_public_keys: Mapping[str, str | Ed25519PublicKey] | None = None,
        now: Callable[[], int] | None = None,
    ) -> None:
        self._now = now or (lambda: int(time.time()))
        self._hmac_gate = RelationIntegrityGate(
            authority_secrets or {},
            now=self._now,
        )
        self._ed25519_gate = Ed25519RelationIntegrityGate(
            authority_public_keys or {},
            now=self._now,
        )

    def evaluate(
        self,
        relation: FederationRelation,
        request: RelationRequest,
    ) -> RelationDecision:
        if relation.witness_scheme == HMAC_SHARED_SECRET_SCHEME:
            return self._hmac_gate.evaluate(relation, request)
        if relation.witness_scheme == ED25519_PUBLIC_KEY_SCHEME:
            return self._ed25519_gate.evaluate(relation, request)

        timestamp = int(self._now())
        reasons = _common_reasons(relation, request, timestamp)
        reasons.append("unsupported_witness_scheme")
        if relation.witness is None:
            reasons.append("missing_bilateral_witness")
        return RelationDecision(
            relation_id=relation.relation_id,
            admissible=False,
            reasons=tuple(reasons),
            relation_sha256=relation.payload_sha256(),
            evaluated_at=timestamp,
        )
