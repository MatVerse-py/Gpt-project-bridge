from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .auth import Principal, require_capability
from .evidence import evidence_receipt
from .openai_runtime import OPENAI_RUNTIME_PROTOCOL, OpenAIConfigurationError, OpenAIProviderError
from .openai_secret_plane import OpenAISecretPlaneBroker, secret_plane_status_from_env
from .secret_plane import KeyAuthority, SecretPlaneError
from .storage import append_event

router = APIRouter(prefix="/providers/openai", tags=["providers", "openai"])

# Process-local authority for short-lived disclosure leases. It is intentionally
# not persisted, exported or reused as provider credential material.
_OPENAI_PROCESS_LEASE_KEY = KeyAuthority.generate()


class OpenAIHumanData(BaseModel):
    consent: bool | None = None
    purpose: str | None = None
    sensitivity: str | None = None
    third_party: bool = False
    third_party_consent: bool = False
    serialize_human: bool = False


class OpenAIResponseRequest(BaseModel):
    input: str = Field(min_length=1, max_length=1_000_000)
    instructions: str | None = Field(default=None, max_length=200_000)
    metadata: dict[str, str] = Field(default_factory=dict)
    human: OpenAIHumanData | None = None


@router.get("/status")
def openai_status(
    principal: Principal = Depends(require_capability("provider:openai:read")),
) -> dict[str, Any]:
    return {**secret_plane_status_from_env(), "read_by": principal.principal_id}


@router.post("/responses")
def openai_response(
    req: OpenAIResponseRequest,
    principal: Principal = Depends(require_capability("provider:openai:invoke")),
) -> dict[str, Any]:
    try:
        broker = OpenAISecretPlaneBroker.from_env(lease_signing_key=_OPENAI_PROCESS_LEASE_KEY)
    except OpenAIConfigurationError as exc:
        event = {
            "event_type": "OPENAI_PROVIDER_CONFIGURATION_HOLD",
            "principal_id": principal.principal_id,
            "protocol": OPENAI_RUNTIME_PROTOCOL,
            "credential_mode": "secret_plane",
            "reason": str(exc),
        }
        receipt = append_event(event, "HOLD")
        raise HTTPException(
            status_code=503,
            detail={"decision": "HOLD", "reason": str(exc), "receipt": receipt},
        ) from exc

    human = req.human.model_dump() if req.human is not None else None
    try:
        result = broker.governed_invoke(
            actor=principal.principal_id,
            input_text=req.input,
            instructions=req.instructions,
            metadata=req.metadata,
            human=human,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OpenAIProviderError as exc:
        event = {
            "event_type": "OPENAI_PROVIDER_FAILURE",
            "principal_id": principal.principal_id,
            "protocol": OPENAI_RUNTIME_PROTOCOL,
            "provider": "openai",
            "credential_mode": "secret_plane",
            "status_code": exc.status_code,
            "provider_request_id": exc.request_id,
            "provider_code": exc.provider_code,
        }
        ledger_receipt = append_event(event, "HOLD")
        raise HTTPException(
            status_code=502,
            detail={
                "decision": "HOLD",
                "reason": "provider request failed",
                "status_code": exc.status_code,
                "provider_request_id": exc.request_id,
                "provider_code": exc.provider_code,
                "receipt": ledger_receipt,
            },
        ) from exc
    except (SecretPlaneError, OpenAIConfigurationError) as exc:
        event = {
            "event_type": "OPENAI_SECRET_PLANE_HOLD",
            "principal_id": principal.principal_id,
            "protocol": OPENAI_RUNTIME_PROTOCOL,
            "provider": "openai",
            "credential_mode": "secret_plane",
            "error_type": type(exc).__name__,
        }
        ledger_receipt = append_event(event, "HOLD")
        raise HTTPException(
            status_code=503,
            detail={
                "decision": "HOLD",
                "reason": "secret plane could not authorize or disclose the provider credential",
                "receipt": ledger_receipt,
            },
        ) from exc

    auditable_input = {
        "protocol": OPENAI_RUNTIME_PROTOCOL,
        "principal_id": principal.principal_id,
        "provider": "openai",
        "credential_mode": "secret_plane",
        "secret_id": result.get("secret_id"),
        "secret_version": result.get("secret_version"),
        "model": result.get("model"),
        "request_hash": result.get("request_hash"),
        "human_boundary_declared": human is not None,
    }

    if result["decision"] != "PASS":
        ledger_receipt = append_event(
            {
                "event_type": "OPENAI_PROVIDER_EXPOSURE_REJECTED",
                **auditable_input,
                "gate": {"decision": result["decision"], "reason": result["reason"]},
                "secret_disclosed": result.get("secret_disclosed", False),
            },
            result["decision"],
        )
        return {**result, "ledger_receipt": ledger_receipt}

    auditable_output = {
        "response_id": result.get("response_id"),
        "model": result.get("model"),
        "response_hash": result.get("response_hash"),
        "usage": result.get("usage", {}),
        "provider_request_id": result.get("provider_request_id"),
        "lease_id": result.get("lease_id"),
    }
    ev = evidence_receipt("OPENAI_RESPONSE", auditable_input, auditable_output)
    ledger_receipt = append_event(
        {
            "event_type": "OPENAI_RESPONSE",
            **auditable_input,
            **auditable_output,
            "evidence_receipt": ev,
        },
        "PASS",
    )
    return {**result, "evidence_receipt": ev, "ledger_receipt": ledger_receipt}
