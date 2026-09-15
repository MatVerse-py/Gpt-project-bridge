from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import version
from io import BytesIO
from hashlib import sha256
from typing import Any, Callable, Mapping

from qiskit import QuantumCircuit, qpy
from qiskit.transpiler import generate_preset_pass_manager
from qiskit_ibm_runtime import SamplerV2

from app.core import Decision, stable_hash
from app.qex_adapters import ExecutionResult, _build_result, _validated_bit
from app.qex_live_calibration_adapter import (
    _backend_name,
    _is_nonlive_backend,
    _normalize_for_hash,
    _properties_last_update,
)
from app.qex_substrate import ComputeRegime, ExperimentContract


@dataclass(frozen=True)
class PhysicalExecutionAuthorization:
    authorized: bool
    authority: str
    purpose: str
    max_shots: int
    allow_resource_consumption: bool

    def __post_init__(self) -> None:
        if not self.authority.strip():
            raise ValueError("authority is required")
        if not self.purpose.strip():
            raise ValueError("purpose is required")
        if type(self.max_shots) is not int or self.max_shots <= 0:
            raise ValueError("max_shots must be a positive integer")

    @property
    def authorization_hash(self) -> str:
        return stable_hash(
            {
                "authorized": self.authorized,
                "authority": self.authority,
                "purpose": self.purpose,
                "max_shots": self.max_shots,
                "allow_resource_consumption": self.allow_resource_consumption,
            }
        )


@dataclass(frozen=True)
class PhysicalQPUPreparation:
    decision: Decision
    reason: str
    backend: Any | None = None
    backend_name: str | None = None
    calibration_id: str | None = None
    calibration_snapshot_hash: str | None = None
    properties_last_update: str | None = None
    pending_jobs: int | None = None
    authorization_hash: str | None = None
    max_shots: int | None = None


@dataclass(frozen=True)
class PhysicalQPUExecution:
    execution: ExecutionResult
    job_id: str
    raw_counts: Mapping[str, int]
    raw_counts_hash: str
    isa_circuit_hash: str
    job_metrics_hash: str
    usage_estimation_hash: str

    def canonical_observable(self) -> dict[str, Any]:
        return self.execution.canonical_observable()


class PhysicalQPUUnavailable(RuntimeError):
    pass


class PhysicalQPUExecutionFailed(RuntimeError):
    pass


