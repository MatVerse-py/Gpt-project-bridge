from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Callable, Iterable

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
        invariant_id = self.invariant_id.strip()
        evidence_ref = self.evidence_ref.strip()
        if not invariant_id:
            raise ValueError("invariant_id is required")
        if self.passed is not None and not isinstance(self.passed, bool):
            raise TypeError("passed must be bool or None")
        if not evidence_ref:
            raise ValueError("every invariant check requires evidence_ref")
        object.__setattr__(self, "invariant_id", invariant_id)
        object.__setattr__(self, "evidence_ref", evidence_ref)


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
        proposal_id = self.proposal_id.strip()
        source_mnb = self.source_mnb.strip()
        principal = self.principal.strip()
        requested_action = self.requested_action.strip()
        if not proposal_id or not source_mnb or not principal:
            raise ValueError("proposal_id, source_mnb and principal are required")
        if not requested_action:
            raise ValueError("requested_action is required")
        object.__setattr__(self, "proposal_id", proposal_id)
        object.__setattr__(self, "source_mnb", source_mnb)
        object.__setattr__(self, "principal", principal)
        object.__setattr__(self, "requested_action", requested_action)
        object.__setattr__(
            self,
            "target_state_hash",
            _require_sha256(self.target_state_hash, field="target_state_hash"),
        )
        object.__setattr__(
            self,
            "context_hash",
            _require_sha256(self.context_hash, field="context_hash"),
        )
        if self.authorization_ref is not None:
            normalized_ref = self.authorization_ref.strip()
            if not normalized_ref:
                raise ValueError("authorization_ref must be non-empty when supplied")
            object.__setattr__(self, "authorization_ref", normalized_ref)


@dataclass(frozen=True)
class AuthorizationRecord:
    """Trusted authorization material resolved outside the request payload.

    A bare authorization reference is never sufficient. The resolved record must
    be active and scoped to the exact principal, requested action and target state.
    """

    authorization_ref: str
    principal: str
    requested_action: str
    target_state_hash: str
    evidence_ref: str
    active: bool = True

    def __post_init__(self) -> None:
        authorization_ref = self.authorization_ref.strip()
        principal = self.principal.strip()
        requested_action = self.requested_action.strip()
        evidence_ref = self.evidence_ref.strip()
        if not authorization_ref or not principal or not requested_action or not evidence_ref:
            raise ValueError(
                "authorization_ref, principal, requested_action and evidence_ref are required"
            )
        object.__setattr__(self, "authorization_ref", authorization_ref)
        object.__setattr__(self, "principal", principal)
        object.__setattr__(self, "requested_action", requested_action)
        object.__setattr__(self, "evidence_ref", evidence_ref)
        object.__setattr__(
            self,
            "target_state_hash",
            _require_sha256(self.target_state_hash, field="authorization target_state_hash"),
        )


AuthorizationResolver = Callable[[str], AuthorizationRecord | None]


@dataclass(frozen=True)
class AdmissionDecision:
    schema: str
    decision_id: str
    proposal_id: str
    status: GateDecision
    reasons: tuple[str, ...]
    evaluated_invariants: tuple[str, ...]
    resolved_authorization: dict[str, Any] | None
    receipt: dict[str, Any]


class OmegaGateContract:
    """Deterministic, fail-closed admission contract for the Ω-Gate API surface.

    The gate consumes already-evaluated invariant evidence. Any failed invariant
    blocks; unresolved evidence holds. When authorization is required, a bare
    string reference never grants authority: a trusted resolver must return an
    active record bound to the exact principal, requested action and target state.
    """

    @staticmethod
    def evaluate(
        request: AdmissionRequest,
        *,
        authorization_resolver: AuthorizationResolver | None = None,
    ) -> AdmissionDecision:
        reasons: list[str] = []
        status = GateDecision.ADMIT
        resolved_authorization: AuthorizationRecord | None = None

        if not request.invariants:
            status = GateDecision.HOLD
            reasons.append("NO_INVARIANT_EVIDENCE")
        else:
            failed = [item.invariant_id for item in request.invariants if item.passed is False]
            unresolved = [item.invariant_id for item in request.invariants if item.passed is None]
            if failed:
                status = GateDecision.BLOCK
                reasons.extend(f"INVARIANT_FAILED:{item}" for item in failed)
            if unresolved:
                if status is not GateDecision.BLOCK:
                    status = GateDecision.HOLD
                reasons.extend(f"INVARIANT_UNRESOLVED:{item}" for item in unresolved)

        if request.requires_authorization:
            if not request.authorization_ref:
                if status is not GateDecision.BLOCK:
                    status = GateDecision.HOLD
                reasons.append("AUTHORIZATION_REQUIRED")
            elif authorization_resolver is None:
                if status is not GateDecision.BLOCK:
                    status = GateDecision.HOLD
                reasons.append("AUTHORIZATION_UNRESOLVED")
            else:
                resolved_authorization = authorization_resolver(request.authorization_ref)
                if resolved_authorization is None:
                    if status is not GateDecision.BLOCK:
                        status = GateDecision.HOLD
                    reasons.append("AUTHORIZATION_UNRESOLVED")
                elif resolved_authorization.authorization_ref != request.authorization_ref:
                    status = GateDecision.BLOCK
                    reasons.append("AUTHORIZATION_REFERENCE_MISMATCH")
                elif not resolved_authorization.active:
                    status = GateDecision.BLOCK
                    reasons.append("AUTHORIZATION_INACTIVE")
                else:
                    mismatches: list[str] = []
                    if resolved_authorization.principal != request.principal:
                        mismatches.append("principal")
                    if resolved_authorization.requested_action != request.requested_action:
                        mismatches.append("requested_action")
                    if resolved_authorization.target_state_hash != request.target_state_hash:
                        mismatches.append("target_state_hash")
                    if mismatches:
                        status = GateDecision.BLOCK
                        reasons.append("AUTHORIZATION_SCOPE_MISMATCH:" + ",".join(mismatches))

        authorization_payload = (
            asdict(resolved_authorization) if resolved_authorization is not None else None
        )
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
            "resolved_authorization": authorization_payload,
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
            resolved_authorization=authorization_payload,
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
