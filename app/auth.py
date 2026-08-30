from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Callable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from fastapi import HTTPException, Request

from .institutional_state_client import InstitutionalStateUnavailable, consume_auth_nonce
from .principal_registry import (
    ED25519_AUTH_SCHEME,
    PrincipalRegistryUnavailable,
    principal_key_id,
    resolve_principal_credential,
)

AUTH_WINDOW_SECONDS = 300
AUTH_MODE_ENV = "MATVERSE_AUTH_MODE"
LEGACY_HMAC_MODE = "legacy-hmac"
ED25519_MODE = "ed25519"
_SIGNATURE_RE = re.compile(r"^[0-9a-f]{128}$")


@dataclass(frozen=True)
class Principal:
    principal_id: str
    capabilities: frozenset[str]
    auth_scheme: str = "HMAC-SHA256-LEGACY"
    key_id: str | None = None

    def allows(self, capability: str) -> bool:
        return "*" in self.capabilities or capability in self.capabilities


def _auth_mode() -> str:
    mode = os.environ.get(AUTH_MODE_ENV, LEGACY_HMAC_MODE).strip().lower()
    if mode not in {LEGACY_HMAC_MODE, ED25519_MODE}:
        raise RuntimeError(
            f"{AUTH_MODE_ENV} must be one of {LEGACY_HMAC_MODE!r} or {ED25519_MODE!r}; no implicit fallback is allowed"
        )
    return mode


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


def signing_payload_ed25519(
    principal_id: str,
    key_id: str,
    method: str,
    path: str,
    timestamp: str,
    nonce: str,
    content_sha256: str,
) -> bytes:
    return "\n".join(
        [
            "matverse.auth-request.v2",
            ED25519_AUTH_SCHEME,
            principal_id,
            key_id,
            method.upper(),
            path,
            timestamp,
            nonce,
            content_sha256,
        ]
    ).encode("utf-8")


def sign_request_ed25519(
    private_key: Ed25519PrivateKey,
    principal_id: str,
    key_id: str,
    method: str,
    path: str,
    timestamp: str,
    nonce: str,
    content_sha256: str,
) -> str:
    if not isinstance(private_key, Ed25519PrivateKey):
        raise ValueError("private_key must be an Ed25519PrivateKey")
    raw_public = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()
    if principal_key_id(raw_public) != key_id:
        raise ValueError("key_id does not match private_key public material")
    return private_key.sign(
        signing_payload_ed25519(
            principal_id,
            key_id,
            method,
            path,
            timestamp,
            nonce,
            content_sha256,
        )
    ).hex()


def _common_headers(request: Request) -> tuple[str, str, str, str, str]:
    principal_id = request.headers.get("X-MatVerse-Principal", "")
    timestamp = request.headers.get("X-MatVerse-Timestamp", "")
    nonce = request.headers.get("X-MatVerse-Nonce", "")
    content_sha256 = request.headers.get("X-MatVerse-Content-SHA256", "")
    signature = request.headers.get("X-MatVerse-Signature", "")
    if not all([principal_id, timestamp, nonce, content_sha256, signature]):
        raise HTTPException(status_code=401, detail="missing MatVerse authentication headers")
    return principal_id, timestamp, nonce, content_sha256, signature


def _validate_time_and_nonce(timestamp: str, nonce: str) -> int:
    try:
        ts = int(timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="invalid authentication timestamp") from exc
    now = int(time.time())
    if abs(now - ts) > AUTH_WINDOW_SECONDS:
        raise HTTPException(status_code=401, detail="authentication timestamp outside allowed window")
    if len(nonce) < 16 or len(nonce) > 128:
        raise HTTPException(status_code=401, detail="invalid authentication nonce")
    return ts


async def _validate_content_hash(request: Request, content_sha256: str) -> None:
    body = await request.body()
    actual_content_hash = hashlib.sha256(body).hexdigest()
    if not hmac.compare_digest(actual_content_hash, content_sha256):
        raise HTTPException(status_code=401, detail="content hash mismatch")


