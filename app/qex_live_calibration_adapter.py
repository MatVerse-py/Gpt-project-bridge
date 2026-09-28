from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from importlib.metadata import version
from typing import Any, Callable, Mapping

from qiskit import QuantumCircuit, transpile
from qiskit_aer import AerSimulator

from app.core import Decision, stable_hash
from app.qex_adapters import ExecutionResult, _build_result, _validated_bit
from app.qex_substrate import ComputeRegime, ExperimentContract


@dataclass(frozen=True)
class LiveCalibrationPreparation:
    decision: Decision
    reason: str
    backend: Any | None = None
    simulator: Any | None = None
    backend_name: str | None = None
    calibration_id: str | None = None
    calibration_snapshot_hash: str | None = None
    properties_last_update: str | None = None


class LiveCalibrationUnavailable(RuntimeError):
    pass


def prepare_live_calibration(
    backend: Any | None,
    *,
    simulator_factory: Callable[[Any], Any] | None = None,
) -> LiveCalibrationPreparation:
    """Bind a current backend calibration to a frozen local Aer simulator.

    This function is deliberately fail-closed. Missing credentials are expected
    to surface to callers as `backend is None`, while unavailable properties,
    non-operational devices, simulators, fake backends, or snapshot failures all
    produce HOLD rather than silently degrading to an ideal/synthetic model.
    """
    if backend is None:
        return LiveCalibrationPreparation(Decision.HOLD, "live backend unavailable or not authenticated")

    backend_name = _backend_name(backend)
    if _is_nonlive_backend(backend):
        return LiveCalibrationPreparation(
            Decision.HOLD,
            "backend is simulator/fake and cannot satisfy LIVE_CALIBRATION",
            backend_name=backend_name,
        )

    status_method = getattr(backend, "status", None)
    if not callable(status_method):
        return LiveCalibrationPreparation(
            Decision.HOLD,
            "backend status is unavailable",
            backend_name=backend_name,
        )
    try:
        status = status_method()
    except Exception as exc:  # provider/network boundary; convert to governed HOLD
        return LiveCalibrationPreparation(
            Decision.HOLD,
            f"backend status query failed: {exc.__class__.__name__}",
            backend_name=backend_name,
        )
    if getattr(status, "operational", None) is not True:
        return LiveCalibrationPreparation(
            Decision.HOLD,
            "backend is not operational",
            backend_name=backend_name,
        )

    properties_method = getattr(backend, "properties", None)
    if not callable(properties_method):
        return LiveCalibrationPreparation(
            Decision.HOLD,
            "backend properties API is unavailable",
            backend_name=backend_name,
        )
    try:
        properties = properties_method(refresh=True)
    except Exception as exc:
        return LiveCalibrationPreparation(
            Decision.HOLD,
            f"live calibration refresh failed: {exc.__class__.__name__}",
            backend_name=backend_name,
        )
    if properties is None or not callable(getattr(properties, "to_dict", None)):
        return LiveCalibrationPreparation(
            Decision.HOLD,
            "live calibration properties are unavailable",
            backend_name=backend_name,
        )

    try:
        normalized_properties = _normalize_for_hash(properties.to_dict())
        calibration_id_raw = getattr(backend, "calibration_id", None)
        calibration_id = str(calibration_id_raw) if calibration_id_raw is not None else None
        snapshot = {
            "source_kind": "LIVE_BACKEND_CALIBRATION",
            "backend_name": backend_name,
            "backend_version": str(getattr(backend, "backend_version", "UNKNOWN")),
            "calibration_id": calibration_id,
            "properties": normalized_properties,
        }
        snapshot_hash = stable_hash(snapshot)
        factory = simulator_factory or AerSimulator.from_backend
        simulator = factory(backend)
    except Exception as exc:
        return LiveCalibrationPreparation(
            Decision.HOLD,
            f"calibration snapshot binding failed: {exc.__class__.__name__}",
            backend_name=backend_name,
        )

    return LiveCalibrationPreparation(
        Decision.PASS,
        "live calibration captured and bound to local simulator",
        backend=backend,
        simulator=simulator,
        backend_name=backend_name,
        calibration_id=calibration_id,
        calibration_snapshot_hash=snapshot_hash,
        properties_last_update=_properties_last_update(normalized_properties),
    )


