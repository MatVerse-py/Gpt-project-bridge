from __future__ import annotations

import hashlib
import json
import time
import uuid

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import auth_trust_plane, institutional_state_client, principal_registry
from app.auth import Principal, sign_request_ed25519
from app.auth_trust_plane import PrincipalCreate, RevokeRequest
from app.institutional_service import app as institutional_app
from app.institutional_state_client import InstitutionalStateUnavailable
from app.principal_registry import ED25519_AUTH_SCHEME, principal_key_id

ATTACKER_PRIVATE = Ed25519PrivateKey.from_private_bytes(bytes.fromhex("91" * 32))


def _public_hex(private_key: Ed25519PrivateKey) -> str:
    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()


ATTACKER_PUBLIC = _public_hex(ATTACKER_PRIVATE)
ATTACKER_KEY_ID = principal_key_id(ATTACKER_PUBLIC)


def _signed_get(client: TestClient, *, timestamp: int | None = None):
    path = "/trust/auth/principals/attacker"
    body = b""
    content_hash = hashlib.sha256(body).hexdigest()
    timestamp_text = str(int(time.time()) if timestamp is None else timestamp)
    nonce = uuid.uuid4().hex
    signature = sign_request_ed25519(
        ATTACKER_PRIVATE,
        "attacker",
        ATTACKER_KEY_ID,
        "GET",
        path,
        timestamp_text,
        nonce,
        content_hash,
    )
    return client.get(
        path,
        headers={
            "X-MatVerse-Principal": "attacker",
            "X-MatVerse-Auth-Scheme": ED25519_AUTH_SCHEME,
            "X-MatVerse-Key-Id": ATTACKER_KEY_ID,
            "X-MatVerse-Timestamp": timestamp_text,
            "X-MatVerse-Nonce": nonce,
            "X-MatVerse-Content-SHA256": content_hash,
            "X-MatVerse-Signature": signature,
        },
    )


def _remote_payload(*, principal_id: str, valid_from: int, valid_until: int) -> dict[str, object]:
    return {
        "auth_scheme": ED25519_AUTH_SCHEME,
        "principal": {
            "principal_id": principal_id,
            "capabilities": ["*"],
            "status": "ACTIVE",
            "created_by": "external-root",
            "created_at": "2026-08-30T00:00:00+00:00",
            "revoked_at": None,
            "revocation_reason": None,
        },
        "key": {
            "principal_id": "attacker",
            "key_id": ATTACKER_KEY_ID,
            "public_key_hex": ATTACKER_PUBLIC,
            "valid_from": valid_from,
            "valid_until": valid_until,
            "previous_key_id": None,
            "registered_by": "external-root",
            "registered_at": "2026-08-30T00:00:00+00:00",
            "revoked_at": None,
            "revocation_reason": None,
        },
        "private_material_present": False,
    }


def test_missing_auth_mode_fails_closed_before_legacy_fallback(monkeypatch):
    monkeypatch.delenv("MATVERSE_AUTH_MODE", raising=False)
    response = TestClient(institutional_app).get("/trust/auth/principals/anything")
    assert response.status_code == 503
    assert "explicitly configured" in response.json()["detail"]


def test_remote_state_plain_http_is_forbidden_except_internal_in_process_host(monkeypatch):
    monkeypatch.setenv("MATVERSE_INSTITUTIONAL_STATE_URL", "http://attacker.example")
    with pytest.raises(InstitutionalStateUnavailable, match="authenticated https"):
        institutional_state_client._base_url()

    monkeypatch.setenv("MATVERSE_INSTITUTIONAL_STATE_URL", "http://state.matverse.internal")
    assert institutional_state_client._base_url() == "http://state.matverse.internal"


def test_remote_credential_identity_mixup_cannot_escalate_capabilities(monkeypatch):
    now = int(time.time())
    monkeypatch.setenv("MATVERSE_AUTH_MODE", "ed25519")
    monkeypatch.setattr(principal_registry, "remote_state_enabled", lambda: True)
    monkeypatch.setattr(
        principal_registry,
        "fetch_remote_auth_credential",
        lambda principal_id, key_id: _remote_payload(
            principal_id="privileged-root",
            valid_from=now - 60,
            valid_until=now + 600,
        ),
    )

    response = _signed_get(TestClient(institutional_app))
    assert response.status_code == 401
    assert response.json()["detail"] == "principal credential identity binding mismatch"


def test_backdated_timestamp_cannot_extend_expired_key(monkeypatch):
    now = int(time.time())
    monkeypatch.setenv("MATVERSE_AUTH_MODE", "ed25519")
    monkeypatch.setattr(principal_registry, "remote_state_enabled", lambda: True)
    monkeypatch.setattr(
        principal_registry,
        "fetch_remote_auth_credential",
        lambda principal_id, key_id: _remote_payload(
            principal_id="attacker",
            valid_from=now - 120,
            valid_until=now - 1,
        ),
    )

    response = _signed_get(TestClient(institutional_app), timestamp=now - 2)
    assert response.status_code == 401
    assert response.json()["detail"] == "principal credential outside validity window"


def test_remote_trust_plane_creation_uses_authoritative_remote_registry(monkeypatch):
    captured: dict[str, object] = {}

    def fake_register(**kwargs):
        captured.update(kwargs)
        return {
            "principal": {
                "principal_id": kwargs["principal_id"],
                "capabilities": kwargs["capabilities"],
                "status": "ACTIVE",
                "created_by": kwargs["actor_id"],
                "created_at": "2026-08-30T00:00:00Z",
                "revoked_at": None,
                "revocation_reason": None,
            },
            "key": {
                "principal_id": kwargs["principal_id"],
                "key_id": principal_key_id(kwargs["public_key_hex"]),
                "public_key_hex": kwargs["public_key_hex"],
                "valid_from": kwargs["valid_from"],
                "valid_until": kwargs["valid_until"],
                "previous_key_id": None,
                "registered_by": kwargs["actor_id"],
                "registered_at": "2026-08-30T00:00:00Z",
                "revoked_at": None,
                "revocation_reason": None,
            },
            "receipt": {"decision": "PASS"},
        }

    monkeypatch.setattr(auth_trust_plane, "remote_state_enabled", lambda: True)
    monkeypatch.setattr(auth_trust_plane, "register_remote_principal", fake_register)
    actor = Principal(
        "root-admin",
        frozenset({"auth:principal:create", "session:read"}),
        auth_scheme=ED25519_AUTH_SCHEME,
        key_id="ed25519:" + "a" * 64,
    )
    request = PrincipalCreate(
        public_key_hex=ATTACKER_PUBLIC,
        capabilities=("session:read",),
        valid_from=100,
        valid_until=200,
    )
    result = auth_trust_plane.create_principal("child", request, actor)

    assert captured["principal_id"] == "child"
    assert captured["actor_id"] == "root-admin"
    assert captured["capabilities"] == ["session:read"]
    assert result["principal"]["principal_id"] == "child"
    assert result["authenticated_actor"] == "root-admin"


def test_future_principal_revocation_schedule_is_explicitly_rejected():
    actor = Principal(
        "root-admin",
        frozenset({"*"}),
        auth_scheme=ED25519_AUTH_SCHEME,
        key_id="ed25519:" + "b" * 64,
    )
    request = RevokeRequest(effective_at=int(time.time()) + 3600, reason="scheduled retirement")
    with pytest.raises(HTTPException) as exc_info:
        auth_trust_plane.revoke_principal("child", request, actor)
    assert exc_info.value.status_code == 422
    assert "future principal revocation scheduling is not supported" in str(exc_info.value.detail)
