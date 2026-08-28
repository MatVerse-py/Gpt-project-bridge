from datetime import datetime, timezone
from types import SimpleNamespace

from app.core import Decision
from app.qex_ibm_runtime_entrypoint import (
    outcome_to_public_dict,
    resolve_ibm_runtime_backend,
    run_physical_qpu_from_runtime,
)


class RuntimeStubProperties:
    def to_dict(self):
        return {
            "backend_name": "ibm_runtime_stub",
            "backend_version": "3.0.0",
            "last_update_date": datetime(2026, 8, 28, 23, 40, tzinfo=timezone.utc),
            "qubits": [[{"name": "T1", "value": 110.0, "unit": "us"}]],
            "gates": [],
            "general": [],
        }


class RuntimeStubBackend:
    name = "ibm_runtime_stub"
    backend_version = "3.0.0"
    calibration_id = "cal-runtime-20260828-2340"
    simulator = False

    def status(self):
        return SimpleNamespace(operational=True, pending_jobs=3)

    def properties(self, refresh=False):
        assert refresh is True
        return RuntimeStubProperties()


class RuntimeStubService:
    def __init__(self, backend=None):
        self._backend = backend or RuntimeStubBackend()
        self.backend_calls = []
        self.least_busy_calls = []

    def backend(self, **kwargs):
        self.backend_calls.append(kwargs)
        return self._backend

    def least_busy(self, **kwargs):
        self.least_busy_calls.append(kwargs)
        return self._backend

    def active_instance(self):
        return "crn:v1:qex-test-instance"


class ServiceFactory:
    def __init__(self, service=None, error=None):
        self.service = service or RuntimeStubService()
        self.error = error
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.service


class IdentityPassManager:
    def run(self, circuit):
        return circuit


class StubMeasurements:
    def __init__(self, counts):
        self._counts = counts

    def get_counts(self):
        return dict(self._counts)


class StubRuntimeJob:
    usage_estimation = {"quantum_seconds": 0.01}

    def job_id(self):
        return "runtime-real-path-double-job-001"

    def result(self):
        data = SimpleNamespace(meas=StubMeasurements({"0": 1, "1": 31}))
        return [SimpleNamespace(data=data)]

    def metrics(self):
        return {"usage": {"quantum_seconds": 0.01}}


class StubSampler:
    def __init__(self):
        self.calls = []

    def run(self, pubs, *, shots=None):
        self.calls.append((pubs, shots))
        return StubRuntimeJob()


def test_runtime_authentication_failure_is_hold():
    factory = ServiceFactory(error=RuntimeError("no account"))
    resolution = resolve_ibm_runtime_backend(service_factory=factory)
    assert resolution.decision is Decision.HOLD
    assert "initialization failed" in resolution.reason


def test_runtime_resolution_uses_named_backend_without_persisting_token():
    service = RuntimeStubService()
    factory = ServiceFactory(service=service)
    resolution = resolve_ibm_runtime_backend(
        backend_name="ibm_runtime_stub",
        token="SECRET-IN-MEMORY-ONLY",
        instance="crn:v1:qex-test-instance",
        service_factory=factory,
    )

    assert resolution.decision is Decision.PASS
    assert resolution.backend_name == "ibm_runtime_stub"
    assert service.backend_calls == [{"name": "ibm_runtime_stub", "instance": "crn:v1:qex-test-instance"}]
    assert factory.calls[0]["channel"] == "ibm_quantum_platform"
    assert factory.calls[0]["token"] == "SECRET-IN-MEMORY-ONLY"
    assert resolution.active_instance == "crn:v1:qex-test-instance"


def test_runtime_resolution_can_choose_least_busy_physical_backend():
    service = RuntimeStubService()
    resolution = resolve_ibm_runtime_backend(service_factory=ServiceFactory(service=service))
    assert resolution.decision is Decision.PASS
    assert service.least_busy_calls == [{"min_num_qubits": 1, "operational": True, "simulator": False}]


def test_missing_execute_signal_never_reaches_sampler():
    sampler = StubSampler()
    outcome = run_physical_qpu_from_runtime(
        authority="QEX_RUNTIME_TEST",
        purpose="verify explicit physical execution gate",
        shots=32,
        execute=False,
        allow_resource_consumption=True,
        service_factory=ServiceFactory(),
        compiler_factory=lambda _backend: IdentityPassManager(),
        sampler_factory=lambda _backend: sampler,
    )
    assert outcome.decision is Decision.HOLD
    assert outcome.stage == "PHYSICAL_PREPARATION"
    assert sampler.calls == []


def test_missing_resource_consumption_signal_never_reaches_sampler():
    sampler = StubSampler()
    outcome = run_physical_qpu_from_runtime(
        authority="QEX_RUNTIME_TEST",
        purpose="verify explicit resource-consumption gate",
        shots=32,
        execute=True,
        allow_resource_consumption=False,
        service_factory=ServiceFactory(),
        compiler_factory=lambda _backend: IdentityPassManager(),
        sampler_factory=lambda _backend: sampler,
    )
    assert outcome.decision is Decision.HOLD
    assert sampler.calls == []


def test_explicit_runtime_execution_path_captures_job_evidence_without_secret_output():
    sampler = StubSampler()
    token = "NEVER-EMIT-THIS-TOKEN"
    outcome = run_physical_qpu_from_runtime(
        authority="QEX_RUNTIME_TEST",
        purpose="exercise physical provider path with dependency-injected non-resource double",
        shots=32,
        bit=0,
        execute=True,
        allow_resource_consumption=True,
        backend_name="ibm_runtime_stub",
        token=token,
        service_factory=ServiceFactory(),
        compiler_factory=lambda _backend: IdentityPassManager(),
        sampler_factory=lambda _backend: sampler,
    )

    assert outcome.decision is Decision.PASS
    assert outcome.stage == "PHYSICAL_RESULT_OBSERVED"
    assert outcome.execution is not None
    assert outcome.execution.job_id == "runtime-real-path-double-job-001"
    assert outcome.execution.raw_counts == {"0": 1, "1": 31}
    assert sampler.calls and sampler.calls[0][1] == 32

    public = outcome_to_public_dict(outcome)
    rendered = str(public)
    assert token not in rendered
    assert public["execution"]["job_id"] == "runtime-real-path-double-job-001"
    assert len(public["execution"]["receipt_hash"]) == 64
    assert len(public["execution"]["contract_hash"]) == 64
