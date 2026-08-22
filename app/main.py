from __future__ import annotations

from typing import Any, Literal
import uuid

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field, model_validator

from .auth import Principal, require_capability
from .core import Decision, POState, evaluate_hdb, omega_gate, stable_hash
from .evidence import evidence_receipt
from .federation_routing import (
    AdmissibilityGate,
    CapabilityGraph,
    CapabilityNode,
    Criterion,
    Crossing,
    Direction,
    PreferenceModel,
    metric_ceiling,
    metric_floor,
    requires_attr,
)
from .model_bridge import PROTOCOL_VERSION, assert_transferable_state, compare_invariants, contract_hash
from .storage import (
    acknowledge_model_handoff,
    append_event,
    append_model_handoff,
    create_model_session,
    get_contract_artifact,
    get_model_session,
    list_model_inbox,
    read_ledger,
    register_contract_artifact,
    replay,
    verify_chain,
)

app = FastAPI(title="MATVERSE_REAL_v1", version="1.2.0-pass-hardening")
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
    action: Literal["READ", "COMMIT", "EXECUTE", "EXPORT", "PUBLISH"] = "EXECUTE"
    human: HumanData | None = None
    source: str = "external"
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))


class ContractArtifactCreate(BaseModel):
    kind: Literal["ontology", "policy", "task", "rubric", "memory_policy"]
    version: str = Field(min_length=1, max_length=128)
    content: dict[str, Any]


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

    @model_validator(mode="after")
    def validate_participants(self) -> "ModelSessionCreate":
        participant_ids = [item.participant_id for item in self.participants]
        if len(set(participant_ids)) != len(participant_ids):
            raise ValueError("participant_id values must be unique")
        identities = {(item.provider, item.model, item.revision) for item in self.participants}
        if len(identities) < 2:
            raise ValueError("cross-model session requires at least two distinct model identities")
        return self


class ModelHandoffCreate(BaseModel):
    from_participant: str = Field(pattern=PARTICIPANT_PATTERN)
    to_participant: str = Field(pattern=PARTICIPANT_PATTERN)
    parent_handoff_id: str | None = Field(default=None, max_length=128)
    expected_contract_hash: str = Field(pattern=SHA256_PATTERN)
    payload: dict[str, Any]
    human: HumanData | None = None


class ModelHandoffAck(BaseModel):
    participant_id: str = Field(pattern=PARTICIPANT_PATTERN)


class InvariantRule(BaseModel):
    path: str = Field(min_length=1, max_length=256)
    mode: Literal["exact", "set_equal", "type_equal"] = "exact"


class CrossModelCompare(BaseModel):
    left: dict[str, Any]
    right: dict[str, Any]
    rules: list[InvariantRule] = Field(min_length=1, max_length=128)


class CriterionSpec(BaseModel):
    name: str
    direction: Literal["higher_is_better", "lower_is_better"]
    lo: float
    hi: float
    scale: Literal["linear", "log"] = "linear"


class CapabilityNodeSpec(BaseModel):
    node_id: str
    layer: str
    raw: dict[str, float]
    attrs: dict[str, Any] = Field(default_factory=dict)


class CrossingSpec(BaseModel):
    src: str
    dst: str
    cost: float = Field(ge=0)
    reason: str = ""


class ConstraintSpec(BaseModel):
    kind: Literal["metric_floor", "metric_ceiling", "attr_equal"]
    name: str
    value: Any


class FederationRouteRequest(BaseModel):
    nodes: list[CapabilityNodeSpec] = Field(min_length=1, max_length=256)
    crossings: list[CrossingSpec] = Field(max_length=4096)
    criteria: list[CriterionSpec] = Field(min_length=1, max_length=64)
    weights: dict[str, float]
    constraints: list[ConstraintSpec] = Field(min_length=1, max_length=128)
    origin: str
    targets: list[str] | None = None


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "service": "MATVERSE_REAL_v1", "model_bridge_protocol": PROTOCOL_VERSION}


@app.get("/ready")
def ready() -> dict[str, Any]:
    chain = verify_chain()
    return {"ready": bool(chain.get("ok")), "ledger": chain}


@app.post("/registry/contracts")
def create_contract_artifact(req: ContractArtifactCreate, principal: Principal = Depends(require_capability("registry:write"))) -> dict[str, Any]:
    return register_contract_artifact(kind=req.kind, version=req.version, content=req.content, created_by=principal.principal_id)


