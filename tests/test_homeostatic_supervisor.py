from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.core import Decision
from app.deterministic_lab import DeterministicFaultPlan, DeterministicTelemetry
from app.homeostatic_supervisor import (
    BoundedHomeostaticSupervisor,
    BoundedRecoveryPolicy,
)
from app.organism_loop import GovernedOrganism
from app.physiology import DurableEventJournal, ExecutionResult, HealthState
from app.physiology_effect_feedback import ClosedLoopPhysiologyEngine

FEEDBACK_SECRET = "bounded-autonomous-feedback-secret"


def _organism() -> GovernedOrganism:
    return GovernedOrganism(
        organism_id="bounded-autonomous-organism",
        frozen_contract_hash="c" * 64,
        runtime_id="bounded-autonomous-runtime",
        state_secret="bounded-autonomous-state-secret",
        authority_secrets={"independent-authorizer": "bounded-autonomous-authority"},
    )


def _telemetry() -> DeterministicTelemetry:
    return DeterministicTelemetry(
        plan=DeterministicFaultPlan(seed=7, cycles=20, directives=())
    )


def _supervisor(tmp_path, executor, *, action="READ", parameters=None):
    journal = DurableEventJournal(tmp_path / "bounded-autonomous.sqlite3")
    engine = ClosedLoopPhysiologyEngine(
        organism=_organism(),
        journal=journal,
        telemetry=_telemetry(),
        executor=executor,
        feedback_state_secret=FEEDBACK_SECRET,
    )
    policy = BoundedRecoveryPolicy.build(
        action=action,
        parameters={"resource": "sensor"} if parameters is None else parameters,
    )
    return journal, engine, BoundedHomeostaticSupervisor(
        engine=engine,
        recovery_policy=policy,
    )


def test_failure_triggers_policy_bound_recovery_without_second_external_proposal(tmp_path):
    calls: list[dict] = []

    def executor(proposal):
        calls.append(dict(proposal))
        if len(calls) == 1:
            return ExecutionResult(status="FAIL", effect={"reason": "transient"})
        return ExecutionResult(status="OK", effect={"recovered": True})

    journal, engine, supervisor = _supervisor(tmp_path, executor)

    failed = supervisor.step(proposal={"action": "READ", "resource": "sensor"})
    assert failed.autonomous_recovery_attempted is False
    assert failed.cycle.effective_decision is Decision.PASS
    assert failed.cycle.effect_status == "FAIL"
    assert failed.cycle.recovery_required is True
    origin = failed.cycle.recovery_for_cycle_id
    assert origin is not None

    recovered = supervisor.step()
    assert recovered.autonomous_recovery_attempted is True
    assert recovered.recovery_origin_cycle_id == origin
    assert recovered.cycle.effective_decision is Decision.PASS
    assert recovered.cycle.cycle.executed is True
    assert recovered.cycle.effect_status == "OK"
    assert recovered.cycle.post_effect_health is HealthState.NORMAL
    assert recovered.cycle.recovery_required is False
    assert len(calls) == 2
    assert calls[1]["recovery_for"] == origin
    assert calls[1]["action"] == "READ"

    events = journal.read(limit=200)
    proposed = next(
        event
        for event in events
        if event.event_type == "BOUNDED_AUTONOMOUS_RECOVERY_PROPOSED"
    )
    outcome = next(
        event
        for event in events
        if event.event_type == "BOUNDED_AUTONOMOUS_RECOVERY_OUTCOME"
    )
    assert proposed.causation_id == f"{origin}:effect-feedback"
    assert outcome.causation_id == proposed.event_id
    assert outcome.payload["outputs"]["recovery_cleared"] is True
    journal.close()


def test_autonomous_recovery_does_not_bypass_omega_or_clear_on_block(tmp_path):
    calls: list[dict] = []

    def executor(proposal):
        calls.append(dict(proposal))
        return ExecutionResult(
            status="FAIL" if len(calls) == 1 else "OK",
            effect={"call": len(calls)},
        )

    journal, engine, supervisor = _supervisor(tmp_path, executor)
    failed = supervisor.step(proposal={"action": "READ", "resource": "sensor"})
    assert failed.cycle.recovery_required is True

    blocked = supervisor.step(signature_valid=False)
    assert blocked.autonomous_recovery_attempted is True
    assert blocked.cycle.effective_decision is Decision.BLOCK
    assert blocked.cycle.cycle.executed is False
    assert blocked.cycle.recovery_required is True
    assert len(calls) == 1

    recovered = supervisor.step()
    assert recovered.cycle.effective_decision is Decision.PASS
    assert recovered.cycle.recovery_required is False
    assert len(calls) == 2
    journal.close()


def test_external_proposal_cannot_replace_policy_while_recovery_pending(tmp_path):
    def executor(proposal):
        return ExecutionResult(status="FAIL", effect={"proposal": dict(proposal)})

    journal, engine, supervisor = _supervisor(tmp_path, executor)
    failed = supervisor.step(proposal={"action": "READ", "resource": "sensor"})
    assert failed.cycle.recovery_required is True

    with pytest.raises(ValueError, match="external proposal"):
        supervisor.step(proposal={"action": "READ", "resource": "different"})
    assert engine.feedback.recovery_required is True
    journal.close()


def test_recovery_policy_rejects_reserved_keys():
    with pytest.raises(ValueError, match="reserved recovery policy keys"):
        BoundedRecoveryPolicy.build(
            action="READ",
            parameters={"recovery_for": "forged"},
        )

    with pytest.raises(ValueError, match="reserved recovery policy keys"):
        BoundedRecoveryPolicy.build(
            action="READ",
            parameters={"action": "EXECUTE"},
        )


