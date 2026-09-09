from __future__ import annotations

import hashlib
import hmac
import json
import re
import threading
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from .core import Decision, stable_hash
from .evidence import canonical_json, evidence_receipt
from .organism_loop import GovernedOrganism
from .physiology import (
    CycleResult,
    DurableEventJournal,
    ExecutionResult,
    Executor,
    HealthState,
    HomeostaticPolicy,
    NativeTelemetry,
    PhysiologyEngine,
)

SCHEMA_VERSION = "matverse.physiology-effect-feedback.v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class EffectFeedbackPolicy:
    """Policy for closing action effects back into the next physiology cycle.

    `success_statuses` are transport/application acknowledgements only. A cycle is
    considered recovered only when the executor reports success *and* a fresh
    post-effect homeostatic measurement is NORMAL.
    """

    success_statuses: tuple[str, ...] = ("OK", "PASS", "SUCCESS")
    failures_before_recovery_gate: int = 1

    def __post_init__(self) -> None:
        normalized = tuple(
            item.strip().upper()
            for item in self.success_statuses
            if isinstance(item, str) and item.strip()
        )
        if not normalized:
            raise ValueError("success_statuses must contain at least one non-empty status")
        if len(normalized) != len(set(normalized)):
            raise ValueError("success_statuses must be unique case-insensitively")
        if self.failures_before_recovery_gate < 1:
            raise ValueError("failures_before_recovery_gate must be >= 1")

    def accepts(self, status: str | None) -> bool:
        if not isinstance(status, str) or not status.strip():
            return False
        allowed = {item.strip().upper() for item in self.success_statuses}
        return status.strip().upper() in allowed


@dataclass(frozen=True)
class FeedbackSnapshot:
    failure_streak: int
    recovery_required: bool
    recovery_for_cycle_id: str | None
    last_effect_status: str | None
    last_post_effect_health: HealthState | None
    last_feedback_receipt_hash: str | None


@dataclass(frozen=True)
class ClosedLoopCycleResult:
    cycle: CycleResult
    effective_decision: Decision | None
    post_effect_health: HealthState | None
    effect_status: str | None
    recovery_required: bool
    recovery_for_cycle_id: str | None
    feedback_receipt_hash: str | None
    closed_loop_state_root: str


