from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass, replace
from enum import Enum
from types import MappingProxyType
from typing import Callable, Mapping, Protocol, Sequence

from .federation_routing import (
    AdmissibilityGate,
    CapabilityGraph,
    CapabilityNode,
    Crossing,
    PreferenceModel,
    RoutingResult,
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
HMAC_SHARED_SECRET_SCHEME = "HMAC-SHA256-SHARED-SECRET-V1"
_ROUTING_GOVERNED_WITNESS_SCHEMES = frozenset({"ED25519-PUBLIC-KEY-V1"})


class RelationStatus(str, Enum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"


def _require_text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _require_sha256(value: str, name: str) -> None:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")


def _blocked_key(src: str, dst: str, relation_id: str) -> str:
    return json.dumps([src, dst, relation_id], separators=(",", ":"))


class FederationWitness(Protocol):
    """Structural contract shared by all federation witness schemes."""

    scheme: str
    payload_sha256: str


@dataclass(frozen=True)
class RelationWitness:
    scheme: str
    payload_sha256: str
    source_hmac_sha256: str
    target_hmac_sha256: str

    def __post_init__(self) -> None:
        _require_text(self.scheme, "scheme")
        _require_sha256(self.payload_sha256, "payload_sha256")
        _require_sha256(self.source_hmac_sha256, "source_hmac_sha256")
        _require_sha256(self.target_hmac_sha256, "target_hmac_sha256")


@dataclass(frozen=True)
class FederationRelation:
    """Directed bilateral authorization for one federation boundary.

    A relation is not trusted because both domains exist. It is trusted only when
    its canonical payload is witnessed by both declared authorities and the
    request remains inside its contract, capability scope, and validity window.
    """

    relation_id: str
    source_domain: str
    target_domain: str
    source_authority: str
    target_authority: str
    contract_hash: str
    capabilities: tuple[str, ...]
    valid_from: int
    valid_until: int
    status: RelationStatus = RelationStatus.ACTIVE
    evidence_policy: str = "receipt_required"
    witness_scheme: str = HMAC_SHARED_SECRET_SCHEME
    witness: FederationWitness | None = None

    def __post_init__(self) -> None:
        for name in (
            "relation_id",
            "source_domain",
            "target_domain",
            "source_authority",
            "target_authority",
            "evidence_policy",
            "witness_scheme",
        ):
            _require_text(getattr(self, name), name)
        if not isinstance(self.status, RelationStatus):
            raise ValueError("status must be a RelationStatus")
        if self.source_domain == self.target_domain:
            raise ValueError("federation relation must cross distinct domains")
        if self.source_authority == self.target_authority:
            raise ValueError("federation relation requires distinct authorities")
        _require_sha256(self.contract_hash, "contract_hash")
        if not isinstance(self.valid_from, int) or not isinstance(self.valid_until, int):
            raise ValueError("validity bounds must be integer unix timestamps")
        if self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be greater than valid_from")
        if not self.capabilities:
            raise ValueError("at least one capability is required")
        if any(not isinstance(item, str) or not item.strip() for item in self.capabilities):
            raise ValueError("capabilities must be non-empty strings")
        canonical_capabilities = tuple(sorted(set(self.capabilities)))
        if self.capabilities != canonical_capabilities:
            raise ValueError("capabilities must be unique and lexicographically sorted")
        if "*" in self.capabilities:
            raise ValueError("wildcard federation capability is forbidden")

    def canonical_payload(self) -> dict[str, object]:
        return {
            "relation_id": self.relation_id,
            "source_domain": self.source_domain,
            "target_domain": self.target_domain,
            "source_authority": self.source_authority,
            "target_authority": self.target_authority,
            "contract_hash": self.contract_hash,
            "capabilities": list(self.capabilities),
            "valid_from": self.valid_from,
            "valid_until": self.valid_until,
            "status": self.status.value,
            "evidence_policy": self.evidence_policy,
            "witness_scheme": self.witness_scheme,
        }

    def payload_sha256(self) -> str:
        encoded = json.dumps(
            self.canonical_payload(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class RelationRequest:
    source_domain: str
    target_domain: str
    contract_hash: str
    capability: str

    def __post_init__(self) -> None:
        _require_text(self.source_domain, "source_domain")
        _require_text(self.target_domain, "target_domain")
        _require_text(self.capability, "capability")
        _require_sha256(self.contract_hash, "contract_hash")


@dataclass(frozen=True)
class RelationDecision:
    relation_id: str
    admissible: bool
    reasons: tuple[str, ...]
    relation_sha256: str
    evaluated_at: int


class RelationGate(Protocol):
    """Verifier interface consumed by federated routing."""

    def evaluate(
        self,
        relation: FederationRelation,
        request: RelationRequest,
    ) -> RelationDecision: ...


def _relation_signature(secret: str, relation_sha256: str, role: str) -> str:
    if not isinstance(secret, str) or not secret:
        raise ValueError("authority secret must be non-empty")
    message = f"matverse.federation-relation.v1\n{role}\n{relation_sha256}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def sign_relation(
    relation: FederationRelation,
    *,
    source_secret: str,
    target_secret: str,
) -> FederationRelation:
    """Attach the current trust-domain HMAC witness to an immutable relation.

    This helper deliberately does not claim independent-domain cryptographic
    sovereignty. A public-key witness adapter is a separate promotion gate.
    """
    if relation.witness is not None:
        raise ValueError("relation is already witnessed")
    if relation.witness_scheme != HMAC_SHARED_SECRET_SCHEME:
        raise ValueError(f"unsupported witness scheme: {relation.witness_scheme}")
    _require_text(source_secret, "source_secret")
    _require_text(target_secret, "target_secret")
    if hmac.compare_digest(source_secret, target_secret):
        raise ValueError("source and target authority secrets must be distinct")
    digest = relation.payload_sha256()
    witness = RelationWitness(
        scheme=HMAC_SHARED_SECRET_SCHEME,
        payload_sha256=digest,
        source_hmac_sha256=_relation_signature(source_secret, digest, "source"),
        target_hmac_sha256=_relation_signature(target_secret, digest, "target"),
    )
    return replace(relation, witness=witness)


class RelationIntegrityGate:
    """Fail-closed relation verification for the current shared-secret trust domain.

    Authority secrets are copied into the verifier and never persisted inside
    relations or receipts. This is compatible with the repository's current HMAC
    principal model, but by itself is not evidence of provider-independent or
    administratively independent federation.
    """

    def __init__(
        self,
        authority_secrets: Mapping[str, str],
        now: Callable[[], int] | None = None,
    ) -> None:
        copied = dict(authority_secrets)
        if any(not isinstance(key, str) or not key for key in copied):
            raise ValueError("authority ids must be non-empty strings")
        if any(not isinstance(secret, str) or not secret for secret in copied.values()):
            raise ValueError("authority secrets must be non-empty strings")
        self._authority_secrets = copied
        self._now = now or (lambda: int(time.time()))

    def evaluate(
        self,
        relation: FederationRelation,
        request: RelationRequest,
    ) -> RelationDecision:
        timestamp = int(self._now())
        reasons: list[str] = []
        digest = relation.payload_sha256()

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
        if relation.witness_scheme != HMAC_SHARED_SECRET_SCHEME:
            reasons.append("unsupported_witness_scheme")

        witness = relation.witness
        if witness is None:
            reasons.append("missing_bilateral_witness")
        elif not isinstance(witness, RelationWitness):
            reasons.append("witness_type_mismatch")
        else:
            if witness.scheme != relation.witness_scheme:
                reasons.append("witness_scheme_mismatch")
            if not hmac.compare_digest(witness.payload_sha256, digest):
                reasons.append("witness_payload_hash_mismatch")

            source_secret = self._authority_secrets.get(relation.source_authority)
            target_secret = self._authority_secrets.get(relation.target_authority)
            if not source_secret:
                reasons.append("unknown_source_authority")
            if not target_secret:
                reasons.append("unknown_target_authority")
            if source_secret and target_secret and hmac.compare_digest(source_secret, target_secret):
                reasons.append("shared_authority_secret")
            if source_secret:
                expected = _relation_signature(source_secret, digest, "source")
                if not hmac.compare_digest(expected, witness.source_hmac_sha256):
                    reasons.append("invalid_source_witness")
            if target_secret:
                expected = _relation_signature(target_secret, digest, "target")
                if not hmac.compare_digest(expected, witness.target_hmac_sha256):
                    reasons.append("invalid_target_witness")

        return RelationDecision(
            relation_id=relation.relation_id,
            admissible=not reasons,
            reasons=tuple(reasons),
            relation_sha256=digest,
            evaluated_at=timestamp,
        )


@dataclass(frozen=True)
class FederatedCrossing:
    src: str
    dst: str
    cost: float
    relation_id: str
    capability: str
    contract_hash: str
    reason: str = ""

    def __post_init__(self) -> None:
        _require_text(self.src, "src")
        _require_text(self.dst, "dst")
        _require_text(self.relation_id, "relation_id")
        _require_text(self.capability, "capability")
        _require_sha256(self.contract_hash, "contract_hash")
        if self.cost < 0:
            raise ValueError("crossing cost must be non-negative")


@dataclass(frozen=True)
class FederatedRoutingResult:
    route: RoutingResult
    traversed_relations: tuple[str, ...]
    blocked_relations: Mapping[str, tuple[str, ...]]
    relation_receipt_sha256: str


RelationSource = Sequence[FederationRelation] | Callable[[], Sequence[FederationRelation]]


class FederatedCapabilityGraph:
    """Capability routing where every cross-domain edge has a current valid relation.

    Relations and their temporal validity are re-evaluated for every route call.
    Supplying a callable relation source allows a registry to revoke or replace a
    relation without reconstructing the graph object. The requested transfer
    capability is supplied independently to route() and must be valid on every
    traversable boundary.

    Witness schemes listed in ``_ROUTING_GOVERNED_WITNESS_SCHEMES`` are not
    routable through cryptographic-only verifiers. Their gate must explicitly
    declare ``enforces_key_lifecycle = True``. This prevents a caller from
    bypassing key binding, rotation, or revocation by supplying a weaker gate.
    """

    def __init__(
        self,
        nodes: Sequence[CapabilityNode],
        crossings: Sequence[FederatedCrossing],
        capability_gate: AdmissibilityGate,
        preference: PreferenceModel,
        relations: RelationSource,
        relation_gate: RelationGate,
    ) -> None:
        pair_keys = [(crossing.src, crossing.dst) for crossing in crossings]
        if len(set(pair_keys)) != len(pair_keys):
            raise ValueError("parallel federated crossings are not supported in v1")
        self._nodes = tuple(nodes)
        self._crossings = tuple(crossings)
        self._capability_gate = capability_gate
        self._preference = preference
        self._relation_source = relations
        self._relation_gate = relation_gate

    def _current_relations(self) -> tuple[FederationRelation, ...]:
        current = self._relation_source() if callable(self._relation_source) else self._relation_source
        return tuple(current)

    def _validated_state(
        self,
        capability: str,
    ) -> tuple[
        CapabilityGraph,
        dict[tuple[str, str], FederatedCrossing],
        dict[str, list[str]],
        dict[str, str],
    ]:
        _require_text(capability, "capability")
        relations = self._current_relations()
        relation_by_id = {relation.relation_id: relation for relation in relations}
        if len(relation_by_id) != len(relations):
            raise ValueError("duplicate relation_id")

        crossing_by_pair: dict[tuple[str, str], FederatedCrossing] = {}
        blocked_relations: dict[str, list[str]] = {}
        valid_crossings: list[Crossing] = []
        relation_digest_by_id: dict[str, str] = {}

        for crossing in self._crossings:
            key = _blocked_key(crossing.src, crossing.dst, crossing.relation_id)
            reasons: list[str] = []
            if crossing.capability != capability:
                reasons.append("crossing_capability_mismatch")
            relation = relation_by_id.get(crossing.relation_id)
            if relation is None:
                reasons.append("relation_not_found")
            else:
                lifecycle_required = relation.witness_scheme in _ROUTING_GOVERNED_WITNESS_SCHEMES
                lifecycle_enforced = bool(
                    getattr(self._relation_gate, "enforces_key_lifecycle", False)
                )
                if lifecycle_required and not lifecycle_enforced:
                    reasons.append("governed_key_lifecycle_required")
                else:
                    decision = self._relation_gate.evaluate(
                        relation,
                        RelationRequest(
                            source_domain=crossing.src,
                            target_domain=crossing.dst,
                            contract_hash=crossing.contract_hash,
                            capability=capability,
                        ),
                    )
                    reasons.extend(decision.reasons)
                    if not reasons:
                        crossing_by_pair[(crossing.src, crossing.dst)] = crossing
                        relation_digest_by_id[relation.relation_id] = decision.relation_sha256
                        valid_crossings.append(
                            Crossing(crossing.src, crossing.dst, crossing.cost, crossing.reason)
                        )
            if reasons:
                blocked_relations[key] = reasons

        graph = CapabilityGraph(
            nodes=self._nodes,
            crossings=valid_crossings,
            gate=self._capability_gate,
            preference=self._preference,
        )
        return graph, crossing_by_pair, blocked_relations, relation_digest_by_id

    def blocked_for(self, capability: str) -> Mapping[str, tuple[str, ...]]:
        graph, _, blocked_relations, _ = self._validated_state(capability)
        snapshot = {
            **{key: tuple(value) for key, value in graph.blocked.items()},
            **{key: tuple(value) for key, value in blocked_relations.items()},
        }
        return MappingProxyType(snapshot)

    def route(
        self,
        origin: str,
        targets: Sequence[str] | None = None,
        *,
        capability: str,
    ) -> FederatedRoutingResult:
        graph, crossing_by_pair, blocked_relations, relation_digest_by_id = self._validated_state(capability)
        route = graph.route(origin, targets)
        traversed: list[str] = []
        for src, dst in zip(route.path, route.path[1:]):
            crossing = crossing_by_pair.get((src, dst))
            if crossing is None:
                raise RuntimeError("route crossed an edge without a validated federation relation")
            traversed.append(crossing.relation_id)

        immutable_blocked = {
            key: tuple(value) for key, value in blocked_relations.items()
        }
        payload = {
            "requested_capability": capability,
            "route_receipt_sha256": route.receipt_sha256,
            "traversed_relations": traversed,
            "relation_hashes": {
                relation_id: relation_digest_by_id[relation_id]
                for relation_id in sorted(set(traversed))
            },
            "blocked_relations": {
                key: list(value) for key, value in sorted(immutable_blocked.items())
            },
        }
        receipt = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return FederatedRoutingResult(
            route=route,
            traversed_relations=tuple(traversed),
            blocked_relations=MappingProxyType(immutable_blocked),
            relation_receipt_sha256=receipt,
        )
