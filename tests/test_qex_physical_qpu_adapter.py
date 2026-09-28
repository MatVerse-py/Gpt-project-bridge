from datetime import datetime, timezone
from types import SimpleNamespace

from qiskit_ibm_runtime.fake_provider import FakeSherbrooke

from app.core import Decision
from app.qex_experiment import qex_substrate_01_contract
from app.qex_physical_qpu_adapter import (
    PhysicalExecutionAuthorization,
    PhysicalQPUAdapter,
    PhysicalQPUExecutionFailed,
    PhysicalQPUUnavailable,
    prepare_physical_qpu,
)


class StubProperties:
    def to_dict(self):
        return {
            "backend_name": "ibm_stub_physical",
            "backend_version": "2.0.0",
            "last_update_date": datetime(2026, 8, 28, 23, 30, tzinfo=timezone.utc),
            "qubits": [[{"name": "T1", "value": 120.0, "unit": "us"}]],
            "gates": [],
            "general": [],
        }


class StubPhysicalBackend:
    name = "ibm_stub_physical"
    backend_version = "2.0.0"
    calibration_id = "cal-physical-20260828-2330"
    simulator = False

    def __init__(self):
        self.refresh_values = []

    def status(self):
        return SimpleNamespace(operational=True, pending_jobs=7)

    def properties(self, refresh=False):
        self.refresh_values.append(refresh)
        return StubProperties()


class IdentityPassManager:
    def run(self, circuit):
        return circuit


class StubMeasurements:
    def __init__(self, counts):
        self._counts = counts

    def get_counts(self):
        return dict(self._counts)


class StubJob:
    usage_estimation = {"quantum_seconds": 0.25}

    def __init__(self, counts):
        self._counts = counts

    def job_id(self):
        return "job-physical-qex-001"

    def result(self):
        data = SimpleNamespace(meas=StubMeasurements(self._counts))
        return [SimpleNamespace(data=data)]

    def metrics(self):
        return {
            "timestamps": {"created": "2026-08-28T23:31:00Z", "finished": "2026-08-28T23:31:02Z"},
            "usage": {"quantum_seconds": 0.25},
        }


class StubSampler:
    def __init__(self, counts):
        self.counts = counts
        self.calls = []

    def run(self, pubs, *, shots=None):
        self.calls.append((pubs, shots))
        return StubJob(self.counts)


def auth(*, authorized=True, allow=True, max_shots=1024):
    return PhysicalExecutionAuthorization(
        authorized=authorized,
        authority="QEX_TEST_AUTHORITY",
        purpose="QEX-SUBSTRATE-01 physical execution boundary test",
        max_shots=max_shots,
        allow_resource_consumption=allow,
    )


def test_physical_execution_requires_explicit_resource_authorization():
    preparation = prepare_physical_qpu(StubPhysicalBackend(), auth(authorized=False))
    assert preparation.decision is Decision.HOLD
    assert "authorization" in preparation.reason


def test_missing_backend_is_hold_even_with_authorization():
    preparation = prepare_physical_qpu(None, auth())
    assert preparation.decision is Decision.HOLD
    assert "unavailable" in preparation.reason


def test_fake_backend_cannot_be_promoted_to_physical_qpu():
    preparation = prepare_physical_qpu(FakeSherbrooke(), auth())
    assert preparation.decision is Decision.HOLD
    assert "simulator/fake" in preparation.reason


def test_physical_preparation_refreshes_and_hashes_calibration():
    backend = StubPhysicalBackend()
    preparation = prepare_physical_qpu(backend, auth())

    assert preparation.decision is Decision.PASS
    assert backend.refresh_values == [True]
    assert preparation.backend_name == "ibm_stub_physical"
    assert preparation.calibration_id == "cal-physical-20260828-2330"
    assert preparation.properties_last_update == "2026-08-28T23:30:00+00:00"
    assert preparation.pending_jobs == 7
    assert len(preparation.calibration_snapshot_hash or "") == 64
    assert len(preparation.authorization_hash or "") == 64


def test_adapter_refuses_shots_above_authorized_budget():
    preparation = prepare_physical_qpu(StubPhysicalBackend(), auth(max_shots=512))
    try:
        PhysicalQPUAdapter(preparation, shots=1024)
    except PhysicalQPUUnavailable as exc:
        assert "exceed" in str(exc)
    else:
        raise AssertionError("adapter exceeded authorized shot budget")


def test_dependency_injected_physical_job_preserves_contract_and_evidence():
    preparation = prepare_physical_qpu(StubPhysicalBackend(), auth(max_shots=1024))
    sampler = StubSampler({"0": 32, "1": 992})
    adapter = PhysicalQPUAdapter(
        preparation,
        shots=1024,
        compiler_factory=lambda _backend: IdentityPassManager(),
        sampler_factory=lambda _backend: sampler,
    )
    contract = qex_substrate_01_contract()

    physical = adapter.execute(contract, {"bit": 0})

    assert physical.execution.contract_hash == contract.contract_hash
    assert physical.execution.result == 1
    assert physical.job_id == "job-physical-qex-001"
    assert physical.raw_counts == {"0": 32, "1": 992}
    assert len(physical.raw_counts_hash) == 64
    assert len(physical.isa_circuit_hash) == 64
    assert len(physical.job_metrics_hash) == 64
    assert len(physical.usage_estimation_hash) == 64
    assert sampler.calls and sampler.calls[0][1] == 1024
    assert physical.execution.backend_id.startswith("physical-qpu-ibm_stub_physical-")


def test_malformed_physical_counts_fail_closed():
    preparation = prepare_physical_qpu(StubPhysicalBackend(), auth(max_shots=128))
    adapter = PhysicalQPUAdapter(
        preparation,
        shots=128,
        compiler_factory=lambda _backend: IdentityPassManager(),
        sampler_factory=lambda _backend: StubSampler({"0": 64, "1": 63}),
    )
    try:
        adapter.execute(qex_substrate_01_contract(), {"bit": 1})
    except PhysicalQPUExecutionFailed as exc:
        assert "does not match" in str(exc)
    else:
        raise AssertionError("malformed physical counts were accepted")
