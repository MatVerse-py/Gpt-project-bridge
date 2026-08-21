from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel, Field
from typing import Any
import uuid

from .core import Decision, POState, evaluate_hdb, omega_gate
from .storage import append_event, verify_chain, replay, read_ledger

app = FastAPI(title="MATVERSE_REAL_v1", version="1.0.0-bootstrap")


class HumanData(BaseModel):
    consent: bool | None = None
    purpose: str | None = None
    sensitivity: str | None = None
    third_party: bool = False
    third_party_consent: bool = False
    serialize_human: bool = False


class Ingress(BaseModel):
    payload: dict[str, Any]
    action: str = "EXECUTE"
    human: HumanData | None = None
    ontology_ok: bool = True
    signature_valid: bool = True
    transition_valid: bool = True
    source: str = "external"
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))


@app.get("/health")
def health():
    return {"status": "ok", "service": "MATVERSE_REAL_v1"}


@app.get("/ready")
def ready():
    chain = verify_chain()
    return {"ready": bool(chain.get("ok")), "ledger": chain}


@app.post("/bridge/ingress")
def ingress(req: Ingress):
    po_state = POState.EVALUATING
    hdb = evaluate_hdb(req.human.model_dump() if req.human else None)
    decision, reason = omega_gate(
        hdb=hdb,
        action=req.action,
        ontology_ok=req.ontology_ok,
        signature_valid=req.signature_valid,
        transition_valid=req.transition_valid,
    )
    po_state = POState.PASS if decision is Decision.PASS else POState.FAIL if decision is Decision.BLOCK else POState.EVALUATING
    event = {
        "request_id": req.request_id,
        "source": req.source,
        "action": req.action,
        "payload": req.payload,
        "hdb": {"decision": hdb.decision.value, "reason": hdb.reason},
        "gate": {"decision": decision.value, "reason": reason},
        "po_state": po_state.value,
    }
    receipt = append_event(event, decision.value)
    return {
        "request_id": req.request_id,
        "decision": decision.value,
        "reason": reason,
        "po_state": po_state.value,
        "executed": decision is Decision.PASS,
        "quarantined": decision is not Decision.PASS,
        "receipt": receipt,
    }


@app.get("/ledger")
def ledger():
    rows = read_ledger()
    return {"events": rows, "integrity": verify_chain()}


@app.get("/replay")
def replay_endpoint():
    return {"integrity": verify_chain(), "replayed_state": replay()}


@app.get("/world-real")
def world_real():
    chain = verify_chain()
    criteria = {
        "endpoint_public_live": False,
        "persistent_storage": True,
        "runtime_integrated": True,
        "fail_closed_enforcement": True,
        "replay_demonstrated": bool(chain.get("ok")),
        "observability": True,
        "ci_green": False,
        "external_reproduction": False,
    }
    return {"status": "WORLD_REAL" if all(criteria.values()) else "PENDING", "criteria": criteria}
