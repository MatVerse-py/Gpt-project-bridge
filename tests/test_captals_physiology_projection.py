"""Integration tests: real Bridge physiology -> local Captals projection.

These exercise the existing canonical loop and durable SQLite journal; they
are not financial production tests or independent evidence verification.
"""
from dataclasses import replace

import pytest

from app.core import Decision
from app.captals_physiology_projection import project_cycle, record_projection
from app.deterministic_lab import DeterministicFaultPlan, DeterministicTelemetry
from app.organism_loop import GovernedOrganism
from app.physiology import DurableEventJournal, ExecutionResult
from app.physiology_effect_feedback import ClosedLoopPhysiologyEngine


def _engine(tmp_path, *, executor=None, telemetry=None):
    journal = DurableEventJournal(tmp_path / "organism.sqlite3")
    organism = GovernedOrganism(
        organism_id="captals-projection-test",
        frozen_contract_hash="b" * 64,
        runtime_id="captals-projection-runtime",
        state_secret="local-test-only",
        authority_secrets={"independent-authorizer": "local-test-secret"},
    )
    if telemetry is None:
        telemetry = DeterministicTelemetry(
            plan=DeterministicFaultPlan(seed=19, cycles=30, directives=())
        )
    engine = ClosedLoopPhysiologyEngine(
        organism=organism,
        journal=journal,
        telemetry=telemetry,
        executor=executor or (lambda p: ExecutionResult("OK", {"accepted": True})),
        feedback_state_secret="local-feedback-secret",
    )
    return journal, engine


def test_real_loop_projects_simulated_cpu_without_inventing_capital(tmp_path):
    journal, engine = _engine(tmp_path)
    try:
        result = engine.tick(proposal={"action": "READ", "resource": "sensor"})
        assert result.effective_decision is Decision.PASS
        value = project_cycle(result, journal)
        assert value["gate_decision"] == "PASS"
        assert value["source"] == "bridge.physiology.simulated"
        assert value["costs"]["computational"]["instrument"] == "deterministic_lab.logical_cpu"
        assert value["costs"]["computational"]["value"] == pytest.approx(0.001)
        assert value["capital_delta"] == {}
        assert value["evidence_status"] == "UNVERIFIED"
        assert value["replay_status"] == "NOT_AVAILABLE"
        assert value["budget_limits"] == {}
    finally:
        journal.close()


def test_projection_is_idempotent_and_durable_across_reopen(tmp_path):
    journal, engine = _engine(tmp_path)
    result = engine.tick(proposal={"action": "READ"})
    first, row_one = record_projection(result, journal)
    second, row_two = record_projection(result, journal)
    assert first == second
    assert row_one.seq == row_two.seq
    assert row_one.event_type == "METABOLIC_PROJECTION"
    assert row_one.causation_id == f"{result.cycle.cycle_id}:memory"
    journal.close()

    reopened = DurableEventJournal(tmp_path / "organism.sqlite3")
    try:
        entries = reopened.read(topic="captals")
        assert len(entries) == 1
        assert entries[0].payload["metabolic_event"] == first
        _, after_reopen = record_projection(result, reopened)
        assert after_reopen.seq == row_one.seq
    finally:
        reopened.close()


def test_failed_effect_does_not_become_eligible_capital(tmp_path):
    journal, engine = _engine(tmp_path, executor=lambda p: ExecutionResult("ERROR", {"failed": True}))
    try:
        result = engine.tick(proposal={"action": "READ"})
        assert result.recovery_required
        projected = project_cycle(result, journal)
        assert projected["capital_delta"] == {}
        assert projected["evidence_status"] == "UNVERIFIED"
        blocked_next = engine.tick(proposal={"action": "READ"})
        assert blocked_next.effective_decision is Decision.HOLD
        on_hold = project_cycle(blocked_next, journal)
        assert on_hold["gate_decision"] == "HOLD"
        assert on_hold["costs"] == {}
    finally:
        journal.close()


def test_tampered_cycle_receipt_fails_closed(tmp_path):
    journal, engine = _engine(tmp_path)
    try:
        result = engine.tick(proposal={"action": "READ"})
        tampered = replace(result, cycle=replace(result.cycle, receipt_hash="f" * 64))
        with pytest.raises(ValueError, match="cycle receipt mismatch"):
            project_cycle(tampered, journal)
    finally:
        journal.close()


def test_tampered_feedback_receipt_fails_closed(tmp_path):
    journal, engine = _engine(tmp_path)
    try:
        result = engine.tick(proposal={"action": "READ"})
        tampered = replace(result, feedback_receipt_hash="f" * 64)
        with pytest.raises(ValueError, match="effect feedback receipt mismatch"):
            project_cycle(tampered, journal)
    finally:
        journal.close()


def test_missing_feedback_binding_fails_closed(tmp_path):
    journal, engine = _engine(tmp_path)
    try:
        result = engine.tick(proposal={"action": "READ"})
        with pytest.raises(ValueError, match="feedback exists"):
            project_cycle(replace(result, feedback_receipt_hash=None), journal)
    finally:
        journal.close()


def test_unrelated_journal_cannot_be_used_as_evidence(tmp_path):
    journal, engine = _engine(tmp_path)
    other = DurableEventJournal(tmp_path / "unrelated.sqlite3")
    try:
        result = engine.tick(proposal={"action": "READ"})
        with pytest.raises(ValueError, match="missing durable memory commit"):
            project_cycle(result, other)
    finally:
        other.close()
        journal.close()


def test_unknown_instrument_produces_no_cost_metric(tmp_path):
    class UnsupportedTelemetry:
        def __init__(self):
            self.delegate = DeterministicTelemetry(
                plan=DeterministicFaultPlan(seed=4, cycles=10, directives=())
            )

        def sample(self):
            sample = self.delegate.sample()
            return replace(sample, instruments={**sample.instruments, "process_time_ns": "unknown.timer"})

    journal, engine = _engine(tmp_path, telemetry=UnsupportedTelemetry())
    try:
        result = engine.tick(proposal={"action": "READ"})
        projected = project_cycle(result, journal)
        assert projected["costs"] == {}
        assert projected["capital_delta"] == {}
        assert projected["source"] == "bridge.physiology.local"
    finally:
        journal.close()


def test_invalid_input_is_rejected(tmp_path):
    journal, _ = _engine(tmp_path)
    try:
        with pytest.raises(TypeError, match="ClosedLoopCycleResult"):
            project_cycle({"decision": "PASS"}, journal)
    finally:
        journal.close()


def test_no_proposal_does_not_turn_into_approval(tmp_path):
    journal, engine = _engine(tmp_path)
    try:
        result = engine.tick()
        projected = project_cycle(result, journal)
        assert projected["gate_decision"] == "HOLD"
        assert projected["capital_delta"] == {}
    finally:
        journal.close()