def _consume_nonce_or_fail(principal_id: str, nonce: str, expires_at: int) -> None:
    try:
        consumed = consume_auth_nonce(principal_id, nonce, expires_at)
    except InstitutionalStateUnavailable as exc:
        raise HTTPException(status_code=503, detail="authentication nonce registry unavailable") from exc
    if not consumed:
        raise HTTPException(status_code=409, detail="authentication nonce replayed")


async def _authenticate_legacy_hmac(request: Request) -> Principal:
    principal_id, timestamp, nonce, content_sha256, signature = _common_headers(request)
    registry = _registry()
    record = registry.get(principal_id)
    if not isinstance(record, dict) or not isinstance(record.get("secret"), str):
        raise HTTPException(status_code=401, detail="unknown principal")
    ts = _validate_time_and_nonce(timestamp, nonce)
    await _validate_content_hash(request, content_sha256)
    expected = sign_request(record["secret"], request.method, request.url.path, timestamp, nonce, content_sha256)
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="invalid request signature")
    _consume_nonce_or_fail(principal_id, nonce, ts + AUTH_WINDOW_SECONDS)
    capabilities = record.get("capabilities", [])
    if not isinstance(capabilities, list) or not all(isinstance(item, str) for item in capabilities):
        raise HTTPException(status_code=401, detail="principal capability registry invalid")
    return Principal(principal_id, frozenset(capabilities))


async def _authenticate_ed25519(request: Request) -> Principal:
    principal_id, timestamp, nonce, content_sha256, signature = _common_headers(request)
    scheme = request.headers.get("X-MatVerse-Auth-Scheme", "")
    key_id = request.headers.get("X-MatVerse-Key-Id", "")
    if scheme != ED25519_AUTH_SCHEME or not key_id:
        raise HTTPException(status_code=401, detail="missing or unsupported asymmetric authentication headers")
    if _SIGNATURE_RE.fullmatch(signature) is None:
        raise HTTPException(status_code=401, detail="invalid Ed25519 request signature encoding")
    ts = _validate_time_and_nonce(timestamp, nonce)
    await _validate_content_hash(request, content_sha256)
    try:
        credential = resolve_principal_credential(principal_id, key_id)
    except PrincipalRegistryUnavailable as exc:
        raise HTTPException(status_code=503, detail="asymmetric principal registry unavailable") from exc
    if credential is None:
        raise HTTPException(status_code=401, detail="unknown principal credential")
    principal_record = credential.principal
    key_record = credential.key
    observed_at = int(time.time())
    if principal_record.status != "ACTIVE":
        raise HTTPException(status_code=401, detail="principal revoked")
    if ts < key_record.valid_from or ts >= key_record.valid_until:
        raise HTTPException(status_code=401, detail="principal credential outside validity window")
    if key_record.revoked_at is not None and observed_at >= key_record.revoked_at:
        raise HTTPException(status_code=401, detail="principal credential revoked")
    try:
        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(key_record.public_key_hex))
        public_key.verify(
            bytes.fromhex(signature),
            signing_payload_ed25519(
                principal_id,
                key_id,
                request.method,
                request.url.path,
                timestamp,
                nonce,
                content_sha256,
            ),
        )
    except (ValueError, InvalidSignature) as exc:
        raise HTTPException(status_code=401, detail="invalid request signature") from exc
    _consume_nonce_or_fail(principal_id, nonce, ts + AUTH_WINDOW_SECONDS)
    return Principal(
        principal_id=principal_record.principal_id,
        capabilities=frozenset(principal_record.capabilities),
        auth_scheme=ED25519_AUTH_SCHEME,
        key_id=key_record.key_id,
    )


async def authenticate(request: Request) -> Principal:
    try:
        mode = _auth_mode()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if mode == LEGACY_HMAC_MODE:
        return await _authenticate_legacy_hmac(request)
    return await _authenticate_ed25519(request)


def require_capability(capability: str) -> Callable[[Request], Any]:
    async def dependency(request: Request) -> Principal:
        principal = await authenticate(request)
        if not principal.allows(capability):
            raise HTTPException(status_code=403, detail=f"principal lacks capability: {capability}")
        return principal
    return dependency