class LiveCalibrationAerAdapter:
    """L3 QeX adapter: local Aer replay bound to a live calibration snapshot.

    The adapter never submits a workload to the physical QPU. It uses a live
    provider backend only to capture current calibration/configuration and then
    executes locally in Aer. Physical execution remains a separate L4 gate.
    """

    regime = ComputeRegime.QUANTUM_GATE

    def __init__(
        self,
        preparation: LiveCalibrationPreparation,
        *,
        shots: int = 4096,
        seed: int = 369,
    ) -> None:
        if preparation.decision is not Decision.PASS or preparation.simulator is None:
            raise LiveCalibrationUnavailable(preparation.reason)
        if type(shots) is not int or shots <= 0:
            raise ValueError("shots must be a positive integer")
        if type(seed) is not int or seed < 0:
            raise ValueError("seed must be a non-negative integer")

        self.preparation = preparation
        self.simulator = preparation.simulator
        self.shots = shots
        self.seed = seed
        snapshot = preparation.calibration_snapshot_hash or "missing"
        name = (preparation.backend_name or "unknown").replace(" ", "_")
        self.backend_id = f"aer-live-calibration-{name}-{snapshot[:12]}-v1"

    def execute(self, contract: ExperimentContract, payload: Mapping[str, Any]) -> ExecutionResult:
        bit = _validated_bit(payload)
        circuit = QuantumCircuit(1, 1)
        if bit == 1:
            circuit.x(0)
        circuit.x(0)
        circuit.measure(0, 0)

        compiled = transpile(
            circuit,
            self.simulator,
            optimization_level=0,
            seed_transpiler=self.seed,
        )
        result_obj = self.simulator.run(compiled, shots=self.shots, seed_simulator=self.seed).result()
        counts = result_obj.get_counts(compiled)
        unexpected = set(counts) - {"0", "1"}
        if unexpected:
            raise RuntimeError(f"unexpected measurement keys: {sorted(unexpected)}")

        p0 = counts.get("0", 0) / self.shots
        p1 = counts.get("1", 0) / self.shots
        if abs((p0 + p1) - 1.0) > 1e-12:
            raise RuntimeError("live-calibration probabilities are not normalized")
        result = 0 if p0 >= p1 else 1

        metadata = {
            "source_kind": "LIVE_BACKEND_CALIBRATION",
            "execution_kind": "LOCAL_AER_REPLAY",
            "backend_name": self.preparation.backend_name,
            "calibration_id": self.preparation.calibration_id,
            "calibration_snapshot_hash": self.preparation.calibration_snapshot_hash,
            "properties_last_update": self.preparation.properties_last_update,
            "shots": self.shots,
            "seed": self.seed,
            "qiskit_version": version("qiskit"),
            "qiskit_aer_version": version("qiskit-aer"),
            "qiskit_ibm_runtime_version": version("qiskit-ibm-runtime"),
        }
        return _build_result(
            contract,
            self.backend_id,
            self.regime,
            payload,
            result,
            p0,
            p1,
            backend_metadata=metadata,
        )


def _backend_name(backend: Any) -> str:
    name = getattr(backend, "name", backend.__class__.__name__)
    return str(name() if callable(name) else name)


def _is_nonlive_backend(backend: Any) -> bool:
    class_name = backend.__class__.__name__.lower()
    module_name = backend.__class__.__module__.lower()
    if class_name.startswith("fake") or "fake_provider" in module_name:
        return True
    return getattr(backend, "simulator", False) is True


def _properties_last_update(properties: Mapping[str, Any]) -> str | None:
    value = properties.get("last_update_date")
    return str(value) if value is not None else None


def _normalize_for_hash(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, complex):
        return {"real": value.real, "imag": value.imag}
    if isinstance(value, Mapping):
        return {str(k): _normalize_for_hash(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize_for_hash(v) for v in value]
    if isinstance(value, set):
        return sorted((_normalize_for_hash(v) for v in value), key=repr)

    item = getattr(value, "item", None)
    if callable(item):
        try:
            return _normalize_for_hash(item())
        except Exception:
            pass
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        try:
            return _normalize_for_hash(tolist())
        except Exception:
            pass
    return str(value)