class ClosedLoopPhysiologyEngine:
    """Adds causal post-effect feedback to the existing PhysiologyEngine.

    The existing engine already provides:
        Sense -> Analyze -> Plan -> Authorize -> Execute -> Effect -> Memory.

    This wrapper adds the missing causal edge:
        Effect -> fresh measurement -> authenticated feedback state -> next-cycle gate.

    A failed or homeostatically harmful effect is therefore not only recorded;
    it changes which proposals can execute on subsequent cycles. Recovery is
    fail-closed and must reference the cycle that opened the recovery gate.

    `tick` is serialized per engine instance. This is intentional: the physiology
    state and the captured executor outcome form one causal transaction and must
    not be interleaved across concurrent callers.
    """

    def __init__(
        self,
        *,
        organism: GovernedOrganism,
        journal: DurableEventJournal,
        telemetry: NativeTelemetry,
        executor: Executor | None,
        feedback_state_secret: str,
        homeostatic_policy: HomeostaticPolicy | None = None,
        feedback_policy: EffectFeedbackPolicy | None = None,
    ) -> None:
        if not isinstance(feedback_state_secret, str) or not feedback_state_secret:
            raise ValueError("feedback_state_secret must be a non-empty string")
        self.organism = organism
        self.journal = journal
        self.telemetry = telemetry
        self.feedback_policy = feedback_policy or EffectFeedbackPolicy()
        self._executor = executor
        self._feedback_state_secret = feedback_state_secret
        self._captured_result: ExecutionResult | None = None
        self._captured_error: dict[str, str] | None = None
        self._state_key = f"physiology-effect-feedback:{self.organism.organism_id}"
        self._tick_lock = threading.RLock()

        stored_state = self.journal.get_state(self._state_key, None)
        if stored_state is None:
            self._feedback_state = self._empty_state()
        else:
            self._feedback_state = self._load_persisted_state(stored_state)

        wrapped_executor: Executor | None = None if executor is None else self._execute_and_capture
        self.engine = PhysiologyEngine(
            organism=organism,
            journal=journal,
            telemetry=telemetry,
            policy=homeostatic_policy,
            executor=wrapped_executor,
        )

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {
            "schema": SCHEMA_VERSION,
            "failure_streak": 0,
            "recovery_required": False,
            "recovery_for_cycle_id": None,
            "last_effect_status": None,
            "last_post_effect_health": None,
            "last_feedback_receipt_hash": None,
        }

    @staticmethod
    def _validate_state(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("invalid effect feedback state")
        if value.get("schema", SCHEMA_VERSION) != SCHEMA_VERSION:
            raise ValueError("unsupported effect feedback schema")
        failure_streak = int(value.get("failure_streak", 0))
        if failure_streak < 0:
            raise ValueError("failure_streak must be >= 0")
        recovery_required = bool(value.get("recovery_required", False))
        recovery_for = value.get("recovery_for_cycle_id")
        if recovery_for is not None and (
            not isinstance(recovery_for, str) or not recovery_for
        ):
            raise ValueError("recovery_for_cycle_id must be a non-empty string or null")
        if recovery_required != (recovery_for is not None):
            raise ValueError("recovery_required and recovery_for_cycle_id are inconsistent")
        last_status = value.get("last_effect_status")
        if last_status is not None and not isinstance(last_status, str):
            raise ValueError("last_effect_status must be a string or null")
        raw_health = value.get("last_post_effect_health")
        if raw_health is not None:
            try:
                raw_health = HealthState(str(raw_health)).value
            except ValueError as exc:
                raise ValueError("invalid last_post_effect_health") from exc
        receipt_hash = value.get("last_feedback_receipt_hash")
        if receipt_hash is not None and (
            not isinstance(receipt_hash, str)
            or _SHA256_RE.fullmatch(receipt_hash) is None
        ):
            raise ValueError("last_feedback_receipt_hash must be a SHA-256 hex string or null")
        return {
            "schema": SCHEMA_VERSION,
            "failure_streak": failure_streak,
            "recovery_required": recovery_required,
            "recovery_for_cycle_id": recovery_for,
            "last_effect_status": last_status,
            "last_post_effect_health": raw_health,
            "last_feedback_receipt_hash": receipt_hash,
        }

    def _state_mac(self, payload: Mapping[str, Any]) -> str:
        return hmac.new(
            self._feedback_state_secret.encode("utf-8"),
            canonical_json(dict(payload)).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _load_persisted_state(self, stored: Any) -> dict[str, Any]:
        if not isinstance(stored, dict) or set(stored) != {"payload", "state_mac"}:
            raise ValueError("invalid persisted effect feedback envelope")
        payload = stored.get("payload")
        supplied_mac = stored.get("state_mac")
        if not isinstance(payload, dict) or not isinstance(supplied_mac, str):
            raise ValueError("invalid persisted effect feedback envelope")
        expected_mac = self._state_mac(payload)
        if not hmac.compare_digest(expected_mac, supplied_mac):
            raise ValueError("effect feedback state authentication failed")
        return self._validate_state(payload)

    def _persist_state(self) -> None:
        payload = json.loads(canonical_json(self._feedback_state))
        self.journal.set_state(
            self._state_key,
            {"payload": payload, "state_mac": self._state_mac(payload)},
        )

    @property
    def feedback(self) -> FeedbackSnapshot:
        raw_health = self._feedback_state["last_post_effect_health"]
        return FeedbackSnapshot(
            failure_streak=int(self._feedback_state["failure_streak"]),
            recovery_required=bool(self._feedback_state["recovery_required"]),
            recovery_for_cycle_id=self._feedback_state["recovery_for_cycle_id"],
            last_effect_status=self._feedback_state["last_effect_status"],
            last_post_effect_health=None
            if raw_health is None
            else HealthState(raw_health),
            last_feedback_receipt_hash=self._feedback_state["last_feedback_receipt_hash"],
        )

    def _execute_and_capture(self, proposal: Mapping[str, Any]) -> ExecutionResult:
        if self._executor is None:
            raise RuntimeError("explicit executor is required")
        try:
            result = self._executor(proposal)
        except Exception as exc:
            self._captured_result = None
            self._captured_error = {
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            }
            raise
        if not isinstance(result, ExecutionResult):
            self._captured_result = None
            self._captured_error = {
                "error_type": "TypeError",
                "error_message": "executor must return ExecutionResult",
            }
            raise TypeError("executor must return ExecutionResult")
        self._captured_result = result
        self._captured_error = None
        return result

    def _closed_loop_root(self, cycle: CycleResult) -> str:
        return stable_hash(
            {
                "schema": SCHEMA_VERSION,
                "organism_state_root": cycle.state_root,
                "feedback_state": self._feedback_state,
            }
        )

    def _recovery_gate(
        self, proposal: Mapping[str, Any] | None
    ) -> tuple[bool, str | None]:
        recovery_for = self._feedback_state["recovery_for_cycle_id"]
        if recovery_for is None or proposal is None:
            return True, None
        proposal_recovery_for = proposal.get("recovery_for")
        if proposal_recovery_for == recovery_for:
            return True, None
        return False, f"effect recovery required for cycle {recovery_for}"

    def _record_gate_hold(self, cycle: CycleResult, reason: str) -> str:
        receipt = evidence_receipt(
            "PHYSIOLOGY_EFFECT_RECOVERY_GATE",
            {
                "schema": SCHEMA_VERSION,
                "cycle_id": cycle.cycle_id,
                "recovery_for_cycle_id": self._feedback_state["recovery_for_cycle_id"],
            },
            {"decision": Decision.HOLD.value, "reason": reason},
        )
        self.journal.append(
            event_id=f"{cycle.cycle_id}:effect-recovery-gate",
            topic="physiology",
            event_type="EFFECT_RECOVERY_GATE",
            payload={"decision": Decision.HOLD.value, "reason": reason, "receipt": receipt},
            causation_id=f"{cycle.cycle_id}:memory",
            correlation_id=cycle.cycle_id,
        )
        return receipt["receipt_hash"]

    def _record_effect_feedback(
        self, cycle: CycleResult, *, was_recovery_attempt: bool
    ) -> tuple[HealthState | None, str, str]:
        if self._captured_result is not None:
            effect_status = str(self._captured_result.status)
        elif self._captured_error is not None:
            effect_status = "ERROR"
        else:
            effect_status = "UNKNOWN"

        post_sample = None
        post_assessment = None
        measurement_error: dict[str, str] | None = None
        try:
            post_sample = self.telemetry.sample()
            post_assessment = self.engine.controller.assess(
                post_sample,
                journal_ok=self.journal.integrity_check(),
            )
        except Exception as exc:
            measurement_error = {
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            }

        transport_success = self.feedback_policy.accepts(effect_status)
        homeostasis_success = (
            post_assessment is not None
            and post_assessment.state is HealthState.NORMAL
        )
        effect_success = (
            transport_success
            and homeostasis_success
            and measurement_error is None
        )

        if effect_success:
            self._feedback_state["failure_streak"] = 0
            self._feedback_state["recovery_required"] = False
            self._feedback_state["recovery_for_cycle_id"] = None
        else:
            self._feedback_state["failure_streak"] = (
                int(self._feedback_state["failure_streak"]) + 1
            )
            if (
                int(self._feedback_state["failure_streak"])
                >= self.feedback_policy.failures_before_recovery_gate
            ):
                if self._feedback_state["recovery_for_cycle_id"] is None:
                    self._feedback_state["recovery_for_cycle_id"] = cycle.cycle_id
                self._feedback_state["recovery_required"] = True

        post_health = None if post_assessment is None else post_assessment.state
        self._feedback_state["last_effect_status"] = effect_status
        self._feedback_state["last_post_effect_health"] = (
            None if post_health is None else post_health.value
        )

        feedback_inputs = {
            "schema": SCHEMA_VERSION,
            "cycle_id": cycle.cycle_id,
            "cycle_receipt_hash": cycle.receipt_hash,
            "was_recovery_attempt": bool(was_recovery_attempt),
            "effect_status": effect_status,
            "executor_error": self._captured_error,
            "measurement_error": measurement_error,
            "post_effect_telemetry": None
            if post_sample is None
            else asdict(post_sample),
            "post_effect_health": None
            if post_health is None
            else post_health.value,
            "post_effect_reasons": []
            if post_assessment is None
            else list(post_assessment.reasons),
        }
        feedback_outputs = {
            "effect_success": effect_success,
            "failure_streak": self._feedback_state["failure_streak"],
            "recovery_required": self._feedback_state["recovery_required"],
            "recovery_for_cycle_id": self._feedback_state["recovery_for_cycle_id"],
        }
        receipt = evidence_receipt(
            "PHYSIOLOGY_EFFECT_FEEDBACK",
            feedback_inputs,
            feedback_outputs,
        )
        self._feedback_state["last_feedback_receipt_hash"] = receipt["receipt_hash"]
        self._persist_state()
        self.journal.append(
            event_id=f"{cycle.cycle_id}:effect-feedback",
            topic="physiology",
            event_type="EFFECT_FEEDBACK",
            payload={
                "inputs": json.loads(canonical_json(feedback_inputs)),
                "outputs": json.loads(canonical_json(feedback_outputs)),
                "receipt": receipt,
            },
            causation_id=f"{cycle.cycle_id}:effect",
            correlation_id=cycle.cycle_id,
        )
        return post_health, effect_status, receipt["receipt_hash"]

    def tick(
        self,
        *,
        proposal: Mapping[str, Any] | None = None,
        human: Mapping[str, Any] | None = None,
        ontology_ok: bool = True,
        signature_valid: bool = True,
        transition_valid: bool = True,
    ) -> ClosedLoopCycleResult:
        with self._tick_lock:
            return self._tick_locked(
                proposal=proposal,
                human=human,
                ontology_ok=ontology_ok,
                signature_valid=signature_valid,
                transition_valid=transition_valid,
            )

    def _tick_locked(
        self,
        *,
        proposal: Mapping[str, Any] | None,
        human: Mapping[str, Any] | None,
        ontology_ok: bool,
        signature_valid: bool,
        transition_valid: bool,
    ) -> ClosedLoopCycleResult:
        proposal_copy = (
            None
            if proposal is None
            else json.loads(canonical_json(dict(proposal)))
        )
        allowed, gate_reason = self._recovery_gate(proposal_copy)
        was_recovery_attempt = bool(
            proposal_copy is not None
            and self._feedback_state["recovery_for_cycle_id"] is not None
            and proposal_copy.get("recovery_for")
            == self._feedback_state["recovery_for_cycle_id"]
        )

        self._captured_result = None
        self._captured_error = None

        if not allowed:
            cycle = self.engine.tick(
                proposal=None,
                human=human,
                ontology_ok=ontology_ok,
                signature_valid=signature_valid,
                transition_valid=transition_valid,
            )
            assert gate_reason is not None
            gate_receipt = self._record_gate_hold(cycle, gate_reason)
            return ClosedLoopCycleResult(
                cycle=cycle,
                effective_decision=Decision.HOLD,
                post_effect_health=None,
                effect_status=None,
                recovery_required=True,
                recovery_for_cycle_id=self._feedback_state["recovery_for_cycle_id"],
                feedback_receipt_hash=gate_receipt,
                closed_loop_state_root=self._closed_loop_root(cycle),
            )

        cycle = self.engine.tick(
            proposal=proposal_copy,
            human=human,
            ontology_ok=ontology_ok,
            signature_valid=signature_valid,
            transition_valid=transition_valid,
        )

        post_health: HealthState | None = None
        effect_status: str | None = None
        feedback_receipt: str | None = None
        if cycle.decision is Decision.PASS:
            post_health, effect_status, feedback_receipt = self._record_effect_feedback(
                cycle,
                was_recovery_attempt=was_recovery_attempt,
            )

        return ClosedLoopCycleResult(
            cycle=cycle,
            effective_decision=cycle.decision,
            post_effect_health=post_health,
            effect_status=effect_status,
            recovery_required=bool(self._feedback_state["recovery_required"]),
            recovery_for_cycle_id=self._feedback_state["recovery_for_cycle_id"],
            feedback_receipt_hash=feedback_receipt,
            closed_loop_state_root=self._closed_loop_root(cycle),
        )
