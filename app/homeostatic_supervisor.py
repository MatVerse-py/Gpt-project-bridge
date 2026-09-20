from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from .core import Decision, stable_hash
from .evidence import canonical_json, evidence_receipt
from .physiology_effect_feedback import (
    ClosedLoopCycleResult,
    ClosedLoopPhysiologyEngine,
    FeedbackSnapshot,
)

SCHEMA_VERSION = "matverse.bounded-autonomous-recovery.v1"
_RESERVED_POLICY_KEYS = {"action", "recovery_for"}


@dataclass(frozen=True)
class BoundedRecoveryPolicy:
    """Predeclared recovery policy that cannot authorize or execute by itself."""

    action: str
    parameters_json: str

    @classmethod
    def build(
        cls,
        *,
        action: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> "BoundedRecoveryPolicy":
        if not isinstance(action, str) or not action.strip():
            raise ValueError("action must be a non-empty string")
        params = {} if parameters is None else json.loads(canonical_json(dict(parameters)))
        overlap = _RESERVED_POLICY_KEYS.intersection(params)
        if overlap:
            raise ValueError(f"reserved recovery policy keys: {sorted(overlap)}")
        return cls(action=action.strip(), parameters_json=canonical_json(params))

    @property
    def fingerprint(self) -> str:
        return stable_hash(
            {
                "schema": SCHEMA_VERSION,
                "action": self.action,
                "parameters": json.loads(self.parameters_json),
            }
        )

    def proposal_for(self, feedback: FeedbackSnapshot) -> dict[str, Any]:
        if not feedback.recovery_required or feedback.recovery_for_cycle_id is None:
            raise ValueError("recovery policy may run only while recovery is required")
        proposal = json.loads(self.parameters_json)
        proposal["action"] = self.action
        proposal["recovery_for"] = feedback.recovery_for_cycle_id
        return proposal


@dataclass(frozen=True)
class SupervisorStepResult:
    cycle: ClosedLoopCycleResult
    autonomous_recovery_attempted: bool
    recovery_origin_cycle_id: str | None
    policy_fingerprint: str | None
    proposal_receipt_hash: str | None
    outcome_receipt_hash: str | None


class BoundedHomeostaticSupervisor:
    """Policy-bound recovery supervisor above the closed-loop physiology engine.

    The supervisor has no independent authorization path. It can only materialize a
    predeclared recovery proposal after authenticated feedback opens the recovery
    gate. The proposal is then evaluated by the ordinary GovernedOrganism/HDB/Ω
    path. BLOCK/HOLD decisions therefore keep recovery open.
    """

    def __init__(
        self,
        *,
        engine: ClosedLoopPhysiologyEngine,
        recovery_policy: BoundedRecoveryPolicy,
    ) -> None:
        self.engine = engine
        self.recovery_policy = recovery_policy

    def step(
        self,
        *,
        proposal: Mapping[str, Any] | None = None,
        human: Mapping[str, Any] | None = None,
        ontology_ok: bool = True,
        signature_valid: bool = True,
        transition_valid: bool = True,
    ) -> SupervisorStepResult:
        feedback = self.engine.feedback
        if not feedback.recovery_required:
            cycle = self.engine.tick(
                proposal=proposal,
                human=human,
                ontology_ok=ontology_ok,
                signature_valid=signature_valid,
                transition_valid=transition_valid,
            )
            return SupervisorStepResult(
                cycle=cycle,
                autonomous_recovery_attempted=False,
                recovery_origin_cycle_id=None,
                policy_fingerprint=None,
                proposal_receipt_hash=None,
                outcome_receipt_hash=None,
            )

        if proposal is not None:
            raise ValueError(
                "external proposal is not accepted while bounded autonomous recovery is pending"
            )

        origin = feedback.recovery_for_cycle_id
        if origin is None:
            raise RuntimeError("recovery_required without recovery origin")

        recovery_proposal = self.recovery_policy.proposal_for(feedback)
        gate_inputs = {
            "ontology_ok": bool(ontology_ok),
            "signature_valid": bool(signature_valid),
            "transition_valid": bool(transition_valid),
        }
        proposal_inputs = {
            "schema": SCHEMA_VERSION,
            "organism_id": self.engine.organism.organism_id,
            "recovery_origin_cycle_id": origin,
            "source_feedback_receipt_hash": feedback.last_feedback_receipt_hash,
            "policy_fingerprint": self.recovery_policy.fingerprint,
            "gate_inputs": gate_inputs,
        }
        proposal_outputs = {"proposal": recovery_proposal}
        proposal_receipt = evidence_receipt(
            "BOUNDED_AUTONOMOUS_RECOVERY_PROPOSAL",
            proposal_inputs,
            proposal_outputs,
        )
        attempt_id = stable_hash(
            {
                **proposal_inputs,
                "proposal_receipt_hash": proposal_receipt["receipt_hash"],
            }
        )
        proposal_event = self.engine.journal.append(
            event_id=f"{origin}:autonomous-recovery:{attempt_id[:24]}",
            topic="physiology",
            event_type="BOUNDED_AUTONOMOUS_RECOVERY_PROPOSED",
            payload={
                "inputs": proposal_inputs,
                "outputs": proposal_outputs,
                "receipt": proposal_receipt,
            },
            causation_id=f"{origin}:effect-feedback",
            correlation_id=origin,
        )

        cycle = self.engine.tick(
            proposal=recovery_proposal,
            human=human,
            ontology_ok=ontology_ok,
            signature_valid=signature_valid,
            transition_valid=transition_valid,
        )

        outcome_outputs = {
            "effective_decision": None
            if cycle.effective_decision is None
            else cycle.effective_decision.value,
            "executed": bool(cycle.cycle.executed),
            "post_effect_health": None
            if cycle.post_effect_health is None
            else cycle.post_effect_health.value,
            "recovery_required": bool(cycle.recovery_required),
            "recovery_cleared": not cycle.recovery_required,
            "feedback_receipt_hash": cycle.feedback_receipt_hash,
            "closed_loop_state_root": cycle.closed_loop_state_root,
        }
        outcome_receipt = evidence_receipt(
            "BOUNDED_AUTONOMOUS_RECOVERY_OUTCOME",
            {
                "schema": SCHEMA_VERSION,
                "proposal_receipt_hash": proposal_receipt["receipt_hash"],
                "recovery_origin_cycle_id": origin,
                "recovery_cycle_id": cycle.cycle.cycle_id,
            },
            outcome_outputs,
        )
        self.engine.journal.append(
            event_id=f"{proposal_event.event_id}:outcome",
            topic="physiology",
            event_type="BOUNDED_AUTONOMOUS_RECOVERY_OUTCOME",
            payload={"outputs": outcome_outputs, "receipt": outcome_receipt},
            causation_id=proposal_event.event_id,
            correlation_id=origin,
        )

        return SupervisorStepResult(
            cycle=cycle,
            autonomous_recovery_attempted=True,
            recovery_origin_cycle_id=origin,
            policy_fingerprint=self.recovery_policy.fingerprint,
            proposal_receipt_hash=proposal_receipt["receipt_hash"],
            outcome_receipt_hash=outcome_receipt["receipt_hash"],
        )
