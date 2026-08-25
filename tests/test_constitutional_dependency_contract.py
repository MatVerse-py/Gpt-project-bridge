import pytest

import app.core as core
import app.organism_loop as organism_loop


def test_source_less_guard_fingerprint_is_deterministic(monkeypatch):
    original_getsource = organism_loop.inspect.getsource

    def source_unavailable(obj):
        if obj in {
            organism_loop.core_module,
            organism_loop.evaluate_hdb,
            organism_loop.omega_gate,
            organism_loop.stable_hash,
        }:
            raise OSError("source intentionally unavailable")
        return original_getsource(obj)

    monkeypatch.setattr(organism_loop.inspect, "getsource", source_unavailable)

    first = organism_loop.gate_fingerprint()
    second = organism_loop.gate_fingerprint()

    assert len(first) == 64
    assert first == second


def test_incompatible_hdb_dependency_fails_closed(monkeypatch):
    class IncompatibleHDBResult:
        def __init__(self, decision, reason):
            self.decision = decision
            self.reason = reason

    monkeypatch.setattr(core, "HDBResult", IncompatibleHDBResult)

    with pytest.raises(RuntimeError, match="HDBResult constitutional contract mismatch"):
        core.evaluate_hdb(None)
