from __future__ import annotations

import threading
import time

from app.core import Decision
from app.deterministic_lab import DeterministicFaultPlan, DeterministicTelemetry
from app.organism_loop import GovernedOrganism
from app.physiology import DurableEventJournal, ExecutionResult
from app.physiology_effect_feedback import ClosedLoopPhysiologyEngine

FEEDBACK_SECRET = "feedback-loop-hardening-secret"


def _organism() -> GovernedOrganism:
    return GovernedOrganism(
        organism_id="feedback-hardening-organism",
        frozen_contract_hash="c" * 64,
        runtime_id="feedback-hardening-runtime",
        state_secret="feedback-hardening-state-secret",
        authority_secrets={"independent-authorizer": "feedback-hardening-authority-secret"},
    )


def _telemetry(cycles: int = 32) -> DeterministicTelemetry:
    return DeterministicTelemetry(
        plan=DeterministicFaultPlan(seed=91, cycles=cycles, directives=())
    )


def test_overlapping_callers_are_serialized_per_engine(tmp_path):
    journal = DurableEventJournal(tmp_path / "serialized.sqlite3")
    active = 0
    max_active = 0
    guard = threading.Lock()
    start = threading.Barrier(3)
    results = []
    errors = []

    def executor(proposal):
        nonlocal active, max_active
        with guard:
            active += 1
            max_active = max(max_active, active)
        try:
            time.sleep(0.03)
            return ExecutionResult(status="OK", effect={"request": proposal["request"]})
        finally:
            with guard:
                active -= 1

    engine = ClosedLoopPhysiologyEngine(
        organism=_organism(),
        journal=journal,
        telemetry=_telemetry(),
        executor=executor,
        feedback_state_secret=FEEDBACK_SECRET,
    )

    def run(request_id: str) -> None:
        try:
            start.wait(timeout=2)
            results.append(engine.tick(proposal={"action": "READ", "request": request_id}))
        except Exception as exc:  # pragma: no cover - assertion reports unexpected thread failure
            errors.append(exc)

    threads = [
        threading.Thread(target=run, args=("A",)),
        threading.Thread(target=run, args=("B",)),
    ]
    for thread in threads:
        thread.start()
    start.wait(timeout=2)
    for thread in threads:
        thread.join(timeout=3)

    assert errors == []
    assert all(not thread.is_alive() for thread in threads)
    assert len(results) == 2
    assert max_active == 1
    assert all(result.effective_decision is Decision.PASS for result in results)
    assert all(result.effect_status == "OK" for result in results)

    feedback_events = [
        event
        for event in journal.read(limit=100)
        if event.event_type == "EFFECT_FEEDBACK"
    ]
    assert len(feedback_events) == 2
    assert len({event.correlation_id for event in feedback_events}) == 2
    journal.close()


class _FailSecondSampleTelemetry:
    def __init__(self) -> None:
        self.delegate = _telemetry()
        self.calls = 0

    def sample(self):
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError("post-effect sensor unavailable")
        return self.delegate.sample()


def test_post_effect_measurement_failure_opens_recovery_gate(tmp_path):
    journal = DurableEventJournal(tmp_path / "measurement-failure.sqlite3")
    telemetry = _FailSecondSampleTelemetry()
    executions = []

    def executor(proposal):
        executions.append(dict(proposal))
        return ExecutionResult(status="OK", effect={"executed": True})

    engine = ClosedLoopPhysiologyEngine(
        organism=_organism(),
        journal=journal,
        telemetry=telemetry,
        executor=executor,
        feedback_state_secret=FEEDBACK_SECRET,
    )

    first = engine.tick(proposal={"action": "READ", "resource": "sensor"})
    assert first.cycle.executed is True
    assert first.effect_status == "OK"
    assert first.post_effect_health is None
    assert first.recovery_required is True
    assert first.recovery_for_cycle_id == first.cycle.cycle_id

    feedback_event = next(
        event
        for event in journal.read(limit=100)
        if event.event_type == "EFFECT_FEEDBACK"
    )
    assert feedback_event.payload["inputs"]["measurement_error"] == {
        "error_type": "RuntimeError",
        "error_message": "post-effect sensor unavailable",
    }
    assert feedback_event.payload["outputs"]["effect_success"] is False

    held = engine.tick(proposal={"action": "READ", "resource": "other"})
    assert held.effective_decision is Decision.HOLD
    assert executions == [{"action": "READ", "resource": "sensor"}]
    journal.close()