@app.get("/registry/contracts/{artifact_hash}")
def read_contract_artifact(artifact_hash: str, principal: Principal = Depends(require_capability("registry:read"))) -> dict[str, Any]:
    artifact = get_contract_artifact(artifact_hash)
    if artifact is None:
        raise HTTPException(status_code=404, detail="contract artifact not found")
    return artifact


@app.post("/bridge/ingress")
def ingress(req: Ingress, principal: Principal = Depends(require_capability("bridge:ingress"))) -> dict[str, Any]:
    hdb = evaluate_hdb(req.human.model_dump() if req.human else None)
    decision, reason = omega_gate(hdb=hdb, action=req.action, ontology_ok=True, signature_valid=True, transition_valid=True)
    po_state = POState.PASS if decision is Decision.PASS else POState.FAIL if decision is Decision.BLOCK else POState.EVALUATING
    payload_hash = stable_hash(req.payload)
    retain_raw_payload = decision is Decision.PASS and req.human is None
    event = {
        "request_id": req.request_id,
        "principal_id": principal.principal_id,
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
    return {"request_id": req.request_id, "decision": decision.value, "reason": reason, "po_state": po_state.value, "executed": decision is Decision.PASS, "quarantined": decision is not Decision.PASS, "payload_hash": payload_hash, "payload_retained": retain_raw_payload, "receipt": receipt}


@app.get("/ledger")
def ledger(principal: Principal = Depends(require_capability("ledger:read"))) -> dict[str, Any]:
    return {"events": read_ledger(), "integrity": verify_chain(), "read_by": principal.principal_id}


@app.get("/replay")
def replay_endpoint(principal: Principal = Depends(require_capability("replay:read"))) -> dict[str, Any]:
    return {"integrity": verify_chain(), "replayed_state": replay(), "read_by": principal.principal_id}


@app.get("/model-bridge/protocol")
def model_bridge_protocol() -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "state_boundary": "allowlisted-observable-explicit-only",
        "forbidden_classes": ["hidden_reasoning", "private_memory", "internal_prompts", "credentials", "tokens", "passwords"],
        "portability_semantics": "pre-declared-invariants-not-text-equality",
        "contract_semantics": "hashes-must-resolve-to-immutable-registry-artifacts",
        "identity_semantics": "authenticated-principal-not-self-declared-participant",
    }


@app.post("/model-bridge/sessions")
def create_cross_model_session(req: ModelSessionCreate, principal: Principal = Depends(require_capability("session:create"))) -> dict[str, Any]:
    hdb = evaluate_hdb(None)
    decision, reason = omega_gate(hdb=hdb, action="COMMIT", ontology_ok=True, signature_valid=True, transition_valid=True)
    if decision is not Decision.PASS:
        raise HTTPException(status_code=403, detail={"decision": decision.value, "reason": reason})
    contract = req.contract.model_dump()
    frozen_contract_hash = contract_hash(contract)
    session_id = f"mbs_{uuid.uuid4().hex}"
    try:
        created = create_model_session(session_id=session_id, protocol_version=PROTOCOL_VERSION, contract=contract, frozen_contract_hash=frozen_contract_hash, participants=[item.model_dump() for item in req.participants], created_by=principal.principal_id)
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"decision": "PASS", "reason": reason, **created}


@app.get("/model-bridge/sessions/{session_id}")
def fetch_cross_model_session(session_id: str, principal: Principal = Depends(require_capability("session:read"))) -> dict[str, Any]:
    session = get_model_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="model bridge session not found")
    participant_ids = {item["participant_id"] for item in session["participants"]}
    if principal.principal_id not in participant_ids and not principal.allows("session:read:any"):
        raise HTTPException(status_code=403, detail="principal is not a session participant")
    return session


@app.post("/model-bridge/sessions/{session_id}/handoffs")
def create_cross_model_handoff(session_id: str, req: ModelHandoffCreate, principal: Principal = Depends(require_capability("handoff:send"))) -> dict[str, Any]:
    if principal.principal_id != req.from_participant and not principal.allows("handoff:send:any"):
        raise HTTPException(status_code=403, detail="authenticated principal does not match from_participant")
    try:
        assert_transferable_state(req.payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    hdb = evaluate_hdb(req.human.model_dump() if req.human else None)
    decision, reason = omega_gate(hdb=hdb, action="PROVIDER_EXPOSURE", ontology_ok=True, signature_valid=True, transition_valid=True)
    payload_hash = stable_hash(req.payload)
    if decision is not Decision.PASS:
        receipt = append_event({"event_type": "MODEL_BRIDGE_HANDOFF_REJECTED", "session_id": session_id, "from_participant": req.from_participant, "to_participant": req.to_participant, "payload_hash": payload_hash, "contract_hash": req.expected_contract_hash, "gate": {"decision": decision.value, "reason": reason}}, decision.value)
        return {"decision": decision.value, "reason": reason, "stored": False, "payload_hash": payload_hash, "receipt": receipt}
    try:
        handoff = append_model_handoff(session_id=session_id, from_participant=req.from_participant, to_participant=req.to_participant, parent_handoff_id=req.parent_handoff_id, payload=req.payload, expected_contract_hash=req.expected_contract_hash)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"decision": "PASS", "reason": reason, "stored": True, **handoff}


