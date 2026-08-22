from __future__ import annotations

from typing import Any, Literal
import uuid

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, model_validator

from .core import Decision, POState, evaluate_hdb, omega_gate, stable_hash
from .model_bridge import (
    PROTOCOL_VERSION,
    assert_transferable_state,
    compare_invariants,
    contract_hash,
)
from .storage import (
    acknowledge_model_handoff,
    append_event,
    append_model_handoff,
    create_model_session,
    get_model_session,
    list_model_inbox,
    read_ledger,
    replay,
    verify_chain,
)

app = FastAPI(title="MATVERSE_REAL_v1", version="1.1.0-cross-model")

SHA256_PATTERN = r"^[0-9a-fA-F]{64}$"
PARTICIPANT_PATTERN = r"^[A-Za-z0-9._:-]{1,128}$"


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


class ModelRef(BaseModel):
    participant_id: str = Field(pattern=PARTICIPANT_PATTERN)
    provider: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=256)
    revision: str | None = Field(default=None, max_length=256)
    endpoint_class: Literal["LOCAL", "REMOTE", "UNKNOWN"] = "UNKNOWN"


class FrozenContract(BaseModel):
    ontology_hash: str = Field(pattern=SHA256_PATTERN)
    policy_hash: str = Field(pattern=SHA256_PATTERN)
    task_hash: str = Field(pattern=SHA256_PATTERN)
    rubric_hash: str = Field(pattern=SHA256_PATTERN)
    memory_policy_hash: str = Field(pattern=SHA256_PATTERN)


class ModelSessionCreate(BaseModel):
    participants: list[ModelRef] = Field(min_length=2, max_length=16)
    contract: FrozenContract
    ontology_ok: bool = True
    signature_valid: bool = True
    transition_valid: bool = True

    @model_validator(mode="after")
    def validate_participants(self) -> "ModelSessionCreate":
        participant_ids = [item.participant_id for item in self.participants]
        if len(set(participant_ids)) != len(participant_ids):
            raise ValueError("participant_id values must be unique")
        model_identities = {(item.provider, item.model, item.revision) for item in self.participants}
        if len(model_identities) < 2:
            raise ValueError("cross-model session requires at least two distinct model identities")
        return self


class ModelHandoffCreate(BaseModel):
    from_participant: str = Field(pattern=PARTICIPANT_PATTERN)
    to_participant: str = Field(pattern=PARTICIPANT_PATTERN)
    parent_handoff_id: str | None = Field(default=None, max_length=128)
    expected_contract_hash: str = Field(pattern=SHA256_PATTERN)
    payload: dict[str, Any]
    human: HumanData | None = None
    ontology_ok: bool = True
    signature_valid: bool = True
    transition_valid: bool = True


class ModelHandoffAck(BaseModel):
    participant_id: str = Field(pattern=PARTICIPANT_PATTERN)


class InvariantRule(BaseModel):
    path: str = Field(min_length=1, max_length=256)
    mode: Literal["exact", "set_equal", "type_equal"] = "exact"


class CrossModelCompare(BaseModel):
    left: dict[str, Any]
    right: dict[str, Any]
    rules: list[InvariantRule] = Field(min_length=1, max_length=128)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "MATVERSE_REAL_v1",
        "model_bridge_protocol": PROTOCOL_VERSION,
    }


@app.get("/ready")
def ready() -> dict[str, Any]:
    chain = verify_chain()
    return {"ready": bool(chain.get("ok")), "ledger": chain}


@app.post("/bridge/ingress")
def ingress(req: Ingress) -> dict[str, Any]:
    hdb = evaluate_hdb(req.human.model_dump() if req.human else None)
    decision, reason = omega_gate(
        hdb=hdb,
        action=req.action,
        ontology_ok=req.ontology_ok,
        signature_valid=req.signature_valid,
        transition_valid=req.transition_valid,
    )
    po_state = (
        POState.PASS
        if decision is Decision.PASS
        else POState.FAIL
        if decision is Decision.BLOCK
        else POState.EVALUATING
    )

    payload_hash = stable_hash(req.payload)
    retain_raw_payload = decision is Decision.PASS and req.human is None
    event = {
        "request_id": req.request_id,
        "source": req.source,
        "action": req.action,
        "payload_hash": payload_hash,
        "payload_retained": retain_raw_payload,
        "hdb": {"decision": hdb.decision.value, "reason": hdb.reason},
        "gate": {"decision": decision.value, "reason": reason},
        "po_state": po_state.value,
    }
    if retain_raw_payload:
        event["payload"] = req.payload

    receipt = append_event(event, decision.value)
    return {
        "request_id": req.request_id,
        "decision": decision.value,
        "reason": reason,
        "po_state": po_state.value,
        "executed": decision is Decision.PASS,
        "quarantined": decision is not Decision.PASS,
        "payload_hash": payload_hash,
        "payload_retained": retain_raw_payload,
        "receipt": receipt,
    }


