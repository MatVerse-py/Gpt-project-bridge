from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Iterable

from .evidence import canonical_json, evidence_receipt, sha256_text

SCHEMA = "matverse.omega-gate.v1"


class GateDecision(str, Enum):
    ADMIT = "ADMIT"
    HOLD = "HOLD"
    BLOCK = "BLOCK"


def _require_sha256(value: str, *, field: str) -> str:
    normalized = str(value).strip().lower()
    if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return normalized


@dataclass(frozen=True)
class InvariantCheck:
    invariant_id: str
    passed: bool | None
    evidence_ref: str
    observed: Any | None = None
    expected: Any | None = None

    def __post_init__(self) -> None:
        if not self.invariant_id.strip():
            raise ValueError("invariant_id is required")
        if not self.evidence_ref.strip():
            raise ValueError("every invariant check requires evidence_ref")


@dataclass(frozen=True)
class AdmissionRequest:
    proposal_id: str
    source_mnb: str
    target_state_hash: str
    principal: str
    requested_action: str
    context_hash: str
    invariants: tuple[InvariantCheck, ...]
    requires_authorization: bool = False
    authorization_ref: str | None = None

    def __post_init__(self) -> None:
        if not self.proposal_id.strip() or not self.source_mnb.strip() or not self.principal.strip():
            raise ValueError("proposal_id, source_mnb and principal are required")
        if not self.requested_action.strip():
            raise ValueError("requested_action is required")
        _require_sha256(self.target_state_hash, field="target_state_hash")
        _require_sha256(self.context_hash, field="context_hash")
        if self.authorization_ref is not None and not self.authorization_ref.strip():
            raise ValueError("authorization_ref must be non-empty when supplied")


@dataclass(frozen=True)
class AdmissionDecision:
    schema: str
    decision_id: str
    proposal_id: str
    status: GateDecision
    reasons: tuple[str, ...]
    evaluated_invariants: tuple[str, ...]
    receipt: dict[str, Any]


class OmegaGateContract:
    """Deterministic admission contract for the Ω-Gate API surface.

    The gate does not invent evidence or thresholds. It consumes already-evaluated
    invariant checks. Any failed check blocks, unresolved evidence holds, and
    required authorization missing from the request holds. Only a fully resolved
    request with all checks passing can be admitted.
    """

    @staticmethod
    def evaluate(request: AdmissionRequest) -> AdmissionDecision:
        reasons: list[str] = []
        status = GateDecision.ADMIT

        if not request.invariants:
            status = GateDecision.HOLD
            reasons.append("NO_INVARIANT_EVIDENCE")
        else:
            failed = [item.invariant_id for item in request.invariants if item.passed is False]
            unresolved = [item.invariant_id for item in request.invariants if item.passed is None]
            if failed:
                status = GateDecision.BLOCK
                reasons.extend(f"INVARIANT_FAILED:{item}" for item in failed)
            elif unresolved:
                status = GateDecision.HOLD
                reasons.extend(f"INVARIANT_UNRESOLVED:{item}" for item in unresolved)

        if request.requires_authorization and not request.authorization_ref:
            if status is not GateDecision.BLOCK:
                status = GateDecision.HOLD
            reasons.append("AUTHORIZATION_REQUIRED")

        core = {
            "schema": SCHEMA,
            "proposal_id": request.proposal_id,
            "source_mnb": request.source_mnb,
            "target_state_hash": request.target_state_hash,
            "principal": request.principal,
            "requested_action": request.requested_action,
            "context_hash": request.context_hash,
            "requires_authorization": request.requires_authorization,
            "authorization_ref": request.authorization_ref,
            "invariants": [asdict(item) for item in request.invariants],
            "status": status.value,
            "reasons": reasons,
        }
        decision_id = f"omega-{sha256_text(canonical_json(core))[:24]}"
        receipt = evidence_receipt(
            "omega_gate.admission",
            {
                "proposal_id": request.proposal_id,
                "target_state_hash": request.target_state_hash,
                "context_hash": request.context_hash,
            },
            {**core, "decision_id": decision_id},
        )
        return AdmissionDecision(
            schema=SCHEMA,
            decision_id=decision_id,
            proposal_id=request.proposal_id,
            status=status,
            reasons=tuple(reasons),
            evaluated_invariants=tuple(item.invariant_id for item in request.invariants),
            receipt=receipt,
        )


def build_request(
    *,
    proposal_id: str,
    source_mnb: str,
    target_state_hash: str,
    principal: str,
    requested_action: str,
    context_hash: str,
    invariants: Iterable[InvariantCheck],
    requires_authorization: bool = False,
    authorization_ref: str | None = None,
) -> AdmissionRequest:
    return AdmissionRequest(
        proposal_id=proposal_id,
        source_mnb=source_mnb,
        target_state_hash=target_state_hash,
        principal=principal,
        requested_action=requested_action,
        context_hash=context_hash,
        invariants=tuple(invariants),
        requires_authorization=requires_authorization,
        authorization_ref=authorization_ref,
    )
