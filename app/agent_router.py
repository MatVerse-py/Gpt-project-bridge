from __future__ import annotations
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ConfigDict

from .auth import Principal, require_capability
from .core import Decision, evaluate_hdb, omega_gate, stable_hash
from . import storage
from .agent_jobs import AgentConflict, claim_job, finish_job, get_job, update_job
from .agent_runtime import AgentBroker, AgentConfigurationError, AgentProviderError, AgentSettings, agent_catalog, validate_payload
from .secret_plane import SecretPlaneError

router = APIRouter(prefix="/agents", tags=["agents"])


class HumanBoundary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consent: bool | None = None
    purpose: str | None = None
    sensitivity: str | None = None
    third_party: bool = False
    third_party_consent: bool = False
    serialize_human: bool = False


class AgentInvocation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(pattern=r"^mbs_[a-f0-9]{32}$")
    handoff_id: str = Field(pattern=r"^mh_[a-f0-9]{32}$")
    expected_contract_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    human: HumanBoundary | None = None


class ExternalReply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payload: dict[str, Any]


def _authorized_agent(principal: Principal, agent_id: str):
    if not principal.allows(f"agent:invoke:{agent_id}"):
        raise HTTPException(403, detail="principal lacks the selected agent invocation capability")


def _job_for(principal: Principal, agent_id: str, job_id: str) -> dict[str, Any]:
    job = get_job(job_id)
    if job is None or job["agent_id"] != agent_id:
        raise HTTPException(404, detail="agent job not found")
    session = storage.get_model_session(job["session_id"])
    enrolled = {p["participant_id"] for p in session["participants"]}
    if principal.principal_id not in enrolled and not principal.allows("agent:read:any"):
        raise HTTPException(403, detail="principal is not a session participant")
    return job


def _complete_provider_job(job_id: str, result: dict[str, Any]) -> dict[str, Any]:
    if result["status"] == "COMPLETE":
        payload = {"kind":"agent_reply","public_summary":result["output_text"],
                   "metadata":{k:result[k] for k in ("model","requested_model","response_id","task_id","usage","tool") if k in result}}
        validate_payload(payload)
        return finish_job(job_id,payload,"provider_http")
    # The prompt is returned to the authorized caller, but never duplicated in a job record.
    retained = {k:v for k,v in result.items() if k not in {"prompt","output_text"}}
    job = update_job(job_id,result["status"],retained)
    if "prompt" in result: job["dispatch_payload"] = result["prompt"]
    if "output_text" in result: job["partial_output"] = result["output_text"]
    return job


@router.get("")
def list_agents(principal: Principal = Depends(require_capability("agent:read"))):
    return {"protocol":"matverse.agent-bridge.v1","agents":agent_catalog(),"read_by":principal.principal_id}


