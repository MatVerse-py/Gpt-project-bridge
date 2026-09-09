from __future__ import annotations

from app.core import Decision
from app.deterministic_lab import DeterministicFaultPlan, DeterministicTelemetry, FaultDirective, FaultKind
from app.organism_loop import GovernedOrganism
from app.physiology import DurableEventJournal, ExecutionResult, HealthState
from app.physiology_effect_feedback import ClosedLoopPhysiologyEngine, EffectFeedbackPolicy


def _organism(runtime_id: str = "feedback-test-runtime") -> GovernedOrganism:
    return GovernedOrganism(
        organism_id="feedback-test-organism",
        frozen_contract_hash="b" * 64,
        runtime_id=runtime_id,
        state_secret="feedback-state-secret",
        authority_secrets={"independent-authorizer": "feedback-authority-secret"},
    )


def _normal_telemetry(cycles: int = 20) -> DeterministicTelemetry:
    return DeterministicTelemetry(plan=DeterministicFaultPlan(seed=1, cycles=cycles, directives=()))


def test_successful_effect_closes_loop_without_recovery_gate(tmp_path):
    journal = DurableEventJournal(tmp_path / "success.sqlite3")
    engine = ClosedLoopPhysiologyEngine(
        organism=_organism(),
        journal=journal,
        telemetry=_normal_telemetry(),
        executor=lambda proposal: ExecutionResult(status="OK", effect={"resource": proposal.get("resource")}),
    )

    result = engine.tick(proposal={"action": "READ", "resource": "sensor"})

    assert result.effective_decision is Decision.PASS
    assert result.cycle.executed is True
    assert result.effect_status == "OK"
    assert result.post_effect_health is HealthState.NORMAL
    assert result.recovery_required is False
    assert len(result.closed_loop_state_root) == 64

    feedback_event = next(event for event in journal.read(limit=100) if event.event_type == "EFFECT_FEEDBACK")
    assert feedback_event.causation_id == f"{result.cycle.cycle_id}:effect"
    assert feedback_event.correlation_id == result.cycle.cycle_id
    assert feedback_event.payload["outputs"]["effect_success"] is True
    journal.close()


def test_post_effect_homeostatic_degradation_changes_next_cycle(tmp_path):
    plan = DeterministicFaultPlan(
        seed=2,
        cycles=8,
        directives=(FaultDirective(cycle_seq=2, kind=FaultKind.DEGRADED_DISK),),
    )
    telemetry = DeterministicTelemetry(plan=plan)
    journal = DurableEventJournal(tmp_path / "post-effect.sqlite3")
    executions: list[dict] = []

    def executor(proposal):
        executions.append(dict(proposal))
        return ExecutionResult(status="OK", effect={"done": True})

    engine = ClosedLoopPhysiologyEngine(
        organism=_organism(),
        journal=journal,
        telemetry=telemetry,
        executor=executor,
    )

    first = engine.tick(proposal={"action": "READ", "resource": "sensor"})
    assert first.cycle.health is HealthState.NORMAL
    assert first.effect_status == "OK"
    assert first.post_effect_health is HealthState.DEGRADED
    assert first.recovery_required is True
    assert first.recovery_for_cycle_id == first.cycle.cycle_id
    assert executions == [{"action": "READ", "resource": "sensor"}]

    blocked_by_feedback = engine.tick(proposal={"action": "READ", "resource": "other"})
    assert blocked_by_feedback.effective_decision is Decision.HOLD
    assert blocked_by_feedback.cycle.decision is None
    assert blocked_by_feedback.recovery_required is True
    assert executions == [{"action": "READ", "resource": "sensor"}]
    assert any(event.event_type == "EFFECT_RECOVERY_GATE" for event in journal.read(limit=100))
    journal.close()