def prepare_physical_qpu(
    backend: Any | None,
    authorization: PhysicalExecutionAuthorization,
) -> PhysicalQPUPreparation:
    """Prepare a physical QPU execution boundary without submitting a job.

    A PASS means only that the backend and authorization are admissible for an
    attempted L4 execution. It does not mean a physical job has been submitted
    or completed. Missing credentials/backend, fake/simulator sources,
    non-operational status, absent calibration, or insufficient authorization
    all return HOLD.
    """
    if not authorization.authorized or not authorization.allow_resource_consumption:
        return PhysicalQPUPreparation(
            Decision.HOLD,
            "explicit physical resource-consumption authorization required",
            authorization_hash=authorization.authorization_hash,
            max_shots=authorization.max_shots,
        )
    if backend is None:
        return PhysicalQPUPreparation(
            Decision.HOLD,
            "physical backend unavailable or not authenticated",
            authorization_hash=authorization.authorization_hash,
            max_shots=authorization.max_shots,
        )

    backend_name = _backend_name(backend)
    if _is_nonlive_backend(backend):
        return PhysicalQPUPreparation(
            Decision.HOLD,
            "backend is simulator/fake and cannot satisfy PHYSICAL_QPU_EXECUTION",
            backend_name=backend_name,
            authorization_hash=authorization.authorization_hash,
            max_shots=authorization.max_shots,
        )

    status_method = getattr(backend, "status", None)
    if not callable(status_method):
        return PhysicalQPUPreparation(
            Decision.HOLD,
            "backend status is unavailable",
            backend_name=backend_name,
            authorization_hash=authorization.authorization_hash,
            max_shots=authorization.max_shots,
        )
    try:
        status = status_method()
    except Exception as exc:
        return PhysicalQPUPreparation(
            Decision.HOLD,
            f"backend status query failed: {exc.__class__.__name__}",
            backend_name=backend_name,
            authorization_hash=authorization.authorization_hash,
            max_shots=authorization.max_shots,
        )
    if getattr(status, "operational", None) is not True:
        return PhysicalQPUPreparation(
            Decision.HOLD,
            "backend is not operational",
            backend_name=backend_name,
            authorization_hash=authorization.authorization_hash,
            max_shots=authorization.max_shots,
        )

    properties_method = getattr(backend, "properties", None)
    if not callable(properties_method):
        return PhysicalQPUPreparation(
            Decision.HOLD,
            "backend properties API is unavailable",
            backend_name=backend_name,
            authorization_hash=authorization.authorization_hash,
            max_shots=authorization.max_shots,
        )
    try:
        properties = properties_method(refresh=True)
    except Exception as exc:
        return PhysicalQPUPreparation(
            Decision.HOLD,
            f"physical calibration refresh failed: {exc.__class__.__name__}",
            backend_name=backend_name,
            authorization_hash=authorization.authorization_hash,
            max_shots=authorization.max_shots,
        )
    if properties is None or not callable(getattr(properties, "to_dict", None)):
        return PhysicalQPUPreparation(
            Decision.HOLD,
            "physical calibration properties are unavailable",
            backend_name=backend_name,
            authorization_hash=authorization.authorization_hash,
            max_shots=authorization.max_shots,
        )

    try:
        normalized_properties = _normalize_for_hash(properties.to_dict())
        calibration_id_raw = getattr(backend, "calibration_id", None)
        calibration_id = str(calibration_id_raw) if calibration_id_raw is not None else None
        snapshot = {
            "source_kind": "PHYSICAL_QPU_PRE_EXECUTION_CALIBRATION",
            "backend_name": backend_name,
            "backend_version": str(getattr(backend, "backend_version", "UNKNOWN")),
            "calibration_id": calibration_id,
            "properties": normalized_properties,
        }
        snapshot_hash = stable_hash(snapshot)
    except Exception as exc:
        return PhysicalQPUPreparation(
            Decision.HOLD,
            f"physical calibration snapshot failed: {exc.__class__.__name__}",
            backend_name=backend_name,
            authorization_hash=authorization.authorization_hash,
            max_shots=authorization.max_shots,
        )

    pending_jobs_raw = getattr(status, "pending_jobs", None)
    pending_jobs = pending_jobs_raw if type(pending_jobs_raw) is int else None
    return PhysicalQPUPreparation(
        Decision.PASS,
        "physical backend and execution authorization are admissible",
        backend=backend,
        backend_name=backend_name,
        calibration_id=calibration_id,
        calibration_snapshot_hash=snapshot_hash,
        properties_last_update=_properties_last_update(normalized_properties),
        pending_jobs=pending_jobs,
        authorization_hash=authorization.authorization_hash,
        max_shots=authorization.max_shots,
    )


