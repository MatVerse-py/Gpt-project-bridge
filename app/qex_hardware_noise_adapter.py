from __future__ import annotations

from importlib.metadata import version
from typing import Any, Mapping

from qiskit import QuantumCircuit, transpile
from qiskit_aer import AerSimulator
from qiskit_aer.noise import NoiseModel
from qiskit_ibm_runtime.fake_provider import FakeSherbrooke

from app.qex_adapters import ExecutionResult, _build_result, _validated_bit
from app.qex_substrate import ComputeRegime, ExperimentContract


class AerHardwareSnapshotNotAdapter:
    """Aer execution using a hardware-derived calibration snapshot.

    The default source is Qiskit Runtime's FakeSherbrooke snapshot. Its noise
    parameters originate from a historical device snapshot bundled with the
    pinned runtime package. This is more realistic than a synthetic scalar
    bit-flip model, but it is still NOT current calibration and NOT physical QPU
    execution.
    """

    backend_id = "aer-fake-sherbrooke-snapshot-v1"
    regime = ComputeRegime.QUANTUM_GATE

    def __init__(self, *, shots: int = 4096, seed: int = 369) -> None:
        if type(shots) is not int or shots <= 0:
            raise ValueError("shots must be a positive integer")
        if type(seed) is not int or seed < 0:
            raise ValueError("seed must be a non-negative integer")

        self.shots = shots
        self.seed = seed
        self.source_backend = FakeSherbrooke()
        self.noise_model = NoiseModel.from_backend(self.source_backend)
        self.simulator = AerSimulator(noise_model=self.noise_model)

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
        job = self.simulator.run(compiled, shots=self.shots, seed_simulator=self.seed)
        counts = job.result().get_counts(compiled)
        unexpected = set(counts) - {"0", "1"}
        if unexpected:
            raise RuntimeError(f"unexpected measurement keys: {sorted(unexpected)}")

        p0 = counts.get("0", 0) / self.shots
        p1 = counts.get("1", 0) / self.shots
        total = p0 + p1
        if abs(total - 1.0) > 1e-12:
            raise RuntimeError("hardware-snapshot probabilities are not normalized")
        result = 0 if p0 >= p1 else 1

        metadata = {
            "source_kind": "HISTORICAL_HARDWARE_SNAPSHOT",
            "source_backend_class": self.source_backend.__class__.__name__,
            "source_backend_name": _backend_name(self.source_backend),
            "shots": self.shots,
            "seed": self.seed,
            "qiskit_version": version("qiskit"),
            "qiskit_aer_version": version("qiskit-aer"),
            "qiskit_ibm_runtime_version": version("qiskit-ibm-runtime"),
            "noise_basis_gates": tuple(sorted(self.noise_model.basis_gates)),
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