def test_recovery_must_reference_origin_and_clear_only_after_measured_normal_effect(tmp_path):
    journal = DurableEventJournal(tmp_path / "recovery.sqlite3")
    calls: list[dict] = []
    call_count = 0

    def executor(proposal):
        nonlocal call_count
        call_count += 1
        calls.append(dict(proposal))
        if call_count == 1:
            return ExecutionResult(status="ERROR", effect={"reason": "synthetic failure"})
        return ExecutionResult(status="OK", effect={"recovered": True})

    engine = ClosedLoopPhysiologyEngine(
        organism=_organism(),
        journal=journal,
        telemetry=_normal_telemetry(),
        executor=executor,
    )

    failed = engine.tick(proposal={"action": "READ", "resource": "sensor"})
    assert failed.effect_status == "ERROR"
    assert failed.recovery_required is True
    origin = failed.recovery_for_cycle_id
    assert origin == failed.cycle.cycle_id

    wrong_reference = engine.tick(proposal={"action": "READ", "recovery_for": "wrong-cycle"})
    assert wrong_reference.effective_decision is Decision.HOLD
    assert call_count == 1

    recovered = engine.tick(proposal={"action": "READ", "resource": "sensor", "recovery_for": origin})
    assert recovered.effective_decision is Decision.PASS
    assert recovered.effect_status == "OK"
    assert recovered.post_effect_health is HealthState.NORMAL
    assert recovered.recovery_required is False
    assert recovered.recovery_for_cycle_id is None
    assert call_count == 2
    journal.close()


def test_executor_exception_becomes_causal_feedback_not_only_a_log(tmp_path):
    journal = DurableEventJournal(tmp_path / "exception.sqlite3")

    def executor(_proposal):
        raise RuntimeError("device disappeared")

    engine = ClosedLoopPhysiologyEngine(
        organism=_organism(),
        journal=journal,
        telemetry=_normal_telemetry(),
        executor=executor,
    )

    failed = engine.tick(proposal={"action": "READ", "resource": "usb-device"})
    assert failed.cycle.decision is Decision.PASS
    assert failed.cycle.executed is False
    assert failed.effect_status == "ERROR"
    assert failed.post_effect_health is HealthState.NORMAL
    assert failed.recovery_required is True
    assert engine.feedback.failure_streak == 1

    next_cycle = engine.tick(proposal={"action": "READ", "resource": "usb-device"})
    assert next_cycle.effective_decision is Decision.HOLD
    journal.close()


def test_feedback_state_survives_journal_reopen(tmp_path):
    path = tmp_path / "durable-feedback.sqlite3"
    first_journal = DurableEventJournal(path)
    first_engine = ClosedLoopPhysiologyEngine(
        organism=_organism(),
        journal=first_journal,
        telemetry=_normal_telemetry(),
        executor=lambda proposal: ExecutionResult(status="FAIL", effect={"proposal": dict(proposal)}),
    )
    failed = first_engine.tick(proposal={"action": "READ"})
    origin = failed.recovery_for_cycle_id
    assert origin is not None
    first_journal.close()

    second_journal = DurableEventJournal(path)
    second_engine = ClosedLoopPhysiologyEngine(
        organism=_organism(runtime_id="feedback-test-runtime-2"),
        journal=second_journal,
        telemetry=_normal_telemetry(),
        executor=lambda proposal: ExecutionResult(status="OK", effect={"proposal": dict(proposal)}),
    )
    assert second_engine.feedback.recovery_required is True
    assert second_engine.feedback.recovery_for_cycle_id == origin

    held = second_engine.tick(proposal={"action": "READ"})
    assert held.effective_decision is Decision.HOLD
    second_journal.close()


def test_feedback_policy_can_require_multiple_failures_before_gating(tmp_path):
    journal = DurableEventJournal(tmp_path / "threshold.sqlite3")
    engine = ClosedLoopPhysiologyEngine(
        organism=_organism(),
        journal=journal,
        telemetry=_normal_telemetry(),
        executor=lambda proposal: ExecutionResult(status="FAIL", effect={"proposal": dict(proposal)}),
        feedback_policy=EffectFeedbackPolicy(failures_before_recovery_gate=2),
    )

    first = engine.tick(proposal={"action": "READ", "n": 1})
    assert first.recovery_required is False
    assert engine.feedback.failure_streak == 1

    second = engine.tick(proposal={"action": "READ", "n": 2})
    assert second.recovery_required is True
    assert second.recovery_for_cycle_id == second.cycle.cycle_id
    assert engine.feedback.failure_streak == 2
    journal.close()
