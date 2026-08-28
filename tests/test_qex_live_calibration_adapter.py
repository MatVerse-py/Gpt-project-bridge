from datetime import datetime, timezone
from types import SimpleNamespace

from qiskit_aer import AerSimulator
from qiskit_ibm_runtime.fake_provider import FakeSherbrooke

from app.core import Decision
from app.qex_experiment import qex_substrate_01_contract
from app.qex_live_calibration_adapter import (
    LiveCalibrationAerAdapter,
    LiveCalibrationUnavailable,
    prepare_live_calibration,
)


class StubProperties:
    def to_dict(self):
        return {
            "backend_name": "ibm_stub_live",
            "backend_version": "1.2.3",
            "last_update_date": datetime(2026, 8, 28, 23, 0, tzinfo=timezone.utc),
            "qubits": [[{"name": "T1", "value": 100.0, "unit": "us"}]],
            "gates": [],
            "general": [],
        }


class StubLiveBackend:
    name = "ibm_stub_live"
    backend_version = "1.2.3"
    calibration_id = "cal-20260828-2300"
    simulator = False

    def __init__(self):
        self.refresh_values = []

    def status(self):
        return SimpleNamespace(operational=True, pending_jobs=0)

    def properties(self, refresh=False):
        self.refresh_values.append(refresh)
        return StubProperties()


def local_snapshot_simulator(_backend):
    return AerSimulator.from_backend(FakeSherbrooke())


def test_missing_live_backend_is_hold_and_cannot_execute():
    preparation = prepare_live_calibration(None)
    assert preparation.decision is Decision.HOLD
    assert preparation.calibration_snapshot_hash is None
    try:
        LiveCalibrationAerAdapter(preparation)
    except LiveCalibrationUnavailable as exc:
        assert "unavailable" in str(exc)
    else:
        raise AssertionError("HOLD preparation was allowed to execute")


def test_fake_backend_cannot_be_promoted_to_live_calibration():
    preparation = prepare_live_calibration(FakeSherbrooke())
    assert preparation.decision is Decision.HOLD
    assert "simulator/fake" in preparation.reason


def test_live_calibration_refresh_is_hashed_and_bound_before_execution():
    backend = StubLiveBackend()
    preparation = prepare_live_calibration(backend, simulator_factory=local_snapshot_simulator)

    assert preparation.decision is Decision.PASS
    assert backend.refresh_values == [True]
    assert preparation.backend_name == "ibm_stub_live"
    assert preparation.calibration_id == "cal-20260828-2300"
    assert preparation.properties_last_update == "2026-08-28T23:00:00+00:00"
    assert len(preparation.calibration_snapshot_hash or "") == 64

    replay = prepare_live_calibration(StubLiveBackend(), simulator_factory=local_snapshot_simulator)
    assert replay.calibration_snapshot_hash == preparation.calibration_snapshot_hash


def test_local_aer_replay_preserves_frozen_contract_and_is_seed_replayable():
    preparation = prepare_live_calibration(StubLiveBackend(), simulator_factory=local_snapshot_simulator)
    adapter = LiveCalibrationAerAdapter(preparation, shots=1024, seed=369)
    contract = qex_substrate_01_contract()

    first = adapter.execute(contract, {"bit": 0})
    second = adapter.execute(contract, {"bit": 0})

    assert first.contract_hash == contract.contract_hash
    assert second.contract_hash == contract.contract_hash
    assert first.canonical_observable() == second.canonical_observable()
    assert first.backend_metadata_hash == second.backend_metadata_hash
    assert preparation.calibration_snapshot_hash[:12] in first.backend_id
    assert first.result in (0, 1)
