from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .auth import Principal, require_capability
from .principal_registry import PrincipalIdentityRegistry

management_router = APIRouter(prefix="/trust/auth", tags=["asymmetric-auth-trust-plane"])
public_router = APIRouter(prefix="/v1/auth", tags=["asymmetric-auth-public-material"])
_registry = PrincipalIdentityRegistry()

PUBLIC_KEY_PATTERN = r"^[0-9a-f]{64}$"
KEY_ID_PATTERN = r"^ed25519:[0-9a-f]{64}$"
IDENTIFIER_PATTERN = r"^[A-Za-z0-9._:-]{1,128}$"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PrincipalCreate(StrictModel):
    public_key_hex: str = Field(pattern=PUBLIC_KEY_PATTERN)
    capabilities: tuple[str, ...] = Field(min_length=1, max_length=128)
    valid_from: int = Field(ge=0)
    valid_until: int = Field(gt=0)

    @field_validator("capabilities")
    @classmethod
    def canonical_capabilities(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() or len(item) > 128 for item in value):
            raise ValueError("capabilities must be non-empty strings <= 128 characters")
        if value != tuple(sorted(set(value))):
            raise ValueError("capabilities must be unique and lexicographically sorted")
        return value

    @model_validator(mode="after")
    def validate_window(self) -> "PrincipalCreate":
        if self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be greater than valid_from")
        return self


class PrincipalKeyRotate(StrictModel):
    public_key_hex: str = Field(pattern=PUBLIC_KEY_PATTERN)
    valid_from: int = Field(ge=0)
    valid_until: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_window(self) -> "PrincipalKeyRotate":
        if self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be greater than valid_from")
        return self


class RevokeRequest(StrictModel):
    effective_at: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=512)


def _raise_registry_error(exc: Exception) -> None:
    if isinstance(exc, LookupError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, PermissionError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise exc


def _can_act_on(principal: Principal, target_principal_id: str, operation: str) -> None:
    if principal.principal_id == target_principal_id:
        return
    if principal.allows(f"auth:principal:{operation}:any"):
        return
    raise HTTPException(
        status_code=403,
        detail=f"principal may not {operation} another principal without auth:principal:{operation}:any",
    )


def _assert_grant_subset(actor: Principal, capabilities: tuple[str, ...]) -> None:
    if actor.allows("auth:principal:grant:any"):
        return
    if "*" in capabilities:
        raise HTTPException(status_code=403, detail="wildcard capability requires auth:principal:grant:any")
    missing = [capability for capability in capabilities if not actor.allows(capability)]
    if missing:
        raise HTTPException(
            status_code=403,
            detail={"reason": "principal may not grant capabilities it does not hold", "capabilities": missing},
        )


def _serialized_principal(principal_id: str) -> dict[str, object]:
    record = _registry.get_principal(principal_id)
    if record is None:
        raise HTTPException(status_code=404, detail="principal not found")
    return {
        "principal": asdict(record),
        "keys": [asdict(key) for key in _registry.list_keys(principal_id)],
    }


@public_router.get("/credentials/{principal_id}/{key_id}")
def public_credential(principal_id: str, key_id: str) -> dict[str, object]:
    try:
        _registry.bootstrap_root_from_environment()
    except Exception as exc:
        _raise_registry_error(exc)
        raise AssertionError("unreachable")
    credential = _registry.resolve_credential(principal_id, key_id)
    if credential is None:
        raise HTTPException(status_code=404, detail="principal credential not found")
    return {
        "auth_scheme": "ED25519-PUBLIC-KEY-V1",
        "principal": asdict(credential.principal),
        "key": asdict(credential.key),
        "private_material_present": False,
    }


@management_router.post("/principals/{principal_id}")
def create_principal(
    principal_id: str,
    req: PrincipalCreate,
    actor: Principal = Depends(require_capability("auth:principal:create")),
) -> dict[str, object]:
    _assert_grant_subset(actor, req.capabilities)
    try:
        result = _registry.register_principal(
            principal_id=principal_id,
            capabilities=req.capabilities,
            public_key_hex=req.public_key_hex,
            valid_from=req.valid_from,
            valid_until=req.valid_until,
            actor_id=actor.principal_id,
        )
    except Exception as exc:
        _raise_registry_error(exc)
        raise AssertionError("unreachable")
    return {
        "principal": asdict(result["principal"]),
        "key": asdict(result["key"]),
        "receipt": result["receipt"],
        "authenticated_actor": actor.principal_id,
    }


@management_router.get("/principals/{principal_id}")
def read_principal(
    principal_id: str,
    actor: Principal = Depends(require_capability("auth:principal:read")),
) -> dict[str, object]:
    _can_act_on(actor, principal_id, "read")
    return {**_serialized_principal(principal_id), "read_by": actor.principal_id}


@management_router.post("/principals/{principal_id}/keys/{previous_key_id}/rotate")
def rotate_principal_key(
    principal_id: str,
    previous_key_id: str,
    req: PrincipalKeyRotate,
    actor: Principal = Depends(require_capability("auth:principal:rotate")),
) -> dict[str, object]:
    _can_act_on(actor, principal_id, "rotate")
    try:
        result = _registry.rotate_key(
            principal_id=principal_id,
            previous_key_id=previous_key_id,
            public_key_hex=req.public_key_hex,
            valid_from=req.valid_from,
            valid_until=req.valid_until,
            actor_id=actor.principal_id,
        )
    except Exception as exc:
        _raise_registry_error(exc)
        raise AssertionError("unreachable")
    return {
        "key": asdict(result["key"]),
        "receipt": result["receipt"],
        "authenticated_actor": actor.principal_id,
    }


@management_router.post("/principals/{principal_id}/keys/{key_id}/revoke")
def revoke_principal_key(
    principal_id: str,
    key_id: str,
    req: RevokeRequest,
    actor: Principal = Depends(require_capability("auth:principal:revoke-key")),
) -> dict[str, object]:
    _can_act_on(actor, principal_id, "revoke-key")
    try:
        result = _registry.revoke_key(
            principal_id=principal_id,
            key_id=key_id,
            effective_at=req.effective_at,
            reason=req.reason,
            actor_id=actor.principal_id,
        )
    except Exception as exc:
        _raise_registry_error(exc)
        raise AssertionError("unreachable")
    return {
        "key": asdict(result["key"]),
        "receipt": result.get("receipt"),
        "idempotent": bool(result.get("idempotent", False)),
        "authenticated_actor": actor.principal_id,
    }


@management_router.post("/principals/{principal_id}/revoke")
def revoke_principal(
    principal_id: str,
    req: RevokeRequest,
    actor: Principal = Depends(require_capability("auth:principal:revoke")),
) -> dict[str, object]:
    _can_act_on(actor, principal_id, "revoke")
    if actor.principal_id == principal_id and not actor.allows("auth:principal:revoke:self"):
        raise HTTPException(status_code=403, detail="self-revocation requires auth:principal:revoke:self")
    try:
        result = _registry.revoke_principal(
            principal_id=principal_id,
            effective_at=req.effective_at,
            reason=req.reason,
            actor_id=actor.principal_id,
        )
    except Exception as exc:
        _raise_registry_error(exc)
        raise AssertionError("unreachable")
    return {
        "principal": asdict(result["principal"]),
        "receipt": result.get("receipt"),
        "idempotent": bool(result.get("idempotent", False)),
        "authenticated_actor": actor.principal_id,
    }