@app.get("/ledger")
def ledger() -> dict[str, Any]:
    rows = read_ledger()
    return {"events": rows, "integrity": verify_chain()}


@app.get("/replay")
def replay_endpoint() -> dict[str, Any]:
    return {"integrity": verify_chain(), "replayed_state": replay()}


@app.get("/model-bridge/protocol")
def model_bridge_protocol() -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "state_boundary": "observable-explicit-only",
        "forbidden": [
            "chain_of_thought",
            "reasoning_trace",
            "hidden_state",
            "private_memory",
            "system_prompt",
            "credentials",
        ],
        "portability_semantics": "pre-declared-invariants-not-text-equality",
    }


@app.post("/model-bridge/sessions")
def create_cross_model_session(req: ModelSessionCreate) -> dict[str, Any]:
    hdb = evaluate_hdb(None)
    decision, reason = omega_gate(
        hdb=hdb,
        action="COMMIT",
        ontology_ok=req.ontology_ok,
        signature_valid=req.signature_valid,
        transition_valid=req.transition_valid,
    )
    if decision is not Decision.PASS:
        raise HTTPException(status_code=403, detail={"decision": decision.value, "reason": reason})

    contract = req.contract.model_dump()
    frozen_contract_hash = contract_hash(contract)
    session_id = f"mbs_{uuid.uuid4().hex}"
    try:
        created = create_model_session(
            session_id=session_id,
            protocol_version=PROTOCOL_VERSION,
            contract=contract,
            frozen_contract_hash=frozen_contract_hash,
            participants=[item.model_dump() for item in req.participants],
        )
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"decision": "PASS", "reason": reason, **created}


@app.get("/model-bridge/sessions/{session_id}")
def fetch_cross_model_session(session_id: str) -> dict[str, Any]:
    session = get_model_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="model bridge session not found")
    return session


@app.post("/model-bridge/sessions/{session_id}/handoffs")
def create_cross_model_handoff(session_id: str, req: ModelHandoffCreate) -> dict[str, Any]:
    try:
        assert_transferable_state(req.payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    hdb = evaluate_hdb(req.human.model_dump() if req.human else None)
    decision, reason = omega_gate(
        hdb=hdb,
        action="PROVIDER_EXPOSURE",
        ontology_ok=req.ontology_ok,
        signature_valid=req.signature_valid,
        transition_valid=req.transition_valid,
    )
    payload_hash = stable_hash(req.payload)
    if decision is not Decision.PASS:
        receipt = append_event(
            {
                "event_type": "MODEL_BRIDGE_HANDOFF_REJECTED",
                "session_id": session_id,
                "from_participant": req.from_participant,
                "to_participant": req.to_participant,
                "payload_hash": payload_hash,
                "contract_hash": req.expected_contract_hash,
                "gate": {"decision": decision.value, "reason": reason},
            },
            decision.value,
        )
        return {
            "decision": decision.value,
            "reason": reason,
            "stored": False,
            "payload_hash": payload_hash,
            "receipt": receipt,
        }

    try:
        handoff = append_model_handoff(
            session_id=session_id,
            from_participant=req.from_participant,
            to_participant=req.to_participant,
            parent_handoff_id=req.parent_handoff_id,
            payload=req.payload,
            expected_contract_hash=req.expected_contract_hash,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"decision": "PASS", "reason": reason, "stored": True, **handoff}


@app.get("/model-bridge/sessions/{session_id}/inbox/{participant_id}")
def cross_model_inbox(session_id: str, participant_id: str) -> dict[str, Any]:
    try:
        handoffs = list_model_inbox(session_id, participant_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {"session_id": session_id, "participant_id": participant_id, "handoffs": handoffs}


@app.post("/model-bridge/handoffs/{handoff_id}/ack")
def ack_cross_model_handoff(handoff_id: str, req: ModelHandoffAck) -> dict[str, Any]:
    try:
        return acknowledge_model_handoff(handoff_id, req.participant_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.post("/model-bridge/compare")
def compare_cross_model_outputs(req: CrossModelCompare) -> dict[str, Any]:
    try:
        result = compare_invariants(
            left=req.left,
            right=req.right,
            rules=[item.model_dump() for item in req.rules],
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return result


@app.get("/world-real")
def world_real() -> dict[str, Any]:
    chain = verify_chain()
    criteria = {
        "endpoint_public_live": False,
        "persistent_storage": True,
        "runtime_integrated": True,
        "fail_closed_enforcement": True,
        "replay_demonstrated": bool(chain.get("ok")),
        "cross_model_protocol_implemented": True,
        "cross_model_external_execution": False,
        "observability": True,
        "ci_green": False,
        "external_reproduction": False,
    }
    return {"status": "WORLD_REAL" if all(criteria.values()) else "PENDING", "criteria": criteria}