def test_concurrent_supervisor_calls_cannot_duplicate_recovery_effect(tmp_path):
    call_lock = threading.Lock()
    calls: list[dict] = []

    def executor(proposal):
        with call_lock:
            calls.append(dict(proposal))
            number = len(calls)
        return ExecutionResult(
            status="FAIL" if number == 1 else "OK",
            effect={"call": number},
        )

    journal, engine, supervisor = _supervisor(tmp_path, executor)
    failed = supervisor.step(proposal={"action": "READ", "resource": "sensor"})
    assert failed.cycle.recovery_required is True

    start = threading.Barrier(3)

    def recover():
        start.wait()
        return supervisor.step()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(recover) for _ in range(2)]
        start.wait()
        results = [future.result(timeout=5) for future in futures]

    assert sum(result.autonomous_recovery_attempted for result in results) == 1
    assert len(calls) == 2
    assert engine.feedback.recovery_required is False

    events = journal.read(limit=500)
    assert sum(
        event.event_type == "BOUNDED_AUTONOMOUS_RECOVERY_PROPOSED"
        for event in events
    ) == 1
    assert sum(
        event.event_type == "BOUNDED_AUTONOMOUS_RECOVERY_OUTCOME"
        for event in events
    ) == 1
    journal.close()



def test_blocked_recovery_retry_gets_unique_attempt_and_persists_receipt_inputs(tmp_path):
    calls: list[dict] = []

    def executor(proposal):
        calls.append(dict(proposal))
        return ExecutionResult(
            status="FAIL" if len(calls) == 1 else "OK",
            effect={"call": len(calls)},
        )

    journal, engine, supervisor = _supervisor(tmp_path, executor)
    failed = supervisor.step(proposal={"action": "READ", "resource": "sensor"})
    assert failed.cycle.recovery_required is True

    blocked = supervisor.step(signature_valid=False)
    assert blocked.cycle.effective_decision is Decision.BLOCK
    assert blocked.cycle.recovery_required is True

    recovered = supervisor.step()
    assert recovered.cycle.effective_decision is Decision.PASS
    assert recovered.cycle.recovery_required is False

    events = journal.read(limit=500)
    proposed = [
        event for event in events
        if event.event_type == "BOUNDED_AUTONOMOUS_RECOVERY_PROPOSED"
    ]
    outcomes = [
        event for event in events
        if event.event_type == "BOUNDED_AUTONOMOUS_RECOVERY_OUTCOME"
    ]
    assert len(proposed) == 2
    assert len(outcomes) == 2
    assert proposed[0].event_id != proposed[1].event_id
    assert proposed[0].payload["inputs"]["attempt_number"] == 1
    assert proposed[1].payload["inputs"]["attempt_number"] == 2
    assert "inputs" in outcomes[0].payload
    assert "recovery_cycle_id" in outcomes[0].payload["inputs"]
    assert outcomes[1].causation_id == proposed[1].event_id
    journal.close()


def test_pending_recovery_cannot_resume_under_different_policy_after_restart(tmp_path):
    calls: list[dict] = []

    def executor(proposal):
        calls.append(dict(proposal))
        return ExecutionResult(status="FAIL", effect={"call": len(calls)})

    journal, engine, supervisor = _supervisor(tmp_path, executor)
    failed = supervisor.step(proposal={"action": "READ", "resource": "sensor"})
    assert failed.cycle.recovery_required is True
    journal.close()

    reopened = DurableEventJournal(tmp_path / "bounded-autonomous.sqlite3")
    reopened_engine = ClosedLoopPhysiologyEngine(
        organism=_organism(),
        journal=reopened,
        telemetry=_telemetry(),
        executor=executor,
        feedback_state_secret=FEEDBACK_SECRET,
    )
    with pytest.raises(ValueError, match="policy fingerprint mismatch"):
        BoundedHomeostaticSupervisor(
            engine=reopened_engine,
            recovery_policy=BoundedRecoveryPolicy.build(
                action="WRITE",
                parameters={"resource": "sensor"},
            ),
        )
    assert reopened_engine.feedback.recovery_required is True
    reopened.close()


def test_retry_causal_link_tracks_latest_feedback_event(tmp_path):
    calls: list[dict] = []

    def executor(proposal):
        calls.append(dict(proposal))
        if len(calls) <= 2:
            return ExecutionResult(status="FAIL", effect={"call": len(calls)})
        return ExecutionResult(status="OK", effect={"call": len(calls)})

    journal, engine, supervisor = _supervisor(tmp_path, executor)
    failed = supervisor.step(proposal={"action": "READ", "resource": "sensor"})
    origin = failed.cycle.recovery_for_cycle_id
    assert origin is not None

    retry_one = supervisor.step()
    assert retry_one.cycle.recovery_required is True
    latest_feedback_event_id = f"{retry_one.cycle.cycle.cycle_id}:effect-feedback"

    retry_two = supervisor.step()
    assert retry_two.cycle.recovery_required is False

    proposed = [
        event for event in journal.read(limit=500)
        if event.event_type == "BOUNDED_AUTONOMOUS_RECOVERY_PROPOSED"
    ]
    assert len(proposed) == 2
    assert proposed[1].causation_id == latest_feedback_event_id
    assert (
        proposed[1].payload["inputs"]["source_feedback_event_id"]
        == latest_feedback_event_id
    )
    journal.close()
