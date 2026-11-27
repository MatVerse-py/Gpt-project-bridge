from __future__ import annotations

from typing import Any, Callable, Mapping


class GovernanceUnavailable(RuntimeError):
    pass


class GovernanceAdapter:
    """Uses the canonical app.core HDB and Ω-Gate functions without redefining them."""

    def __init__(
        self,
        *,
        evaluate_hdb_fn: Callable[..., Any] | None = None,
        omega_gate_fn: Callable[..., Any] | None = None,
    ) -> None:
        if evaluate_hdb_fn is None or omega_gate_fn is None:
            try:
                from app.core import evaluate_hdb, omega_gate
            except ImportError as exc:  # pragma: no cover - exercised by integration install
                raise GovernanceUnavailable("canonical app.core governance is unavailable") from exc
            evaluate_hdb_fn = evaluate_hdb
            omega_gate_fn = omega_gate
        self._evaluate_hdb = evaluate_hdb_fn
        self._omega_gate = omega_gate_fn

    def evaluate(
        self,
        *,
        authenticated: bool,
        transition_valid: bool,
        human: Mapping[str, Any] | None = None,
    ) -> dict[str, str]:
        hdb = self._evaluate_hdb(None if human is None else dict(human))
        decision, reason = self._omega_gate(
            hdb=hdb,
            action="EXECUTE",
            ontology_ok=True,
            signature_valid=authenticated,
            transition_valid=transition_valid,
        )
        return {"decision": decision.value, "reason": str(reason)}
