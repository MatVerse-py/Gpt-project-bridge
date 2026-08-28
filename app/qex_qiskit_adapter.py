from __future__ import annotations

from math import isclose
from typing import Any, Mapping

from qiskit import QuantumCircuit
from qiskit.quantum_info import Statevector

from app.qex_adapters import ExecutionResult, _build_result, _validated_bit
from app.qex_substrate import ComputeRegime, ExperimentContract


class QiskitStatevectorNotAdapter:
    """Qiskit SDK statevector adapter for QEX-SUBSTRATE-01.

    This adapter exercises an independent external quantum SDK while preserving
    the frozen MatVerse experiment contract. It is still simulation: no IBM
    Quantum account, cloud service, or physical QPU is used.
    """

    backend_id = "qiskit-2.5-statevector-1q-v1"
    regime = ComputeRegime.QUANTUM_GATE

    def execute(self, contract: ExperimentContract, payload: Mapping[str, Any]) -> ExecutionResult:
        bit = _validated_bit(payload)
        circuit = QuantumCircuit(1)
        if bit == 1:
            circuit.x(0)
        circuit.x(0)

        probabilities = Statevector.from_instruction(circuit).probabilities()
        p0 = float(probabilities[0])
        p1 = float(probabilities[1])
        if not isclose(p0 + p1, 1.0, rel_tol=0.0, abs_tol=1e-12):
            raise RuntimeError("Qiskit statevector normalization violated")
        result = 0 if p0 >= p1 else 1
        return _build_result(contract, self.backend_id, self.regime, payload, result, p0, p1)
