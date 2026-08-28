from __future__ import annotations

import os
import re

from fastapi import APIRouter, Depends, HTTPException

from .auth import Principal, authenticate
from .institutional_projection import ProjectionUnavailable, build_institutional_projection
from .institutional_protocol import AUTH_METHOD, PROTOCOL_VERSION, RUNTIME_SCHEMA_VERSION

runtime_router = APIRouter()
_RUNTIME_ID = re.compile(r"^[A-Za-z0-9._:-]{3,128}$")


def _runtime_id_or_503() -> str:
    runtime_id = os.environ.get("MATVERSE_RUNTIME_ID", "")
    if _RUNTIME_ID.fullmatch(runtime_id) is None:
        raise HTTPException(
            status_code=503,
            detail={
                "decision": "HOLD",
                "freshness": "SOURCE_UNAVAILABLE",
                "reason": "MATVERSE_RUNTIME_ID must be provisioned before external activation",
            },
        )
    return runtime_id


def _projection_or_503() -> dict:
    try:
        return build_institutional_projection()
    except ProjectionUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "decision": "HOLD",
                "freshness": "SOURCE_UNAVAILABLE",
                "reason": str(exc),
            },
        ) from exc


@runtime_router.get("/institutional/runtime")
def institutional_runtime(principal: Principal = Depends(authenticate)) -> dict:
    if not principal.allows("institutional:projection:read"):
        raise HTTPException(status_code=403, detail="principal lacks capability: institutional:projection:read")
    projection = _projection_or_503()
    return {
        "schema_version": RUNTIME_SCHEMA_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "runtime_id": _runtime_id_or_503(),
        "authentication": AUTH_METHOD,
        "authenticated_principal_id": principal.principal_id,
        "source": projection["source"],
        "projection_hash": projection["projection"]["projection_hash"],
        "status": "READY",
        # Runtime metadata proves connectivity and binding only. It explicitly
        # does not claim that accepted institutional intents will execute.
        "intent_execution": "HOLD",
    }
