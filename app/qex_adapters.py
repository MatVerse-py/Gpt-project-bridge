from __future__ import annotations

from dataclasses import dataclass
from math import isclose
from typing import Any, Mapping

from app.core import stable_hash
from app.evidence import evidence_receipt
from app.qex_substrate import ComputeRegime, ExperimentContract


@dataclass(frozen=True)
class ExecutionResult:
    experiment_id: str
    contract_hash: str
    backend_id: str
    regime: ComputeRegime
    problem_hash: str
    metric_schema_hash: str
    observable_schema_hash: str
    evidence_policy_hash: str
    result: int
    probability_0: float
    probability_1: float
    backend_payload_hash: str
    backend_metadata_hash: str
    receipt: Mapping[str, Any]

    def canonical_observable(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "contract_hash": self.contract_hash,
            "problem_hash": self.problem_hash,
            "metric_schema_hash": self.metric_schema_hash,
            "observable_schema_hash": self.observable_schema_hash,
            "evidence_policy_hash": self.evidence_policy_hash,
            "result": self.result,
            "probability_0": self.probability_0,
            "probability_1": self.probability_1,
        }


class ClassicalNotAdapter:
    backend_id = "cpu-classical-not-v1"
    regime = ComputeRegime.CLASSICAL

    def execute(self, contract: ExperimentContract, payload: Mapping[str, Any]) -> ExecutionResult:
        bit = _validated_bit(payload)
        result = 1 - bit
        p0 = 1.0 if result == 0 else 0.0
        p1 = 1.0 if result == 1 else 0.0
        return _build_result(contract, self.backend_id, self.regime, payload, result, p0, p1)


class IdealStatevectorNotAdapter:
    """Minimal dependency-free ideal one-qubit simulator.

    This is an execution adapter for substrate-invariance testing only. It does
    not represent a physical QPU and does not model device noise, queueing,
    calibration drift, error correction, or quantum advantage.
    """

    backend_id = "ideal-statevector-1q-v1"
    regime = ComputeRegime.QUANTUM_GATE

    def execute(self, contract: ExperimentContract, payload: Mapping[str, Any]) -> ExecutionResult:
        bit = _validated_bit(payload)
        state = (1.0 + 0.0j, 0.0 + 0.0j) if bit == 0 else (0.0 + 0.0j, 1.0 + 0.0j)
        after_x = (state[1], state[0])
        p0 = abs(after_x[0]) ** 2
        p1 = abs(after_x[1]) ** 2
        if not isclose(p0 + p1, 1.0, rel_tol=0.0, abs_tol=1e-12):
            raise RuntimeError("statevector normalization violated")
        result = 0 if p0 >= p1 else 1
        return _build_result(contract, self.backend_id, self.regime, payload, result, p0, p1)


def _validated_bit(payload: Mapping[str, Any]) -> int:
    if set(payload) != {"bit"}:
        raise ValueError("payload must contain exactly one field: bit")
    bit = payload["bit"]
    if type(bit) is not int or bit not in (0, 1):
        raise ValueError("bit must be integer 0 or 1")
    return bit


def _build_result(
    contract: ExperimentContract,
    backend_id: str,
    regime: ComputeRegime,
    payload: Mapping[str, Any],
    result: int,
    p0: float,
    p1: float,
    *,
    backend_metadata: Mapping[str, Any] | None = None,
) -> ExecutionResult:
    metadata = dict(backend_metadata or {})
    backend_payload = {
        "backend_id": backend_id,
        "regime": regime.value,
        "input": dict(payload),
        "result": result,
        "probability_0": p0,
        "probability_1": p1,
        "backend_metadata": metadata,
    }
    receipt = evidence_receipt(
        "QEX_SUBSTRATE_EXECUTION",
        {
            "experiment_id": contract.experiment_id,
            "contract_hash": contract.contract_hash,
            "problem_hash": contract.problem_hash,
            "payload": dict(payload),
            "backend_id": backend_id,
            "backend_metadata_hash": stable_hash(metadata),
        },
        backend_payload,
    )
    return ExecutionResult(
        experiment_id=contract.experiment_id,
        contract_hash=contract.contract_hash,
        backend_id=backend_id,
        regime=regime,
        problem_hash=contract.problem_hash,
        metric_schema_hash=contract.metric_schema_hash,
        observable_schema_hash=contract.observable_schema_hash,
        evidence_policy_hash=contract.evidence_policy_hash,
        result=result,
        probability_0=p0,
        probability_1=p1,
        backend_payload_hash=stable_hash(backend_payload),
        backend_metadata_hash=stable_hash(metadata),
        receipt=receipt,
    )
