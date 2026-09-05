from __future__ import annotations

from copy import deepcopy

from app.core import Decision
from app.deterministic_lab import DeterministicFaultPlan, DeterministicTelemetry
from app.organism_loop import GovernedOrganism
from app.physiology import DurableEventJournal, ExecutionResult, PhysiologyEngine
from app.runtime_binding import build_execution_binding, validate_execution_binding
from app.runtime_bound_execution import (
    REPLAY_DIVERGENT,
    REPLAY_EXACT,
    RuntimeBoundExecutor,
    bind_executor,
    execute_bound_workload,
    replay_bound_execution_record,
)
from app.runtime_discovery import discover_runtime_capabilities


def _binding(*, digest: str = "sha256:qwen") -> dict:
    def getter(url: str, timeout: float) -> dict:
        if url.endswith("/api/version"):
            return {"version": "0.11.0"}
        if url.endswith("/api/tags"):
            return {
                "models": [
                    {"name": "qwen2.5:0.5b", "digest": digest, "size": 398000000},
                ]
            }
        raise ConnectionError("llama.cpp unavailable")

    report = discover_runtime_capabilities(
        getter=getter,
        binary_probe=lambda candidates: (None, None, "binary_not_found"),
    )
    binding = build_execution_binding(report, required_model="qwen2.5:0.5b")
    assert binding["decision"] == "PASS"
    return binding


def _organism() -> GovernedOrganism:
    return GovernedOrganism(
        organism_id="runtime-bound-test-organism",
        frozen_contract_hash="a" * 64,
        runtime_id="pytest-runtime",
        state_secret="state-secret",
        authority_secrets={"independent-authorizer": "authority-secret"},
    )


def _engine(tmp_path, *, binding: dict, delegate):
    plan = DeterministicFaultPlan(seed=7, cycles=2, directives=())
    journal = DurableEventJournal(tmp_path / "runtime-bound.sqlite3")
    bound_executor = bind_executor(binding, delegate)
    engine = PhysiologyEngine(
        organism=_organism(),
        journal=journal,
        telemetry=DeterministicTelemetry(plan=plan),
        executor=bound_executor,
    )
    return engine, journal


def test_standalone_binding_validation_recomputes_hash() -> None:
    binding = _binding()
    valid, reason = validate_execution_binding(binding)
    assert (valid, reason) == (True, "ok")

    forged = deepcopy(binding)
    forged["runtime"]["runtime_id"] = "forged-runtime"
    valid, reason = validate_execution_binding(forged)
    assert valid is False
    assert reason == "binding_hash_mismatch"


def test_bound_executor_receives_exact_identity_after_omega_pass(tmp_path) -> None:
    binding = _binding()
    calls: list[tuple[dict, dict]] = []

    def delegate(proposal, identity):
        calls.append((dict(proposal), dict(identity)))
        return ExecutionResult(status="OK", effect={"validated": True, "source": "test-adapter"})

    engine, journal = _engine(tmp_path, binding=binding, delegate=delegate)
    record = execute_bound_workload(
        engine=engine,
        binding=binding,
        proposal={"action": "READ", "resource": "sensor"},
    )

    assert record["decision"] == Decision.PASS.value
    assert record["executed"] is True
    assert calls == [
        (
            {"action": "READ", "resource": "sensor"},
            {
                "runtime": binding["runtime"],
                "model": binding["model"],
                "container": None,
            },
        )
    ]
    bound_events = journal.read(limit=100, topic="runtime-binding")
    assert len(bound_events) == 1
    event = bound_events[0]
    assert event.event_type == "RUNTIME_BOUND_EXECUTION"
    assert event.correlation_id == record["cycle_id"]
    assert event.causation_id == f"{record['cycle_id']}:memory"
    assert event.payload["binding_hash"] == binding["binding_hash"]
    assert event.payload["proposal_hash"] == record["proposal_hash"]
    assert "action" not in event.payload
    journal.close()


def test_binding_does_not_bypass_hdb_or_omega(tmp_path) -> None:
    binding = _binding()
    calls: list[dict] = []

    def delegate(proposal, identity):
        calls.append(dict(proposal))
        return ExecutionResult(status="OK", effect={"validated": True})

    engine, journal = _engine(tmp_path, binding=binding, delegate=delegate)
    record = execute_bound_workload(
        engine=engine,
        binding=binding,
        proposal={"action": "READ", "resource": "sensor"},
        human={"consent": False, "purpose": "test"},
    )

    assert record["decision"] == Decision.HOLD.value
    assert record["executed"] is False
    assert "consent" in record["reason"]
    assert calls == []
    event_types = [event.event_type for event in journal.read(limit=100, topic="physiology")]
    assert "DECISION" in event_types
    assert "EXECUTION" not in event_types
    assert "MEMORY_COMMIT" in event_types
    journal.close()


def test_forged_binding_is_hold_before_physiology_or_executor(tmp_path) -> None:
    binding = _binding()
    calls: list[dict] = []

    def delegate(proposal, identity):
        calls.append(dict(proposal))
        return ExecutionResult(status="OK", effect={"validated": True})

    engine, journal = _engine(tmp_path, binding=binding, delegate=delegate)
    forged = deepcopy(binding)
    forged["model"]["digest"] = "sha256:forged"

    record = execute_bound_workload(
        engine=engine,
        binding=forged,
        proposal={"action": "READ"},
    )

    assert record["status"] == "HOLD"
    assert record["decision"] == Decision.HOLD.value
    assert record["executed"] is False
    assert record["reason"] == "invalid_binding:binding_hash_mismatch"
    assert calls == []
    assert journal.read(limit=100) == ()
    journal.close()


def test_executor_bound_to_different_identity_cannot_run(tmp_path) -> None:
    binding = _binding(digest="sha256:one")
    other = _binding(digest="sha256:two")
    calls: list[dict] = []

    def delegate(proposal, identity):
        calls.append(dict(proposal))
        return ExecutionResult(status="OK", effect={"validated": True})

    engine, journal = _engine(tmp_path, binding=binding, delegate=delegate)
    assert isinstance(engine.executor, RuntimeBoundExecutor)
    record = execute_bound_workload(engine=engine, binding=other, proposal={"action": "READ"})

    assert record["status"] == "HOLD"
    assert record["reason"] == "executor_binding_identity_mismatch"
    assert calls == []
    assert journal.read(limit=100) == ()
    journal.close()


def test_observable_bound_execution_receipt_replays_exactly(tmp_path) -> None:
    binding = _binding()

    def delegate(proposal, identity):
        return ExecutionResult(status="OK", effect={"validated": True, "result_hash": "b" * 64})

    engine, journal = _engine(tmp_path, binding=binding, delegate=delegate)
    record = execute_bound_workload(engine=engine, binding=binding, proposal={"action": "READ"})

    replay = replay_bound_execution_record(record)
    assert replay["status"] == REPLAY_EXACT
    assert replay["binding_hash"] == binding["binding_hash"]
    assert replay["cycle_id"] == record["cycle_id"]

    tampered = deepcopy(record)
    tampered["runtime_identity"]["model"]["digest"] = "sha256:tampered"
    divergent = replay_bound_execution_record(tampered)
    assert divergent["status"] == REPLAY_DIVERGENT
    assert divergent["reason"] == "record_hash_mismatch"
    journal.close()
