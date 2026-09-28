from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen


_STATE_URL_ENV = "MATVERSE_INSTITUTIONAL_STATE_URL"
_STATE_TIMEOUT_ENV = "MATVERSE_INSTITUTIONAL_STATE_TIMEOUT_SECONDS"
_INTERNAL_STATE_HOST = "state.matverse.internal"


class InstitutionalStateUnavailable(RuntimeError):
    pass


class InstitutionalStateRejected(RuntimeError):
    def __init__(self, status: int, detail: Any):
        super().__init__(f"institutional state service rejected request: status={status} detail={detail!r}")
        self.status = status
        self.detail = detail


def remote_state_enabled() -> bool:
    return bool(os.environ.get(_STATE_URL_ENV, "").strip())


def _base_url() -> str:
    value = os.environ.get(_STATE_URL_ENV, "").strip().rstrip("/")
    if not value:
        raise InstitutionalStateUnavailable(f"{_STATE_URL_ENV} is not configured")
    parsed = urlparse(value)
    if parsed.username or parsed.password or not parsed.hostname:
        raise InstitutionalStateUnavailable(f"{_STATE_URL_ENV} must be a canonical service URL without userinfo")
    if parsed.scheme == "https":
        return value
    if parsed.scheme == "http" and parsed.hostname == _INTERNAL_STATE_HOST:
        return value
    raise InstitutionalStateUnavailable(
        f"{_STATE_URL_ENV} must use authenticated https:// transport; plain http:// is allowed only for {_INTERNAL_STATE_HOST} in-process routing"
    )


def _timeout() -> float:
    raw = os.environ.get(_STATE_TIMEOUT_ENV, "5").strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise InstitutionalStateUnavailable(f"{_STATE_TIMEOUT_ENV} must be numeric") from exc
    if not 0.1 <= value <= 30.0:
        raise InstitutionalStateUnavailable(f"{_STATE_TIMEOUT_ENV} must be between 0.1 and 30 seconds")
    return value


def _request_json(method: str, path: str, *, payload: Any | None = None, query: dict[str, Any] | None = None) -> Any:
    url = _base_url() + path
    if query:
        url += "?" + urlencode({key: str(value) for key, value in query.items()})
    body = None if payload is None else json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(url, data=body, headers=headers, method=method.upper())
    try:
        with urlopen(request, timeout=_timeout()) as response:
            raw = response.read()
    except HTTPError as exc:
        raw = exc.read()
        detail: Any
        try:
            detail = json.loads(raw.decode("utf-8")) if raw else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            detail = raw.decode("utf-8", errors="replace")
        raise InstitutionalStateRejected(exc.code, detail) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise InstitutionalStateUnavailable(f"institutional state service unavailable: {exc}") from exc
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InstitutionalStateUnavailable("institutional state service returned invalid JSON") from exc


def _object_response(result: Any, operation: str) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise InstitutionalStateUnavailable(f"{operation} response must be an object")
    return result


def consume_auth_nonce(principal_id: str, nonce: str, expires_at: int) -> bool:
    if not remote_state_enabled():
        from .storage import consume_auth_nonce as consume_local_auth_nonce

        return consume_local_auth_nonce(principal_id, nonce, expires_at)
    result = _request_json(
        "POST",
        "/v1/nonces/consume",
        payload={"principal_id": principal_id, "nonce": nonce, "expires_at": expires_at},
    )
    if not isinstance(result, dict) or not isinstance(result.get("consumed"), bool):
        raise InstitutionalStateUnavailable("nonce response is missing consumed boolean")
    return bool(result["consumed"])


def fetch_remote_auth_credential(principal_id: str, key_id: str) -> dict[str, Any] | None:
    path = f"/v1/auth/credentials/{quote(principal_id, safe='')}/{quote(key_id, safe='')}"
    try:
        result = _request_json("GET", path)
    except InstitutionalStateRejected as exc:
        if exc.status == 404:
            return None
        raise
    result = _object_response(result, "auth credential")
    principal = result.get("principal")
    key = result.get("key")
    if not isinstance(principal, dict) or not isinstance(key, dict):
        raise InstitutionalStateUnavailable("auth credential response is missing principal/key objects")
    return result


def fetch_remote_principal(principal_id: str) -> dict[str, Any] | None:
    path = f"/v1/auth/principals/{quote(principal_id, safe='')}"
    try:
        result = _request_json("GET", path)
    except InstitutionalStateRejected as exc:
        if exc.status == 404:
            return None
        raise
    result = _object_response(result, "principal lookup")
    if not isinstance(result.get("principal"), dict) or not isinstance(result.get("keys"), list):
        raise InstitutionalStateUnavailable("principal lookup response is invalid")
    return result


