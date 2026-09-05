from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .auth import Principal, require_capability
from .federation_ed25519 import ED25519_PUBLIC_KEY_SCHEME, Ed25519RelationWitness
from .federation_key_registry import (
    AuthorityKeyRecord,
    FederationAuthorityKeyRegistry,
    authority_key_id,
)
from .federation_relation import FederationRelation, RelationStatus

router = APIRouter(prefix="/trust/federation", tags=["federation-trust-plane"])
_registry = FederationAuthorityKeyRegistry()

PUBLIC_KEY_PATTERN = r"^[0-9a-f]{64}$"
KEY_ID_PATTERN = r"^ed25519:[0-9a-f]{64}$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"
SIGNATURE_PATTERN = r"^[0-9a-f]{128}$"
IDENTIFIER_PATTERN = r"^[A-Za-z0-9._:-]{1,128}$"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GenesisKeyCreate(StrictModel):
    public_key_hex: str = Field(pattern=PUBLIC_KEY_PATTERN)
    valid_from: int = Field(ge=0)
    valid_until: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_window(self) -> "GenesisKeyCreate":
        if self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be greater than valid_from")
        return self


class RotateKeyCreate(StrictModel):
    public_key_hex: str = Field(pattern=PUBLIC_KEY_PATTERN)
    valid_until: int = Field(gt=0)


class RevokeKeyRequest(StrictModel):
    effective_at: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=512)


class RelationKeyBindingCreate(StrictModel):
    source_domain: str = Field(pattern=IDENTIFIER_PATTERN)
    target_domain: str = Field(pattern=IDENTIFIER_PATTERN)
    source_authority: str = Field(pattern=IDENTIFIER_PATTERN)
    target_authority: str = Field(pattern=IDENTIFIER_PATTERN)
    contract_hash: str = Field(pattern=SHA256_PATTERN)
    capabilities: tuple[str, ...] = Field(min_length=1, max_length=128)
    valid_from: int = Field(ge=0)
    valid_until: int = Field(gt=0)
    evidence_policy: str = Field(default="receipt_required", min_length=1, max_length=128)
    witness_payload_sha256: str = Field(pattern=SHA256_PATTERN)
    source_signature_hex: str = Field(pattern=SIGNATURE_PATTERN)
    target_signature_hex: str = Field(pattern=SIGNATURE_PATTERN)
    source_key_id: str = Field(pattern=KEY_ID_PATTERN)
    target_key_id: str = Field(pattern=KEY_ID_PATTERN)

    @field_validator("capabilities")
    @classmethod
    def canonical_capabilities(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not isinstance(item, str) or not item.strip() for item in value):
            raise ValueError("capabilities must be non-empty strings")
        canonical = tuple(sorted(set(value)))
        if value != canonical:
            raise ValueError("capabilities must be unique and lexicographically sorted")
        if "*" in value:
            raise ValueError("wildcard federation capability is forbidden")
        return value

    @model_validator(mode="after")
    def validate_relation(self) -> "RelationKeyBindingCreate":
        if self.source_domain == self.target_domain:
            raise ValueError("federation relation must cross distinct domains")
        if self.source_authority == self.target_authority:
            raise ValueError("federation relation requires distinct authorities")
        if self.source_key_id == self.target_key_id:
            raise ValueError("binding requires distinct authority keys")
        if self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be greater than valid_from")
        return self


