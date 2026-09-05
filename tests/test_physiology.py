from __future__ import annotations

import pytest

from app.core import Decision
from app.deterministic_lab import DeterministicFaultPlan, DeterministicTelemetry, FaultDirective, FaultKind
from app.organism_loop import GovernedOrganism
from app.physiology import (
    DurableEventJournal,
    ExecutionResult,
    HealthState,
    HomeostaticPolicy,
    PhysiologyEngine,
    RecoveryAction,
)


def _organism() -> GovernedOrganism:
    return GovernedOrganism(
        organism_id="test-organism",
        frozen_contract_hash="a" * 64,
        runtime_id="pytest-runtime",
        state_secret="state-secret",
        authority_secrets={"independent-authorizer": "authority-secret"},
    )


def test_journal_is_durable_idempotent_and_offsets_are_monotonic(tmp_path):
    path = tmp_path / "physiology.sqlite3"
    journal = DurableEventJournal(path)
    first = journal.append(event_id="e1", topic="t", event_type="OBS", payload={"x": 1}, created_ns=1)
    same = journal.append(event_id="e1", topic="t", event_type="OBS", payload={"x": 1}, created_ns=1)
    assert first.seq == same.seq
    assert first.receipt_hash == same.receipt_hash
    with pytest.raises(ValueError, match="collision"):
        journal.append(event_id="e1", topic="t", event_type="OBS", payload={"x": 2}, created_ns=1)
    journal.ack("worker", first.seq)
    assert journal.consumer_offset("worker") == first.seq
    with pytest.raises(ValueError, match="monotonic"):
        journal.ack("worker", 0)
    assert journal.integrity_check() is True
    journal.close()

    reopened = DurableEventJournal(path)
    assert reopened.consumer_offset("worker") == first.seq
    events = reopened.read()
    assert len(events) == 1
    assert events[0].payload == {"x": 1}
    reopened.close()


def test_normal_cycle_passes_through_constitution_and_records_effect(tmp_path):
    plan = DeterministicFaultPlan(seed=7, cycles=1, directives=())
    telemetry = DeterministicTelemetry(plan=plan)
    journal = DurableEventJournal(tmp_path / "normal.sqlite3")
    executed: list[dict] = []

    def executor(proposal):
        executed.append(dict(proposal))
        return ExecutionResult(status="OK", effect={"observed": True})

    engine = PhysiologyEngine(
        organism=_organism(),
        journal=journal,
        telemetry=telemetry,
        executor=executor,
    )
    result = engine.tick(proposal={"action": "READ", "resource": "sensor"})

    assert result.health is HealthState.NORMAL
    assert result.decision is Decision.PASS
    assert result.executed is True
    assert executed == [{"action": "READ", "resource": "sensor"}]
    event_types = [event.event_type for event in journal.read(limit=100)]
    assert event_types == ["TICK", "OBSERVATION", "RECOVERY_PLAN", "DECISION", "EXECUTION", "EFFECT", "MEMORY_COMMIT"]
    journal.close()


def test_critical_telemetry_enters_safe_mode_and_requires_hysteresis_to_exit(tmp_path):
    plan = DeterministicFaultPlan(
        seed=11,
        cycles=4,
        directives=(FaultDirective(cycle_seq=1, kind=FaultKind.CRITICAL_DISK),),
    )
    telemetry = DeterministicTelemetry(plan=plan)
    journal = DurableEventJournal(tmp_path / "homeostasis.sqlite3")
    engine = PhysiologyEngine(
        organism=_organism(),
        journal=journal,
        telemetry=telemetry,
        policy=HomeostaticPolicy(normal_streak_to_exit_safe_mode=3),
        executor=lambda proposal: ExecutionResult(status="OK", effect={"proposal": dict(proposal)}),
    )

    critical = engine.tick()
    assert critical.health is HealthState.CRITICAL
    assert critical.recovery_action is RecoveryAction.ENTER_SAFE_MODE
    assert engine.safe_mode is True

    hold_1 = engine.tick(proposal={"action": "READ"})
    hold_2 = engine.tick(proposal={"action": "READ"})
    assert hold_1.decision is Decision.HOLD
    assert hold_2.decision is Decision.HOLD
    assert engine.safe_mode is True

    recovered = engine.tick(proposal={"action": "READ"})
    assert recovered.recovery_action is RecoveryAction.EXIT_SAFE_MODE
    assert recovered.decision is Decision.PASS
    assert recovered.executed is True
    assert engine.safe_mode is False
    journal.close()


def test_pass_without_executor_fails_closed(tmp_path):
    plan = DeterministicFaultPlan(seed=1, cycles=1, directives=())
    journal = DurableEventJournal(tmp_path / "no-executor.sqlite3")
    engine = PhysiologyEngine(
        organism=_organism(),
        journal=journal,
        telemetry=DeterministicTelemetry(plan=plan),
        executor=None,
    )
    with pytest.raises(RuntimeError, match="explicit executor"):
        engine.tick(proposal={"action": "READ"})
    journal.close()
