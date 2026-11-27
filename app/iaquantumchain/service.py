from __future__ import annotations

import uuid
from typing import Any, Mapping

from .contracts import EXPERIMENT_ID, InvalidExperiment, validate_submission
from .governance import GovernanceAdapter
from .iachain import IAChainAdapter
from .qex_adapter import QeXAdapter, QeXUnavailable
from .superblock import SuperblockLedgerAdapter
from .tsc import TSCAdapter


class IAQuantumChainService:
    """Local orchestration over the existing Bridge, Ω-Gate, QeX and shared ledger."""

    def __init__(
        self,
        *,
        qex: Any | None = None,
        iachain: Any | None = None,
        governance: Any | None = None,
        superblock: Any | None = None,
        tsc: Any | None = None,
    ) -> None:
        self.qex = qex or QeXAdapter()
        self.iachain = iachain or IAChainAdapter()
        self.governance = governance or GovernanceAdapter()
        self.superblock = superblock or SuperblockLedgerAdapter()
        self.tsc = tsc or TSCAdapter()

    def status(self, *, public: bool = False) -> dict[str, Any]:
        modules = {
            "IAChain": {"state": "READY", "provider": "app.bridge_core.CausalEnvelope"},
            "TSC": {"state": "READY_L0", "provider": "QeX comparator with finite-value checks"},
            "QeX": self.qex.status(),
            "CapabilityService": {"state": "READY", "provider": "existing app.auth HMAC + SQLite nonce consumption"},
            "Superblock": {"state": "READY_LOCAL_LEDGER", "provider": "app.storage shared append-only SQLite ledger"},
            "PQC": {"state": "HOLD", "reason": "production PQC adapter is not integrated"},
            "PhysicalQPU": {"state": "HOLD", "reason": "no authenticated physical provider job is configured"},
            "CassandralLM": {"state": "NOT_CONNECTED", "reason": "no model provider is configured"},
        }
        if public:
            return {
                "service": "MATVERSE_IAQUANTUMCHAIN_CORE",
                "version": "0.1.0",
                "scope": "LOCAL_INTEGRATION_CORE",
                "modules": modules,
            }
        return {
            "service": "MATVERSE_IAQUANTUMCHAIN_CORE",
            "version": "0.1.0",
            "scope": "LOCAL_INTEGRATION_CORE",
            "experiment": EXPERIMENT_ID,
            "modules": modules,
            "execution_policy": {
                "allowed_experiment": EXPERIMENT_ID,
                "arbitrary_code_execution": False,
                "automatic_backend_preference_routing": False,
                "physical_qpu_submission": False,
                "onchain_transaction_submission": False,
            },
        }

    def submit(
        self,
        *,
        principal_id: str,
        authenticated: bool,
        experiment_id: str,
        payload: Mapping[str, Any],
        request_id: str,
    ) -> dict[str, Any]:
        try:
            submission = validate_submission(
                experiment_id=experiment_id,
                payload=payload,
                request_id=request_id,
            )
        except InvalidExperiment as exc:
            return {"decision": "BLOCK", "status": "INVALID_REQUEST", "reason": str(exc)}

        run_id = str(uuid.uuid4())
        contract_hash = self.qex.contract().contract_hash if self.qex.available else None
        try:
            envelope = self.iachain.build_experiment_envelope(
                principal_id=principal_id,
                request_id=submission["request_id"],
                experiment_id=submission["experiment_id"],
                contract_hash=contract_hash,
                payload=submission["payload"],
            )
        except Exception as exc:
            return {"run_id": run_id, "decision": "HOLD", "status": "IA_CHAIN_UNAVAILABLE", "reason": type(exc).__name__}

        gate = self.governance.evaluate(authenticated=authenticated, transition_valid=True, human=None)
        ingress = self.superblock.append({
            "event_type": "IAQUANTUMCHAIN_INGRESS",
            "run_id": run_id,
            "request_id": submission["request_id"],
            "principal_id": principal_id,
            "experiment_id": experiment_id,
            "contract_hash": contract_hash,
            "envelope_id": envelope.get("envelope_id"),
            "decision_reason": gate["reason"],
        }, gate["decision"])

        if gate["decision"] != "PASS":
            return {
                "run_id": run_id,
                "decision": gate["decision"],
                "status": "GOVERNANCE_HELD",
                "reason": gate["reason"],
                "envelope": envelope,
                "ledger": ingress,
            }

        if not self.qex.available:
            reason = self.qex.status().get("reason", "QeX unavailable")
            ledger = self.superblock.append({
                "event_type": "IAQUANTUMCHAIN_EXECUTION_HELD",
                "run_id": run_id,
                "decision_reason": reason,
            }, "HOLD")
            return {"run_id": run_id, "decision": "HOLD", "status": "QEX_NOT_INSTALLED", "reason": reason, "envelope": envelope, "ledger": ledger}

        try:
            matrix = self.qex.execute_l0_matrix(
                submission["payload"],
                ontology_ok=self.qex.ontology_admissible(),
                signature_valid=authenticated,
                transition_valid=(gate["decision"] == "PASS"),
            )
            items = matrix["results"]
            if len(items) != 2:
                raise ValueError("L0 must return exactly two substrate results")
            comparison = self.tsc.compare(
                left=items[0]["observable"],
                right=items[1]["observable"],
                comparator=self.qex.compare,
            )
            comparison_status = comparison["status"]
            decision = "PASS" if comparison_status in {"EXACT", "FUNCTIONALLY_EQUIVALENT", "WITHIN_TOLERANCE"} else "BLOCK"
            ledger = self.superblock.append({
                "event_type": "IAQUANTUMCHAIN_EXECUTION_COMPLETED",
                "run_id": run_id,
                "request_id": submission["request_id"],
                "experiment_id": experiment_id,
                "contract_hash": matrix["contract"]["contract_hash"],
                "backend_ids": [item["backend_id"] for item in items],
                "tsc_status": comparison_status,
                "tsc_preserved": comparison["preserved"],
                "tsc_approximated": comparison["approximated"],
                "tsc_lost": comparison["lost"],
                "comparison_receipt_hash": comparison["receipt"]["receipt_hash"],
            }, decision)
            return {
                "run_id": run_id,
                "decision": decision,
                "status": "EXECUTED_L0",
                "envelope": envelope,
                "contract": matrix["contract"],
                "admission": matrix["admission"],
                "routing": matrix["routing"],
                "results": items,
                "tsc": comparison,
                "ledger": ledger,
                "execution_boundary": {
                    "regime": "CLASSICAL_AND_IDEAL_STATEVECTOR",
                    "physical_qpu": "NOT_EXECUTED",
                    "quantum_advantage": "NOT_CLAIMED",
                },
            }
        except QeXUnavailable as exc:
            reason = type(exc).__name__
            ledger = self.superblock.append({"event_type": "IAQUANTUMCHAIN_EXECUTION_HELD", "run_id": run_id, "decision_reason": reason}, "HOLD")
            return {"run_id": run_id, "decision": "HOLD", "status": "QEX_UNAVAILABLE", "reason": reason, "ledger": ledger}
        except Exception as exc:
            reason = type(exc).__name__
            ledger = self.superblock.append({"event_type": "IAQUANTUMCHAIN_EXECUTION_HELD", "run_id": run_id, "decision_reason": reason}, "HOLD")
            return {"run_id": run_id, "decision": "HOLD", "status": "EXECUTION_ERROR", "reason": reason, "ledger": ledger}

    def get_run(self, run_id: str) -> dict[str, Any]:
        events = self.superblock.events_for_run(run_id)
        if not events:
            return {"found": False, "run_id": run_id}
        return {"found": True, "run_id": run_id, "events": events}

    def verify_ledger(self) -> dict[str, Any]:
        return self.superblock.verify()