def _raise_registry_error(exc: Exception) -> None:
    if isinstance(exc, LookupError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, PermissionError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise exc


def _mutation_response(result: dict[str, object], principal: Principal) -> dict[str, object]:
    payload = dict(result)
    if "record" in payload:
        payload["record"] = asdict(payload["record"])
    if "binding" in payload:
        payload["binding"] = asdict(payload["binding"])
    payload["authenticated_actor"] = principal.principal_id
    return payload


@router.post("/authorities/{authority_id}/keys")
def register_genesis_key(
    authority_id: str,
    req: GenesisKeyCreate,
    principal: Principal = Depends(require_capability("federation:key:register")),
) -> dict[str, object]:
    record = AuthorityKeyRecord(
        authority_id=authority_id,
        key_id=authority_key_id(req.public_key_hex),
        public_key_hex=req.public_key_hex,
        valid_from=req.valid_from,
        valid_until=req.valid_until,
    )
    try:
        result = _registry.register_key(record, actor_id=principal.principal_id)
    except Exception as exc:
        _raise_registry_error(exc)
        raise AssertionError("unreachable")
    return _mutation_response(result, principal)


@router.post("/authorities/{authority_id}/keys/{previous_key_id}/rotate")
def rotate_authority_key(
    authority_id: str,
    previous_key_id: str,
    req: RotateKeyCreate,
    principal: Principal = Depends(require_capability("federation:key:rotate")),
) -> dict[str, object]:
    previous = _registry.get_key(previous_key_id)
    if previous is None:
        raise HTTPException(status_code=404, detail="previous authority key not found")
    if previous.authority_id != authority_id:
        raise HTTPException(status_code=409, detail="previous key belongs to a different authority")
    if req.valid_until <= previous.valid_until:
        raise HTTPException(status_code=409, detail="rotated key valid_until must exceed previous valid_until")
    record = AuthorityKeyRecord(
        authority_id=authority_id,
        key_id=authority_key_id(req.public_key_hex),
        public_key_hex=req.public_key_hex,
        valid_from=previous.valid_until,
        valid_until=req.valid_until,
        previous_key_id=previous.key_id,
    )
    try:
        result = _registry.register_key(record, actor_id=principal.principal_id)
    except Exception as exc:
        _raise_registry_error(exc)
        raise AssertionError("unreachable")
    return _mutation_response(result, principal)


@router.post("/authorities/{authority_id}/keys/{key_id}/revoke")
def revoke_authority_key(
    authority_id: str,
    key_id: str,
    req: RevokeKeyRequest,
    principal: Principal = Depends(require_capability("federation:key:revoke")),
) -> dict[str, object]:
    try:
        result = _registry.revoke_key(
            authority_id,
            key_id,
            effective_at=req.effective_at,
            reason=req.reason,
            actor_id=principal.principal_id,
        )
    except Exception as exc:
        _raise_registry_error(exc)
        raise AssertionError("unreachable")
    return _mutation_response(result, principal)


@router.get("/authorities/{authority_id}/keys")
def list_authority_keys(
    authority_id: str,
    principal: Principal = Depends(require_capability("federation:key:read")),
) -> dict[str, object]:
    return {
        "authority_id": authority_id,
        "keys": [asdict(record) for record in _registry.list_authority_keys(authority_id)],
        "read_by": principal.principal_id,
    }


@router.get("/keys/{key_id}")
def read_authority_key(
    key_id: str,
    principal: Principal = Depends(require_capability("federation:key:read")),
) -> dict[str, object]:
    record = _registry.get_key(key_id)
    if record is None:
        raise HTTPException(status_code=404, detail="authority key not found")
    return {"record": asdict(record), "read_by": principal.principal_id}


@router.post("/relations/{relation_id}/key-binding")
def bind_relation_keys(
    relation_id: str,
    req: RelationKeyBindingCreate,
    principal: Principal = Depends(require_capability("federation:relation:bind-key")),
) -> dict[str, object]:
    relation = FederationRelation(
        relation_id=relation_id,
        source_domain=req.source_domain,
        target_domain=req.target_domain,
        source_authority=req.source_authority,
        target_authority=req.target_authority,
        contract_hash=req.contract_hash,
        capabilities=req.capabilities,
        valid_from=req.valid_from,
        valid_until=req.valid_until,
        status=RelationStatus.ACTIVE,
        evidence_policy=req.evidence_policy,
        witness_scheme=ED25519_PUBLIC_KEY_SCHEME,
        witness=Ed25519RelationWitness(
            scheme=ED25519_PUBLIC_KEY_SCHEME,
            payload_sha256=req.witness_payload_sha256,
            source_signature_hex=req.source_signature_hex,
            target_signature_hex=req.target_signature_hex,
        ),
    )
    try:
        result = _registry.register_relation_binding(
            relation,
            source_key_id=req.source_key_id,
            target_key_id=req.target_key_id,
            actor_id=principal.principal_id,
        )
    except Exception as exc:
        _raise_registry_error(exc)
        raise AssertionError("unreachable")
    return _mutation_response(result, principal)


@router.get("/relations/{relation_id}/key-binding")
def read_relation_binding(
    relation_id: str,
    principal: Principal = Depends(require_capability("federation:key:read")),
) -> dict[str, object]:
    binding = _registry.get_relation_binding(relation_id)
    if binding is None:
        raise HTTPException(status_code=404, detail="relation key binding not found")
    return {"binding": asdict(binding), "read_by": principal.principal_id}