def register_remote_principal(
    *,
    principal_id: str,
    capabilities: list[str],
    public_key_hex: str,
    valid_from: int,
    valid_until: int,
    actor_id: str,
) -> dict[str, Any]:
    path = f"/v1/auth/principals/{quote(principal_id, safe='')}"
    return _object_response(
        _request_json(
            "POST",
            path,
            payload={
                "capabilities": capabilities,
                "public_key_hex": public_key_hex,
                "valid_from": valid_from,
                "valid_until": valid_until,
                "actor_id": actor_id,
            },
        ),
        "principal registration",
    )


def rotate_remote_principal_key(
    *,
    principal_id: str,
    previous_key_id: str,
    public_key_hex: str,
    valid_from: int,
    valid_until: int,
    actor_id: str,
) -> dict[str, Any]:
    path = (
        f"/v1/auth/principals/{quote(principal_id, safe='')}/keys/"
        f"{quote(previous_key_id, safe='')}/rotate"
    )
    return _object_response(
        _request_json(
            "POST",
            path,
            payload={
                "public_key_hex": public_key_hex,
                "valid_from": valid_from,
                "valid_until": valid_until,
                "actor_id": actor_id,
            },
        ),
        "principal key rotation",
    )


def revoke_remote_principal_key(
    *,
    principal_id: str,
    key_id: str,
    effective_at: int,
    reason: str,
    actor_id: str,
) -> dict[str, Any]:
    path = (
        f"/v1/auth/principals/{quote(principal_id, safe='')}/keys/"
        f"{quote(key_id, safe='')}/revoke"
    )
    return _object_response(
        _request_json(
            "POST",
            path,
            payload={"effective_at": effective_at, "reason": reason, "actor_id": actor_id},
        ),
        "principal key revocation",
    )


def revoke_remote_principal(
    *,
    principal_id: str,
    effective_at: int,
    reason: str,
    actor_id: str,
) -> dict[str, Any]:
    path = f"/v1/auth/principals/{quote(principal_id, safe='')}/revoke"
    return _object_response(
        _request_json(
            "POST",
            path,
            payload={"effective_at": effective_at, "reason": reason, "actor_id": actor_id},
        ),
        "principal revocation",
    )


def fetch_state_snapshot() -> dict[str, Any]:
    result = _request_json("GET", "/v1/snapshot")
    if not isinstance(result, dict):
        raise InstitutionalStateUnavailable("snapshot response must be an object")
    ledger = result.get("ledger")
    artifacts = result.get("contract_artifacts")
    if not isinstance(ledger, list) or not all(isinstance(row, dict) for row in ledger):
        raise InstitutionalStateUnavailable("snapshot ledger must be a list of objects")
    if not isinstance(artifacts, list) or not all(isinstance(row, dict) for row in artifacts):
        raise InstitutionalStateUnavailable("snapshot contract_artifacts must be a list of objects")
    return {"ledger": ledger, "contract_artifacts": artifacts}


def accept_remote_intent(*, intent: dict[str, Any], principal_id: str, allow_delegated_actor: bool, expected_ledger_head: str) -> dict[str, Any]:
    result = _request_json(
        "POST",
        "/v1/intents/accept",
        payload={
            "intent": intent,
            "principal_id": principal_id,
            "allow_delegated_actor": allow_delegated_actor,
            "expected_ledger_head": expected_ledger_head,
        },
    )
    if not isinstance(result, dict):
        raise InstitutionalStateUnavailable("intent acceptance response must be an object")
    return result


def get_remote_intent(intent_id: str) -> dict[str, Any] | None:
    try:
        result = _request_json("GET", "/v1/intents/item", query={"intent_id": intent_id})
    except InstitutionalStateRejected as exc:
        if exc.status == 404:
            return None
        raise
    if not isinstance(result, dict):
        raise InstitutionalStateUnavailable("intent response must be an object")
    return result


def list_remote_intents(principal_id: str, *, limit: int, offset: int) -> list[dict[str, Any]]:
    result = _request_json(
        "GET",
        "/v1/intents/list",
        query={"principal_id": principal_id, "limit": limit, "offset": offset},
    )
    if not isinstance(result, dict) or not isinstance(result.get("intents"), list):
        raise InstitutionalStateUnavailable("intent list response is invalid")
    intents = result["intents"]
    if not all(isinstance(item, dict) for item in intents):
        raise InstitutionalStateUnavailable("intent list contains non-object entries")
    return intents
