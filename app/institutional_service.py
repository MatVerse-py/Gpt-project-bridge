from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request

from .auth import Principal, authenticate
from .institutional_projection import (
    ProjectionUnavailable,
    build_institutional_projection,
    jcs_subset_hash,
)
from .institutional_store import get_intent, list_intents_for_principal, persist_intent
from .model_bridge import assert_transferable_state


app = FastAPI(title="MatVerse Institutional Adapter v1", version="1.0.0")

_ALLOWED_INTENT_OPERATIONS = {
    "REGISTER_ARTIFACT",
    "REGISTER_CLAIM",
    "REGISTER_EVIDENCE",
    "REGISTER_RELATION",
    "REQUEST_MATURITY_EVALUATION",
    "REQUEST_EXTERNAL_REPRODUCTION",
    "REQUEST_WORLD_REAL_EVALUATION",
    "REQUEST_PUBLICATION",
    "REQUEST_ANCHOR",
    "REQUEST_AUTHORIZATION",
    "OTHER",
}
_ALLOWED_TARGET_KINDS = {"SYSTEM", "SUBJECT", "ARTIFACT", "CLAIM", "EXPERIMENT", "RELATION", "MATURITY", "OTHER"}
_REQUIRED_INTENT_KEYS = {
    "schema_version",
    "intent_id",
    "requested_operation",
    "actor_id",
    "target",
    "parameters",
    "created_at",
    "source",
    "intent_hash",
    "hash_algorithm",
    "canonicalization",
    "hash_excludes",
}
_REQUIRED_SOURCE_KEYS = {
    "repository",
    "commit_sha",
    "ref",
    "frozen_contract_hash",
    "gate_fingerprint",
    "constitutional_contract_hash",
    "projection_hash",
}


def _requires(principal: Principal, capability: str, any_capability: str | None = None) -> None:
    if principal.allows(capability):
        return
    if any_capability is not None and principal.allows(any_capability):
        return
    raise HTTPException(status_code=403, detail=f"principal lacks capability: {capability}")


def _nonempty_string(value: Any, field: str, *, max_length: int = 256) -> str:
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise HTTPException(status_code=422, detail=f"{field} must be a non-empty string <= {max_length} characters")
    return value


