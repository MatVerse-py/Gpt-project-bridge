from __future__ import annotations

import pytest

from app.deterministic_lab import DeterministicFaultPlan, DeterministicTelemetry
from app.organism_loop import GovernedOrganism
from app.physiology import DurableEventJournal, PhysiologyEngine
from app.physiology_recovery import RestartSafePhysiologyEngine, find_incomplete_cycles, seal_incomplete_cycles


def _organism() -> GovernedOrganism:
    return GovernedOrganism(
        organism_id="restart-organism",
        frozen_contract_hash="b" * 64,
        runtime_id="restart-runtime",
        state_secret="state-secret",
        authority_secrets={"authorizer": "authority-secret"},
    )


def test_incomplete_cycle_is_sealed_as_aborted_and_not_fabricated_as_commit(tmp_path):
    journal = DurableEventJournal(tmp_path / "restart.sqlite3")
    engine = PhysiologyEngine(
        organism=_organism(),
        journal=journal,
        telemetry=DeterministicTelemetry(plan=DeterministicFaultPlan(seed=1, cycles=1, directives=())),
        executor=None,
    )

    with pytest.raises(RuntimeError, match="explicit executor"):
        engine.tick(proposal={"action": "READ"})

    incomplete = find_incomplete_cycles(journal)
    assert len(incomplete) == 1
    assert "MEMORY_COMMIT" not in incomplete[0].event_types

    sealed = seal_incomplete_cycles(journal)
    assert sealed == (f"{incomplete[0].cycle_id}:aborted",)
    assert find_incomplete_cycles(journal) == ()

    event_types = [event.event_type for event in journal.read(limit=100)]
    assert "CYCLE_ABORTED" in event_types
    assert "MEMORY_COMMIT" not in event_types
    journal.close()


def test_restart_safe_engine_recovers_once_and_continues_next_cycle(tmp_path):
    path = tmp_path / "resume.sqlite3"
    journal = DurableEventJournal(path)
    first = PhysiologyEngine(
        organism=_organism(),
        journal=journal,
        telemetry=DeterministicTelemetry(plan=DeterministicFaultPlan(seed=2, cycles=1, directives=())),
        executor=None,
    )
    with pytest.raises(RuntimeError):
        first.tick(proposal={"action": "READ"})
    journal.close()

    reopened = DurableEventJournal(path)
    resumed = RestartSafePhysiologyEngine(
        organism=_organism(),
        journal=reopened,
        telemetry=DeterministicTelemetry(plan=DeterministicFaultPlan(seed=3, cycles=1, directives=())),
        executor=None,
    )
    assert len(resumed.recovered_event_ids) == 1
    result = resumed.tick()
    assert result.cycle_seq == 2

    second = RestartSafePhysiologyEngine(
        organism=_organism(),
        journal=reopened,
        telemetry=DeterministicTelemetry(plan=DeterministicFaultPlan(seed=4, cycles=1, directives=())),
        executor=None,
    )
    assert second.recovered_event_ids == ()
    reopened.close()
