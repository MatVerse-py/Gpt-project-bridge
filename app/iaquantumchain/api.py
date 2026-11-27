from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.auth import Principal, require_capability

from .service import IAQuantumChainService

router = APIRouter(tags=["IAQuantumChain"])
_SERVICE = IAQuantumChainService()
_DASHBOARD = Path(__file__).with_name("dashboard.html")


class ExperimentSubmit(BaseModel):
    experiment_id: str = Field(default="QEX-SUBSTRATE-01", min_length=1, max_length=80)
    payload: dict[str, Any]
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))


@router.get("/status/public")
def public_status() -> dict[str, Any]:
    """Read-only module status; exposes no credentials, ledger payloads or run data."""
    return _SERVICE.status(public=True)


@router.get("/status")
def status(principal: Principal = Depends(require_capability("iaquantumchain:read"))) -> dict[str, Any]:
    return _SERVICE.status()


@router.post("/experiments")
def submit_experiment(
    request: ExperimentSubmit,
    principal: Principal = Depends(require_capability("iaquantumchain:experiment:submit")),
) -> dict[str, Any]:
    result = _SERVICE.submit(
        principal_id=principal.principal_id,
        authenticated=True,
        experiment_id=request.experiment_id,
        payload=request.payload,
        request_id=request.request_id,
    )
    if result.get("status") == "INVALID_REQUEST":
        raise HTTPException(status_code=422, detail=result)
    return result


@router.get("/runs/{run_id}")
def get_run(
    run_id: str,
    principal: Principal = Depends(require_capability("iaquantumchain:read")),
) -> dict[str, Any]:
    result = _SERVICE.get_run(run_id)
    if not result.get("found"):
        raise HTTPException(status_code=404, detail="IAQuantumChain run not found")
    return result


@router.get("/ledger/verify")
def verify_ledger(
    principal: Principal = Depends(require_capability("iaquantumchain:ledger:read")),
) -> dict[str, Any]:
    result = _SERVICE.verify_ledger()
    if result.get("ok") is not True:
        raise HTTPException(status_code=503, detail={"decision": "HOLD", "ledger": result})
    return result


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard() -> str:
    if not _DASHBOARD.is_file():
        raise HTTPException(status_code=404, detail="dashboard asset missing")
    return _DASHBOARD.read_text(encoding="utf-8")
