from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import HTTPException, Request

from .institutional_state_client import consume_auth_nonce

AUTH_WINDOW_SECONDS = 300


@dataclass(frozen=True)
class Principal:
    principal_id: str
    capabilities: frozenset[str]

    def allows(self, capability: str) -> bool:
        return "*" in self.capabilities or capability in self.capabilities


def _registry() -> dict[str, dict[str, Any]]:
    raw = os.environ.get("MATVERSE_PRINCIPALS_JSON", "")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("MATVERSE_PRINCIPALS_JSON must be valid JSON") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("MATVERSE_PRINCIPALS_JSON must be an object")
    return parsed


def signing_payload(method: str, path: str, timestamp: str, nonce: str, content_sha256: str) -> bytes:
    return "\n".join([method.upper(), path, timestamp, nonce, content_sha256]).encode("utf-8")


def sign_request(secret: str, method: str, path: str, timestamp: str, nonce: str, content_sha256: str) -> str:
    return hmac.new(secret.encode("utf-8"), signing_payload(method, path, timestamp, nonce, content_sha256), hashlib.sha256).hexdigest()


async def authenticate(request: Request) -> Principal:
    principal_id = request.headers.get("X-MatVerse-Principal", "")
    timestamp = request.headers.get("X-MatVerse-Timestamp", "")
    nonce = request.headers.get("X-MatVerse-Nonce", "")
    content_sha256 = request.headers.get("X-MatVerse-Content-SHA256", "")
    signature = request.headers.get("X-MatVerse-Signature", "")
    if not all([principal_id, timestamp, nonce, content_sha256, signature]):
        raise HTTPException(status_code=401, detail="missing MatVerse authentication headers")
    registry = _registry()
    record = registry.get(principal_id)
    if not isinstance(record, dict) or not isinstance(record.get("secret"), str):
        raise HTTPException(status_code=401, detail="unknown principal")
    try:
        ts = int(timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="invalid authentication timestamp") from exc
    now = int(time.time())
    if abs(now - ts) > AUTH_WINDOW_SECONDS:
        raise HTTPException(status_code=401, detail="authentication timestamp outside allowed window")
    if len(nonce) < 16 or len(nonce) > 128:
        raise HTTPException(status_code=401, detail="invalid authentication nonce")
    body = await request.body()
    actual_content_hash = hashlib.sha256(body).hexdigest()
    if not hmac.compare_digest(actual_content_hash, content_sha256):
        raise HTTPException(status_code=401, detail="content hash mismatch")
    expected = sign_request(record["secret"], request.method, request.url.path, timestamp, nonce, content_sha256)
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="invalid request signature")
    if not consume_auth_nonce(principal_id, nonce, ts + AUTH_WINDOW_SECONDS):
        raise HTTPException(status_code=409, detail="authentication nonce replayed")
    capabilities = record.get("capabilities", [])
    if not isinstance(capabilities, list) or not all(isinstance(item, str) for item in capabilities):
        raise HTTPException(status_code=401, detail="principal capability registry invalid")
    return Principal(principal_id, frozenset(capabilities))


def require_capability(capability: str) -> Callable[[Request], Any]:
    async def dependency(request: Request) -> Principal:
        principal = await authenticate(request)
        if not principal.allows(capability):
            raise HTTPException(status_code=403, detail=f"principal lacks capability: {capability}")
        return principal
    return dependency
