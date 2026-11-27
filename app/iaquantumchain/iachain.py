from __future__ import annotations

from typing import Any, Mapping


class IAChainUnavailable(RuntimeError):
    pass


class IAChainAdapter:
    """Adapter to the canonical MatVerse causal-envelope implementation.

    It does not redefine the Bridge protocol. It calls app.bridge_core and
    serializes the existing CausalEnvelope for the QeX handoff.
    """

    def __init__(self) -> None:
        try:
            from app.bridge_core import (
                AnalyticStatus,
                EpistemicNature,
                build_envelope,
            )
        except ImportError as exc:  # pragma: no cover - exercised by integration install
            raise IAChainUnavailable("app.bridge_core is not available") from exc
        self._builder = build_envelope
        self._epistemic_nature = EpistemicNature
        self._analytic_status = AnalyticStatus

    def build_experiment_envelope(
        self,
        *,
        principal_id: str,
        request_id: str,
        experiment_id: str,
        contract_hash: str | None,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        envelope = self._builder(
            source="iachain",
            destination="qex",
            actor=principal_id,
            objective_id=experiment_id,
            capability="iaquantumchain:experiment:submit",
            payload={
                "experiment_id": experiment_id,
                "contract_hash": contract_hash,
                "experiment_payload": dict(payload),
            },
            schema="matverse.iaquantumchain.experiment-envelope.v1",
            authority_ref=f"principal:{principal_id}",
            policy_ref="matverse.policy.iaquantumchain.v1",
            correlation_id=request_id,
            epistemic_nature=self._epistemic_nature.RUNTIME,
            analytic_status=self._analytic_status.FACT,
            ttl_seconds=300,
        )
        return envelope.as_dict()