def _validate_intent_shape(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise HTTPException(status_code=422, detail="intent payload must be an object")
    keys = set(raw)
    missing = sorted(_REQUIRED_INTENT_KEYS - keys)
    extra = sorted(keys - _REQUIRED_INTENT_KEYS)
    if missing or extra:
        raise HTTPException(status_code=422, detail={"missing": missing, "unexpected": extra})
    if raw.get("schema_version") != "matverse.institutional-intent.v1":
        raise HTTPException(status_code=422, detail="unsupported institutional intent schema_version")
    _nonempty_string(raw.get("intent_id"), "intent_id")
    _nonempty_string(raw.get("actor_id"), "actor_id")
    operation = raw.get("requested_operation")
    if operation not in _ALLOWED_INTENT_OPERATIONS:
        raise HTTPException(status_code=422, detail="unsupported requested_operation")
    target = raw.get("target")
    if not isinstance(target, dict) or set(target) != {"kind", "id"}:
        raise HTTPException(status_code=422, detail="target must contain exactly kind and id")
    if target.get("kind") not in _ALLOWED_TARGET_KINDS:
        raise HTTPException(status_code=422, detail="unsupported target kind")
    _nonempty_string(target.get("id"), "target.id")
    parameters = raw.get("parameters")
    if not isinstance(parameters, dict):
        raise HTTPException(status_code=422, detail="parameters must be an object")
    try:
        assert_transferable_state({"metadata": parameters})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        jcs_subset_hash(parameters)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    created_at = raw.get("created_at")
    if not isinstance(created_at, str):
        raise HTTPException(status_code=422, detail="created_at must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="created_at must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise HTTPException(status_code=422, detail="created_at must include an explicit timezone")
    source = raw.get("source")
    if not isinstance(source, dict) or set(source) != _REQUIRED_SOURCE_KEYS:
        raise HTTPException(status_code=422, detail="source must contain the complete institutional source binding")
    if raw.get("hash_algorithm") != "SHA-256":
        raise HTTPException(status_code=422, detail="hash_algorithm must be SHA-256")
    if raw.get("canonicalization") != "RFC8785_JCS":
        raise HTTPException(status_code=422, detail="canonicalization must be RFC8785_JCS")
    if raw.get("hash_excludes") != ["intent_hash"]:
        raise HTTPException(status_code=422, detail="hash_excludes must equal ['intent_hash']")
    intent_hash = raw.get("intent_hash")
    if not isinstance(intent_hash, str) or len(intent_hash) != 64 or any(ch not in "0123456789abcdef" for ch in intent_hash):
        raise HTTPException(status_code=422, detail="intent_hash must be a lowercase SHA-256 digest")
    canonical_payload = dict(raw)
    canonical_payload.pop("intent_hash")
    try:
        expected_hash = jcs_subset_hash(canonical_payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if intent_hash != expected_hash:
        raise HTTPException(status_code=422, detail="intent_hash mismatch")
    return raw


def _current_projection_or_503() -> dict[str, Any]:
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


def _assert_current_source(intent_source: dict[str, Any], projection: dict[str, Any]) -> None:
    expected = {
        **projection["source"],
        "projection_hash": projection["projection"]["projection_hash"],
    }
    if intent_source != expected:
        raise HTTPException(
            status_code=409,
            detail={
                "decision": "HOLD",
                "reason": "intent source binding is stale or does not match the current canonical projection",
                "current_projection_hash": projection["projection"]["projection_hash"],
            },
        )


def _accepted_response(stored: dict[str, Any]) -> dict[str, Any]:
    return {
        "acceptance_decision": "PASS",
        "acceptance_reason": "authenticated, source-bound institutional intent accepted for canonical evaluation",
        "execution_decision": "HOLD",
        "execution_reason": "intent acceptance does not authorize or execute the requested operation",
        **stored,
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "matverse-institutional-adapter-v1"}


@app.get("/institutional/projection")
def institutional_projection(principal: Principal = Depends(authenticate)) -> dict[str, Any]:
    _requires(principal, "institutional:projection:read")
    return _current_projection_or_503()


@app.post("/institutional/intents")
async def submit_institutional_intent(request: Request, principal: Principal = Depends(authenticate)) -> dict[str, Any]:
    _requires(principal, "institutional:intent:submit")
    try:
        raw = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="request body must be valid JSON") from exc
    intent = _validate_intent_shape(raw)
    if principal.principal_id != intent["actor_id"] and not principal.allows("institutional:intent:submit:any"):
        raise HTTPException(status_code=403, detail="authenticated principal does not match intent actor_id")

    # Idempotent retries are checked before freshness. The original acceptance
    # changes the Ledger/projection, so requiring the old projection to remain
    # current would make a legitimate retry impossible. The stored principal
    # and content hash still have to match exactly.
    existing = get_intent(intent["intent_id"])
    if existing is not None:
        if existing["principal_id"] != principal.principal_id or existing["intent_hash"] != intent["intent_hash"]:
            raise HTTPException(status_code=409, detail="intent_id collision or principal/content mismatch")
        existing = {**existing, "idempotent": True}
        return _accepted_response(existing)

    projection = _current_projection_or_503()
    _assert_current_source(intent["source"], projection)
    try:
        stored = persist_intent(intent=intent, principal_id=principal.principal_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _accepted_response(stored)


@app.get("/institutional/intents/{intent_id}")
def read_institutional_intent(intent_id: str, principal: Principal = Depends(authenticate)) -> dict[str, Any]:
    _requires(principal, "institutional:intent:read")
    item = get_intent(intent_id)
    if item is None:
        raise HTTPException(status_code=404, detail="institutional intent not found")
    if item["principal_id"] != principal.principal_id and not principal.allows("institutional:intent:read:any"):
        raise HTTPException(status_code=403, detail="principal may not read another actor's intent")
    return item


@app.get("/institutional/intents")
def list_institutional_intents(principal: Principal = Depends(authenticate)) -> dict[str, Any]:
    _requires(principal, "institutional:intent:read")
    return {"principal_id": principal.principal_id, "intents": list_intents_for_principal(principal.principal_id)}
