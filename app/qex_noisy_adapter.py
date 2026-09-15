from __future__ import annotations

from typing import Any, Mapping

from app.qex_adapters import ExecutionResult, _build_result, _validated_bit
from app.qex_substrate import ComputeRegime, ExperimentContract


class ControlledBitFlipNoiseNotAdapter:
    """Deterministic hardware-realism proxy for QEX-SUBSTRATE-01.

    Applies the ideal NOT result, then models symmetric classical readout/bit-flip
    noise with a configured probability. This is not a physical QPU and is not a
    device-faithful noise model; it exists to test governed tolerance handling
    before hardware integration.
    """

    regime = ComputeRegime.QUANTUM_GATE

    def __init__(self, error_probability: float = 0.05) -> None:
        if not 0.0 <= error_probability <= 0.5:
            raise ValueError("error_probability must be in [0,0.5]")
        self.error_probability = float(error_probability)
        self.backend_id = f"controlled-bitflip-noise-p{self.error_probability:.6f}-v1"

    def execute(self, contract: ExperimentContract, payload: Mapping[str, Any]) -> ExecutionResult:
        bit = _validated_bit(payload)
        ideal_result = 1 - bit
        e = self.error_probability
        if ideal_result == 0:
            p0, p1 = 1.0 - e, e
        else:
            p0, p1 = e, 1.0 - e
        result = 0 if p0 >= p1 else 1
        return _build_result(contract, self.backend_id, self.regime, payload, result, p0, p1)


def total_variation_distance(left: Mapping[str, Any], right: Mapping[str, Any]) -> float:
    """TVD for the canonical binary outcome distribution."""
    return 0.5 * (
        abs(float(left["probability_0"]) - float(right["probability_0"]))
        + abs(float(left["probability_1"]) - float(right["probability_1"]))
    )
