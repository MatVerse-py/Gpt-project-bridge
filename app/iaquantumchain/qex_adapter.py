from __future__ import annotations

import importlib
import math
import time
from dataclasses import asdict
from typing import Any, Mapping

from app.core import stable_hash


class QeXUnavailable(RuntimeError):
    pass


class QeXAdmissionError(RuntimeError):
    pass


class QeXAdapter:
    """Narrow adapter to the repository's canonical QeX L0 implementation."""

    REQUIRED_MODULES = (
        "app.qex_experiment",
        "app.qex_substrate",
        "app.qex_adapters",
    )

    def __init__(self) -> None:
        self._modules: dict[str, Any] = {}
        self._load_error: str | None = None
        try:
            self._modules = {name: importlib.import_module(name) for name in self.REQUIRED_MODULES}
        except Exception as exc:
            self._load_error = type(exc).__name__

    @property
    def available(self) -> bool:
        return len(self._modules) == len(self.REQUIRED_MODULES)

    def status(self) -> dict[str, Any]:
        return {
            "state": "READY_L0" if self.available else "HOLD",
            "reason": "repository QeX L0 modules imported" if self.available else "QeX modules unavailable",
            "load_error": self._load_error,
            "required_modules": list(self.REQUIRED_MODULES),
            "experiment_id": "QEX-SUBSTRATE-01",
            "automatic_preference_routing": "DISABLED",
        }

    def contract(self) -> Any:
        if not self.available:
            raise QeXUnavailable("QeX modules are unavailable")
        return self._modules["app.qex_experiment"].qex_substrate_01_contract()

    def ontology_admissible(self) -> bool:
        """Validate the frozen L0 problem identity before the Ω-Gate selection call."""
        contract = self.contract()
        expected_problem_hash = stable_hash({"problem": "NOT", "domain": "bit"})
        return (
            contract.experiment_id == "QEX-SUBSTRATE-01"
            and contract.problem_hash == expected_problem_hash
            and "bit_not" in contract.required_capabilities
            and contract.require_classical_baseline is True
        )

    @staticmethod
    def _validate_observable(observable: Mapping[str, Any]) -> None:
        for field in ("probability_0", "probability_1"):
            value = observable.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise QeXAdmissionError(f"{field} must be numeric")
            if not math.isfinite(float(value)) or not 0.0 <= float(value) <= 1.0:
                raise QeXAdmissionError(f"{field} must be finite and in [0,1]")
        total = float(observable["probability_0"]) + float(observable["probability_1"])
        if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-12):
            raise QeXAdmissionError("measurement probabilities must sum to one")
        result = observable.get("result")
        if type(result) is not int or result not in (0, 1):
            raise QeXAdmissionError("result must be integer 0 or 1")

    def execute_l0_matrix(
        self,
        payload: Mapping[str, Any],
        *,
        ontology_ok: bool = False,
        signature_valid: bool = False,
        transition_valid: bool = False,
    ) -> dict[str, Any]:
        if not self.available:
            raise QeXUnavailable("QeX modules are unavailable")
        if set(payload) != {"bit"} or type(payload.get("bit")) is not int or payload["bit"] not in (0, 1):
            raise QeXAdmissionError("L0 accepts exactly one integer field: bit=0|1")

        substrate = self._modules["app.qex_substrate"]
        adapters_module = self._modules["app.qex_adapters"]
        contract = self.contract()
        adapters = (
            adapters_module.ClassicalNotAdapter(),
            adapters_module.IdealStatevectorNotAdapter(),
        )
        profiles = (
            substrate.CapabilityProfile(
                backend_id=adapters[0].backend_id,
                regime=adapters[0].regime,
                capabilities=("bit_not",),
                maturity="REFERENCE",
                available=True,
            ),
            substrate.CapabilityProfile(
                backend_id=adapters[1].backend_id,
                regime=adapters[1].regime,
                capabilities=("bit_not",),
                maturity="SIMULATED",
                available=True,
            ),
        )
        if not contract.require_classical_baseline:
            raise QeXAdmissionError("L0 contract must require a classical baseline")
        selection = substrate.select_substrate(
            contract,
            profiles,
            ontology_ok=ontology_ok,
            signature_valid=signature_valid,
            transition_valid=transition_valid,
        )
        if selection.selected_backend_id is None:
            raise QeXAdmissionError("QeX substrate selection did not pass constitutional admissibility")
        assessments = selection.assessments

        results: list[dict[str, Any]] = []
        for adapter in adapters:
            started = time.perf_counter_ns()
            result = adapter.execute(contract, dict(payload))
            elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000.0
            observable = result.canonical_observable()
            self._validate_observable(observable)
            if result.contract_hash != contract.contract_hash or result.experiment_id != contract.experiment_id:
                raise QeXAdmissionError("backend changed the frozen experiment contract")
            if result.result != 1 - payload["bit"]:
                raise QeXAdmissionError("backend violated the frozen NOT operation")
            if result.backend_id != adapter.backend_id or result.regime is not adapter.regime:
                raise QeXAdmissionError("backend identity or regime mismatch")
            receipt = dict(result.receipt)
            if not isinstance(receipt.get("receipt_hash"), str) or len(receipt["receipt_hash"]) != 64:
                raise QeXAdmissionError("backend receipt is missing a SHA-256 receipt_hash")
            results.append({
                "backend_id": result.backend_id,
                "regime": result.regime.value,
                "execution_class": "CLASSICAL" if result.regime is substrate.ComputeRegime.CLASSICAL else "QUANTUM_SIMULATED",
                "physical_execution": False,
                "elapsed_ms_observed": elapsed_ms,
                "observable": observable,
                "receipt": receipt,
                "backend_payload_hash": result.backend_payload_hash,
                "backend_metadata_hash": result.backend_metadata_hash,
            })

        return {
            "contract": {
                "experiment_id": contract.experiment_id,
                "contract_hash": contract.contract_hash,
                "problem_hash": contract.problem_hash,
                "metric_schema_hash": contract.metric_schema_hash,
                "observable_schema_hash": contract.observable_schema_hash,
                "evidence_policy_hash": contract.evidence_policy_hash,
                "required_capabilities": list(contract.required_capabilities),
                "require_classical_baseline": contract.require_classical_baseline,
            },
            "admission": [asdict(item) for item in assessments],
            "selection_receipt": dict(selection.receipt),
            "routing": {
                "mode": "FIXED_L0_COMPARISON_MATRIX",
                "selected_baseline": selection.selected_backend_id,
                "automatic_preference_routing": False,
                "reason": "L0 compares both substrates; unknown telemetry is not interpreted as zero",
            },
            "results": results,
        }

    def compare(self, left: Mapping[str, Any], right: Mapping[str, Any]) -> str:
        if not self.available:
            raise QeXUnavailable("QeX comparator is unavailable")
        status = self._modules["app.qex_substrate"].compare_substrate_results(
            left,
            right,
            hard_invariants=(
                "experiment_id",
                "contract_hash",
                "problem_hash",
                "metric_schema_hash",
                "observable_schema_hash",
                "evidence_policy_hash",
                "result",
            ),
            numeric_tolerances={"probability_0": 1e-12, "probability_1": 1e-12},
        )
        return status.value
