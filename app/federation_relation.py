from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Callable, Mapping, Sequence

from .federation_routing import (
    AdmissibilityGate,
    CapabilityGraph,
    CapabilityNode,
    Crossing,
    PreferenceModel,
    RoutingResult,
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class RelationStatus(str, Enum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"


def _require_text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _require_sha256(value: str, name: str) -> None:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")


@dataclass(frozen=True)
class RelationWitness:
    payload_sha256: str
    source_hmac_sha256: str
    target_hmac_sha256: str

    def __post_init__(self) -> None:
        _require_sha256(self.payload_sha256, "payload_sha256")
        _require_sha256(self.source_hmac_sha256, "source_hmac_sha256")
        _require_sha256(self.target_hmac_sha256, "target_hmac_sha256")


@dataclass(frozen=True)
class FederationRelation:
    """Directed, bilateral authorization for one federation boundary.

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
    witness: RelationWitness | None = None

    def __post_init__(self) -> None:
        for name in (
            "relation_id",
            "source_domain",
            "target_domain",
            "source_authority",
            "target_authority",
            "evidence_policy",
        ):
            _require_text(getattr(self, name), name)
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
    """Attach bilateral HMAC witness to an otherwise immutable relation."""
    if relation.witness is not None:
        raise ValueError("relation is already witnessed")
    digest = relation.payload_sha256()
    witness = RelationWitness(
        payload_sha256=digest,
        source_hmac_sha256=_relation_signature(source_secret, digest, "source"),
        target_hmac_sha256=_relation_signature(target_secret, digest, "target"),
    )
    return replace(relation, witness=witness)


@dataclass
class RelationIntegrityGate:
    """Fail-closed bilateral relation verification.

    authority_secrets is intentionally injected by the caller. Secrets never
    become relation fields or receipt material.
    """

    authority_secrets: Mapping[str, str]
    now: Callable[[], int] = field(default=lambda: int(time.time()))

    def evaluate(
        self,
        relation: FederationRelation,
        request: RelationRequest,
        *,
        evaluated_at: int | None = None,
    ) -> RelationDecision:
        timestamp = int(self.now()) if evaluated_at is None else int(evaluated_at)
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

        witness = relation.witness
        if witness is None:
            reasons.append("missing_bilateral_witness")
        else:
            if not hmac.compare_digest(witness.payload_sha256, digest):
                reasons.append("witness_payload_hash_mismatch")

            source_secret = self.authority_secrets.get(relation.source_authority)
            target_secret = self.authority_secrets.get(relation.target_authority)
            if not source_secret:
                reasons.append("unknown_source_authority")
            else:
                expected = _relation_signature(source_secret, digest, "source")
                if not hmac.compare_digest(expected, witness.source_hmac_sha256):
                    reasons.append("invalid_source_witness")
            if not target_secret:
                reasons.append("unknown_target_authority")
            else:
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
    blocked_relations: Mapping[str, Sequence[str]]
    relation_receipt_sha256: str


class FederatedCapabilityGraph:
    """Capability routing where every cross-domain edge has a valid relation."""

    def __init__(
        self,
        nodes: Sequence[CapabilityNode],
        crossings: Sequence[FederatedCrossing],
        capability_gate: AdmissibilityGate,
        preference: PreferenceModel,
        relations: Sequence[FederationRelation],
        relation_gate: RelationIntegrityGate,
        *,
        evaluated_at: int | None = None,
    ) -> None:
        relation_by_id = {relation.relation_id: relation for relation in relations}
        if len(relation_by_id) != len(relations):
            raise ValueError("duplicate relation_id")

        pair_keys = [(crossing.src, crossing.dst) for crossing in crossings]
        if len(set(pair_keys)) != len(pair_keys):
            raise ValueError("parallel federated crossings are not supported in v1")

        self._crossing_by_pair: dict[tuple[str, str], FederatedCrossing] = {}
        self.blocked_relations: dict[str, list[str]] = {}
        valid_crossings: list[Crossing] = []
        self._relation_digest_by_id: dict[str, str] = {}

        for crossing in crossings:
            key = f"{crossing.src}->{crossing.dst}:{crossing.relation_id}"
            relation = relation_by_id.get(crossing.relation_id)
            if relation is None:
                self.blocked_relations[key] = ["relation_not_found"]
                continue
            decision = relation_gate.evaluate(
                relation,
                RelationRequest(
                    source_domain=crossing.src,
                    target_domain=crossing.dst,
                    contract_hash=crossing.contract_hash,
                    capability=crossing.capability,
                ),
                evaluated_at=evaluated_at,
            )
            if not decision.admissible:
                self.blocked_relations[key] = list(decision.reasons)
                continue
            self._crossing_by_pair[(crossing.src, crossing.dst)] = crossing
            self._relation_digest_by_id[relation.relation_id] = decision.relation_sha256
            valid_crossings.append(
                Crossing(crossing.src, crossing.dst, crossing.cost, crossing.reason)
            )

        self._graph = CapabilityGraph(
            nodes=nodes,
            crossings=valid_crossings,
            gate=capability_gate,
            preference=preference,
        )

    @property
    def blocked(self) -> Mapping[str, Sequence[str]]:
        return {
            **{key: tuple(value) for key, value in self._graph.blocked.items()},
            **{key: tuple(value) for key, value in self.blocked_relations.items()},
        }

    def route(self, origin: str, targets: Sequence[str] | None = None) -> FederatedRoutingResult:
        route = self._graph.route(origin, targets)
        traversed: list[str] = []
        for src, dst in zip(route.path, route.path[1:]):
            crossing = self._crossing_by_pair.get((src, dst))
            if crossing is None:
                raise RuntimeError("route crossed an edge without a validated federation relation")
            traversed.append(crossing.relation_id)

        payload = {
            "route_receipt_sha256": route.receipt_sha256,
            "traversed_relations": traversed,
            "relation_hashes": {
                relation_id: self._relation_digest_by_id[relation_id]
                for relation_id in sorted(set(traversed))
            },
            "blocked_relations": {
                key: sorted(value) for key, value in sorted(self.blocked_relations.items())
            },
        }
        receipt = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return FederatedRoutingResult(
            route=route,
            traversed_relations=tuple(traversed),
            blocked_relations=self.blocked_relations,
            relation_receipt_sha256=receipt,
        )