@router.post("/{agent_id}/invoke")
def invoke_agent(agent_id: str, req: AgentInvocation,
                 principal: Principal = Depends(require_capability("agent:invoke"))):
    _authorized_agent(principal,agent_id)
    session = storage.get_model_session(req.session_id)
    if session is None: raise HTTPException(404,detail="model bridge session not found")
    if session["contract_hash"] != req.expected_contract_hash:
        raise HTTPException(403,detail="frozen contract hash mismatch")
    participants = {p["participant_id"]:p for p in session["participants"]}
    if principal.principal_id not in participants and not principal.allows("agent:invoke:any"):
        raise HTTPException(403,detail="principal is not a session participant")
    if agent_id not in participants: raise HTTPException(403,detail="agent is not enrolled in this session")
    # Repetition must resolve the existing durable job even after the input ACK.
    from .agent_jobs import job_for_handoff
    prior = job_for_handoff(req.handoff_id)
    if prior:
        if prior["agent_id"] != agent_id or prior["session_id"] != req.session_id:
            raise HTTPException(409,detail="handoff belongs to another agent job")
        if principal.principal_id not in {prior["actor"],agent_id} and not principal.allows("agent:invoke:any"):
            raise HTTPException(403,detail="principal is not an endpoint of this agent job")
        if prior["status"] == "WAITING_EXTERNAL":
            inbox = storage.list_model_inbox(req.session_id,agent_id)
            source = next((x for x in inbox if x["handoff_id"] == req.handoff_id),None)
            if source: prior["dispatch_payload"] = validate_payload(source["payload"])
        return prior
    inbox = storage.list_model_inbox(req.session_id,agent_id)
    handoff = next((x for x in inbox if x["handoff_id"] == req.handoff_id),None)
    if handoff is None: raise HTTPException(404,detail="pending agent handoff not found")
    if principal.principal_id not in {handoff["from_participant"],agent_id} and not principal.allows("agent:invoke:any"):
        raise HTTPException(403,detail="principal is not an endpoint of this handoff")
    human = req.human.model_dump() if req.human else None
    decision, reason = omega_gate(hdb=evaluate_hdb(human), action="PROVIDER_EXPOSURE",
                                 ontology_ok=True,signature_valid=True,transition_valid=True)
    if decision is not Decision.PASS:
        return {"decision":decision.value,"reason":reason,"executed":False}
    try:
        validate_payload(handoff["payload"])
        settings = AgentSettings.from_env(agent_id)
        enrollment = participants[agent_id]
        if enrollment["provider"] != settings.spec.provider or enrollment["model"] != settings.model:
            raise PermissionError("configured agent identity does not match the frozen session enrollment")
        if not settings.status()["configured"]:
            raise AgentConfigurationError("selected agent requires a configured credential")
        broker = AgentBroker(settings,actor=principal.principal_id)
        job, claimed = claim_job(agent_id=agent_id,session_id=req.session_id,handoff_id=req.handoff_id,
            contract_hash=req.expected_contract_hash,actor=principal.principal_id,
            transport=settings.spec.transport,model=settings.model)
        if not claimed: return job
        result = broker.invoke(handoff["payload"],human,allow_base44_write=principal.allows("agent:base44:write"))
        return _complete_provider_job(job["job_id"],result)
    except (AgentConfigurationError,SecretPlaneError) as exc:
        if "job" in locals():
            update_job(job["job_id"],"UNCERTAIN",{"error_type":type(exc).__name__})
        raise HTTPException(503,detail="agent credential or configuration is unavailable") from None
    except AgentProviderError as exc:
        if "job" in locals():
            update_job(job["job_id"],"UNCERTAIN",{"error_type":exc.code,"status_code":exc.status_code})
        raise HTTPException(502,detail={"reason":exc.code,"status_code":exc.status_code,
                                      "retry":"reconcile the existing job before another external dispatch"}) from None
    except (PermissionError,ValueError,AgentConflict) as exc:
        if "job" in locals(): update_job(job["job_id"],"FAILED",{"error_type":type(exc).__name__})
        raise HTTPException(403 if isinstance(exc,PermissionError) else 409 if isinstance(exc,AgentConflict) else 422,
                            detail=str(exc)) from None
    except Exception:
        if "job" in locals(): update_job(job["job_id"],"UNCERTAIN",{"error_type":"unexpected_dispatch_failure"})
        raise


@router.get("/{agent_id}/jobs/{job_id}")
def read_agent_job(agent_id: str,job_id: str,principal: Principal = Depends(require_capability("agent:read"))):
    return _job_for(principal,agent_id,job_id)


@router.post("/{agent_id}/jobs/{job_id}/complete")
def complete_external_agent(agent_id: str,job_id: str,req: ExternalReply,
                            principal: Principal = Depends(require_capability("agent:reply"))):
    job = _job_for(principal,agent_id,job_id)
    if job["transport"] != "external_handoff":
        raise HTTPException(403,detail="manual reply is restricted to an external handoff transport")
    if principal.principal_id != agent_id and not principal.allows("agent:reply:any"):
        raise HTTPException(403,detail="principal does not match the enrolled responding agent")
    if job["status"] not in {"WAITING_EXTERNAL","COMPLETE"}:
        raise HTTPException(409,detail="job is not waiting for an external reply")
    try:
        validate_payload(req.payload)
        return finish_job(job_id,req.payload,"authenticated_external_client")
    except (ValueError,PermissionError,AgentConflict) as exc:
        raise HTTPException(409,detail=str(exc)) from None


@router.post("/manus/jobs/{job_id}/poll")
def poll_manus_job(job_id: str,principal: Principal = Depends(require_capability("agent:invoke"))):
    _authorized_agent(principal,"manus")
    job = _job_for(principal,"manus",job_id)
    if job["status"] in {"COMPLETE","FAILED","UNCERTAIN"}: return job
    if int(time.time()) > job["deadline"]:
        return update_job(job_id,"UNKNOWN",{"reason":"deadline expired; reconcile provider task","task_id":job["result"].get("task_id")})
    task_id = job["result"].get("task_id")
    if not task_id: raise HTTPException(409,detail="Manus task has not been accepted")
    try:
        settings = AgentSettings.from_env("manus")
        if settings.model != job["requested_model"]:
            raise AgentConfigurationError("Manus configuration changed during a frozen job")
        result = AgentBroker(settings,actor=principal.principal_id).poll_manus(task_id)
        return _complete_provider_job(job_id,result)
    except (SecretPlaneError,AgentConfigurationError):
        raise HTTPException(503,detail="Manus credential or configuration is unavailable") from None
    except AgentProviderError as exc:
        raise HTTPException(502,detail={"reason":exc.code,"status_code":exc.status_code}) from None