class PhysicalQPUAdapter:
    """L4 adapter that can submit one frozen QeX circuit to a physical QPU.

    Construction alone never submits work. `execute()` requires a PASS
    preparation and enforces the prepared shot budget. The default runtime path
    uses an ISA circuit produced for the selected backend and SamplerV2 in job
    mode. Tests dependency-inject compiler/sampler doubles, so CI never consumes
    quantum hardware resources.
    """

    regime = ComputeRegime.QUANTUM_GATE

    def __init__(
        self,
        preparation: PhysicalQPUPreparation,
        *,
        shots: int = 1024,
        compiler_factory: Callable[[Any], Any] | None = None,
        sampler_factory: Callable[[Any], Any] | None = None,
    ) -> None:
        if preparation.decision is not Decision.PASS or preparation.backend is None:
            raise PhysicalQPUUnavailable(preparation.reason)
        if type(shots) is not int or shots <= 0:
            raise ValueError("shots must be a positive integer")
        if preparation.max_shots is None or shots > preparation.max_shots:
            raise PhysicalQPUUnavailable("requested shots exceed authorized maximum")

        self.preparation = preparation
        self.backend = preparation.backend
        self.shots = shots
        self.compiler_factory = compiler_factory or (
            lambda backend: generate_preset_pass_manager(backend=backend, optimization_level=1)
        )
        self.sampler_factory = sampler_factory or (lambda backend: SamplerV2(mode=backend))
        name = (preparation.backend_name or "unknown").replace(" ", "_")
        snapshot = preparation.calibration_snapshot_hash or "missing"
        self.backend_id = f"physical-qpu-{name}-{snapshot[:12]}-v1"

    def execute(self, contract: ExperimentContract, payload: Mapping[str, Any]) -> PhysicalQPUExecution:
        bit = _validated_bit(payload)
        circuit = QuantumCircuit(1)
        if bit == 1:
            circuit.x(0)
        circuit.x(0)
        circuit.measure_all()

        try:
            pass_manager = self.compiler_factory(self.backend)
            isa_circuit = pass_manager.run(circuit)
            isa_circuit_hash = _qpy_hash(isa_circuit)
            sampler = self.sampler_factory(self.backend)
            job = sampler.run([isa_circuit], shots=self.shots)
            job_id = str(job.job_id())
            primitive_result = job.result()
            counts = primitive_result[0].data.meas.get_counts()
        except Exception as exc:
            raise PhysicalQPUExecutionFailed(
                f"physical QPU submission/result failed: {exc.__class__.__name__}"
            ) from exc

        normalized_counts = _validated_counts(counts, self.shots)
        p0 = normalized_counts.get("0", 0) / self.shots
        p1 = normalized_counts.get("1", 0) / self.shots
        result = 0 if p0 >= p1 else 1

        job_metrics = _safe_job_mapping(job, "metrics")
        usage_estimation = _normalize_for_hash(getattr(job, "usage_estimation", None))
        raw_counts_hash = stable_hash(normalized_counts)
        job_metrics_hash = stable_hash(job_metrics)
        usage_estimation_hash = stable_hash(usage_estimation)
        metadata = {
            "source_kind": "PHYSICAL_QPU_EXECUTION",
            "execution_kind": "IBM_QUANTUM_SAMPLER_V2_JOB_MODE",
            "backend_name": self.preparation.backend_name,
            "calibration_id": self.preparation.calibration_id,
            "calibration_snapshot_hash": self.preparation.calibration_snapshot_hash,
            "properties_last_update": self.preparation.properties_last_update,
            "authorization_hash": self.preparation.authorization_hash,
            "pending_jobs_before_submission": self.preparation.pending_jobs,
            "job_id": job_id,
            "shots": self.shots,
            "raw_counts": normalized_counts,
            "raw_counts_hash": raw_counts_hash,
            "isa_circuit_hash": isa_circuit_hash,
            "job_metrics": job_metrics,
            "job_metrics_hash": job_metrics_hash,
            "usage_estimation": usage_estimation,
            "usage_estimation_hash": usage_estimation_hash,
            "qiskit_version": version("qiskit"),
            "qiskit_ibm_runtime_version": version("qiskit-ibm-runtime"),
        }
        execution = _build_result(
            contract,
            self.backend_id,
            self.regime,
            payload,
            result,
            p0,
            p1,
            backend_metadata=metadata,
        )
        return PhysicalQPUExecution(
            execution=execution,
            job_id=job_id,
            raw_counts=normalized_counts,
            raw_counts_hash=raw_counts_hash,
            isa_circuit_hash=isa_circuit_hash,
            job_metrics_hash=job_metrics_hash,
            usage_estimation_hash=usage_estimation_hash,
        )


def _validated_counts(counts: Any, shots: int) -> dict[str, int]:
    if not isinstance(counts, Mapping):
        raise PhysicalQPUExecutionFailed("physical result counts are not a mapping")
    normalized: dict[str, int] = {}
    for key, value in counts.items():
        bitstring = str(key)
        if bitstring not in {"0", "1"}:
            raise PhysicalQPUExecutionFailed(f"unexpected physical measurement key: {bitstring}")
        if type(value) is not int or value < 0:
            raise PhysicalQPUExecutionFailed("physical result count must be a non-negative integer")
        normalized[bitstring] = value
    if sum(normalized.values()) != shots:
        raise PhysicalQPUExecutionFailed("physical result count total does not match requested shots")
    return dict(sorted(normalized.items()))


def _qpy_hash(circuit: QuantumCircuit) -> str:
    buffer = BytesIO()
    qpy.dump(circuit, buffer)
    return sha256(buffer.getvalue()).hexdigest()


def _safe_job_mapping(job: Any, method_name: str) -> Any:
    method = getattr(job, method_name, None)
    if not callable(method):
        return None
    try:
        return _normalize_for_hash(method())
    except Exception:
        return None