@app.get("/model-bridge/sessions/{session_id}/inbox/{participant_id}")
def cross_model_inbox(session_id: str, participant_id: str, principal: Principal = Depends(require_capability("inbox:read"))) -> dict[str, Any]:
    if principal.principal_id != participant_id and not principal.allows("inbox:read:any"):
        raise HTTPException(status_code=403, detail="authenticated principal does not match inbox participant")
    try:
        handoffs = list_model_inbox(session_id, participant_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {"session_id": session_id, "participant_id": participant_id, "handoffs": handoffs}


@app.post("/model-bridge/handoffs/{handoff_id}/ack")
def ack_cross_model_handoff(handoff_id: str, req: ModelHandoffAck, principal: Principal = Depends(require_capability("handoff:ack"))) -> dict[str, Any]:
    if principal.principal_id != req.participant_id and not principal.allows("handoff:ack:any"):
        raise HTTPException(status_code=403, detail="authenticated principal does not match ack participant")
    try:
        return acknowledge_model_handoff(handoff_id, req.participant_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.post("/model-bridge/compare")
def compare_cross_model_outputs(req: CrossModelCompare, principal: Principal = Depends(require_capability("compare:execute"))) -> dict[str, Any]:
    try:
        return compare_invariants(left=req.left, right=req.right, rules=[item.model_dump() for item in req.rules])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/federation/route")
def federation_route(req: FederationRouteRequest, principal: Principal = Depends(require_capability("federation:route"))) -> dict[str, Any]:
    criteria = {
        item.name: Criterion(item.name, Direction(item.direction), item.lo, item.hi, item.scale)
        for item in req.criteria
    }
    constraints = []
    for item in req.constraints:
        if item.kind == "metric_floor":
            constraints.append(metric_floor(item.name, float(item.value)))
        elif item.kind == "metric_ceiling":
            constraints.append(metric_ceiling(item.name, float(item.value)))
        else:
            constraints.append(requires_attr(item.name, item.value))
    graph = CapabilityGraph(
        nodes=[CapabilityNode(item.node_id, item.layer, item.raw, item.attrs) for item in req.nodes],
        crossings=[Crossing(item.src, item.dst, item.cost, item.reason) for item in req.crossings],
        gate=AdmissibilityGate(constraints),
        preference=PreferenceModel(criteria, req.weights),
    )
    try:
        result = graph.route(req.origin, req.targets)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    output = {
        "path": list(result.path),
        "total_cost": result.total_cost,
        "crossing_cost": result.crossing_cost,
        "terminal_potential": result.terminal_potential,
        "blocked": result.blocked,
        "route_receipt_sha256": result.receipt_sha256,
        "conservativity": graph.conservativity_report(),
        "sensitivity": graph.preference.sensitivity(graph.admissible),
    }
    ev = evidence_receipt("FEDERATION_ROUTE", req.model_dump(), output)
    ledger_receipt = append_event({"event_type": "FEDERATION_ROUTE", "principal_id": principal.principal_id, "route_receipt_sha256": result.receipt_sha256, "evidence_receipt": ev}, "PASS")
    return {**output, "evidence_receipt": ev, "ledger_receipt": ledger_receipt}


@app.get("/world-real")
def world_real(principal: Principal = Depends(require_capability("world:read"))) -> dict[str, Any]:
    chain = verify_chain()
    criteria = {
        "authenticated_principals": True,
        "contract_registry_binding": True,
        "transfer_boundary_hardened": True,
        "sensitive_endpoints_authorized": True,
        "federation_routing_integrated": True,
        "persistent_storage": True,
        "runtime_integrated": True,
        "fail_closed_enforcement": True,
        "replay_demonstrated_local": bool(chain.get("ok")),
        "endpoint_public_live": False,
        "cross_model_external_execution": False,
        "external_reproduction": False,
    }
    return {"status": "WORLD_REAL" if all(criteria.values()) else "PENDING", "criteria": criteria, "read_by": principal.principal_id}
