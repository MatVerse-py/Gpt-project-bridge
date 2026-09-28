from __future__ import annotations

from typing import Any


class PortableStateContractError(RuntimeError):
    pass


def adapt_runtime_result(runtime: dict[str, Any], prereg: dict[str, Any]) -> dict[str, Any]:
    """Convert a fully passing portable-state relay result into the frozen normalized mutation.

    This adapter is intentionally deterministic and fail-closed. It does not inspect raw model text.
    """
    if runtime.get("runtime_pass") is not True:
        raise PortableStateContractError("portable-state runtime did not pass")

    expected = prereg["portable_state_contract"]["state"]
    parsed = runtime.get("parsed_state") or {}
    if parsed != {
        "claims": sorted(expected["claims"]),
        "decision": expected["decision"],
        "safety_gate": expected["safety_gate"],
        "transfer_hidden_reasoning": expected["transfer_hidden_reasoning"],
    }:
        raise PortableStateContractError(f"portable-state mismatch: {parsed!r}")

    mapping = prereg["deterministic_adapter"]["mapping"]
    required = sorted(prereg["deterministic_adapter"]["required_claims"])
    if parsed["claims"] != required:
        raise PortableStateContractError("required claims mismatch")

    mutation_class = mapping["C1"]["mutation_class"]
    confidence = float(mapping["C2"]["confidence"])
    compensating_guard = bool(mapping["C3"]["compensating_guard"])
    return {
        "mutation_class": mutation_class,
        "confidence": confidence,
        "compensating_guard": compensating_guard,
        "adapter_basis": {
            "portable_contract_hash": runtime["contract_hash"],
            "observable_output_hash": runtime["observable_output_hash"],
            "model_id": runtime["model_id"],
            "model_revision": runtime.get("model_revision"),
        },
    }
