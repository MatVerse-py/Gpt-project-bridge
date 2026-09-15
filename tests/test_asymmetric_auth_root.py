from __future__ import annotations

import hashlib
import json
import time
import uuid

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from app import storage
from app.auth import sign_request, sign_request_ed25519
from app.institutional_service import app as institutional_app
from app.principal_registry import ED25519_AUTH_SCHEME, principal_key_id

ROOT_PRIVATE = Ed25519PrivateKey.from_private_bytes(bytes.fromhex("71" * 32))
ROOT_ROTATED_PRIVATE = Ed25519PrivateKey.from_private_bytes(bytes.fromhex("72" * 32))
CHILD_PRIVATE = Ed25519PrivateKey.from_private_bytes(bytes.fromhex("73" * 32))
DELEGATE_PRIVATE = Ed25519PrivateKey.from_private_bytes(bytes.fromhex("74" * 32))


def _public_hex(private_key: Ed25519PrivateKey) -> str:
    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()


ROOT_PUBLIC = _public_hex(ROOT_PRIVATE)
ROOT_ROTATED_PUBLIC = _public_hex(ROOT_ROTATED_PRIVATE)
CHILD_PUBLIC = _public_hex(CHILD_PRIVATE)
DELEGATE_PUBLIC = _public_hex(DELEGATE_PRIVATE)
ROOT_KEY_ID = principal_key_id(ROOT_PUBLIC)
ROOT_ROTATED_KEY_ID = principal_key_id(ROOT_ROTATED_PUBLIC)
CHILD_KEY_ID = principal_key_id(CHILD_PUBLIC)
DELEGATE_KEY_ID = principal_key_id(DELEGATE_PUBLIC)


@pytest.fixture
def institutional_client() -> TestClient:
    return TestClient(institutional_app)


@pytest.fixture
def asymmetric_env(monkeypatch):
    now = int(time.time())
    monkeypatch.setenv("MATVERSE_AUTH_MODE", "ed25519")
    monkeypatch.delenv("MATVERSE_INSTITUTIONAL_STATE_URL", raising=False)
    monkeypatch.setenv(
        "MATVERSE_BOOTSTRAP_ROOT_JSON",
        json.dumps(
            {
                "principal_id": "root-admin",
                "public_key_hex": ROOT_PUBLIC,
                "capabilities": ["*"],
                "valid_from": now - 60,
                "valid_until": now + 7200,
            },
            sort_keys=True,
        ),
    )
    return now


def _body_bytes(payload) -> bytes:
    return b"" if payload is None else json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _ed_request(
    client: TestClient,
    private_key: Ed25519PrivateKey,
    principal_id: str,
    key_id: str,
    method: str,
    path: str,
    payload=None,
    *,
    nonce: str | None = None,
    timestamp: int | None = None,
    signature_override: str | None = None,
):
    body = _body_bytes(payload)
    content_hash = hashlib.sha256(body).hexdigest()
    timestamp_value = str(timestamp if timestamp is not None else int(time.time()))
    nonce_value = nonce or uuid.uuid4().hex
    signature = sign_request_ed25519(
        private_key,
        principal_id,
        key_id,
        method,
        path,
        timestamp_value,
        nonce_value,
        content_hash,
    )
    headers = {
        "X-MatVerse-Principal": principal_id,
        "X-MatVerse-Auth-Scheme": ED25519_AUTH_SCHEME,
        "X-MatVerse-Key-Id": key_id,
        "X-MatVerse-Timestamp": timestamp_value,
        "X-MatVerse-Nonce": nonce_value,
        "X-MatVerse-Content-SHA256": content_hash,
        "X-MatVerse-Signature": signature_override or signature,
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
    return client.request(method, path, content=body, headers=headers)


def _register_principal(
    client: TestClient,
    private_key: Ed25519PrivateKey,
    actor_id: str,
    actor_key_id: str,
    target_id: str,
    target_public: str,
    capabilities: list[str],
    now: int,
):
    return _ed_request(
        client,
        private_key,
        actor_id,
        actor_key_id,
        "POST",
        f"/trust/auth/principals/{target_id}",
        {
            "public_key_hex": target_public,
            "capabilities": capabilities,
            "valid_from": now - 10,
            "valid_until": now + 3600,
        },
    )


def _events() -> list[dict[str, object]]:
    return [json.loads(row["event_json"]) for row in storage.read_ledger()]


def test_public_only_bootstrap_is_persisted_and_ledgered(institutional_client, asymmetric_env):
    response = _ed_request(
        institutional_client,
        ROOT_PRIVATE,
        "root-admin",
        ROOT_KEY_ID,
        "GET",
        "/trust/auth/principals/root-admin",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["principal"]["principal_id"] == "root-admin"
    assert body["keys"][0]["key_id"] == ROOT_KEY_ID

    events = _events()
    assert events[0]["event_type"] == "AUTH_ROOT_PRINCIPAL_BOOTSTRAPPED"
    assert events[0]["private_material_present"] is False
    serialized = json.dumps(events[0], sort_keys=True)
    assert "private_key" not in serialized
    assert "secret" not in serialized


def test_ed25519_mode_does_not_downgrade_to_hmac(institutional_client, asymmetric_env):
    path = "/trust/auth/principals/root-admin"
    body = b""
    content_hash = hashlib.sha256(body).hexdigest()
    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex
    secret = "admin-secret-32-bytes-minimum-0001"
    signature = sign_request(secret, "GET", path, timestamp, nonce, content_hash)
    response = institutional_client.get(
        path,
        headers={
            "X-MatVerse-Principal": "admin",
            "X-MatVerse-Timestamp": timestamp,
            "X-MatVerse-Nonce": nonce,
            "X-MatVerse-Content-SHA256": content_hash,
            "X-MatVerse-Signature": signature,
        },
    )
    assert response.status_code == 401
    assert "asymmetric" in response.json()["detail"]


def test_invalid_ed25519_signature_cannot_authenticate(institutional_client, asymmetric_env):
    response = _ed_request(
        institutional_client,
        ROOT_PRIVATE,
        "root-admin",
        ROOT_KEY_ID,
        "GET",
        "/trust/auth/principals/root-admin",
        signature_override="0" * 128,
    )
    assert response.status_code == 401
    assert [event["event_type"] for event in _events()] == ["AUTH_ROOT_PRINCIPAL_BOOTSTRAPPED"]


def test_nonce_replay_is_rejected_after_first_asymmetric_request(institutional_client, asymmetric_env):
    nonce = "0123456789abcdef0123456789abcdef"
    first = _ed_request(
        institutional_client,
        ROOT_PRIVATE,
        "root-admin",
        ROOT_KEY_ID,
        "GET",
        "/trust/auth/principals/root-admin",
        nonce=nonce,
    )
    assert first.status_code == 200
    second = _ed_request(
        institutional_client,
        ROOT_PRIVATE,
        "root-admin",
        ROOT_KEY_ID,
        "GET",
        "/trust/auth/principals/root-admin",
        nonce=nonce,
    )
    assert second.status_code == 409


def test_delegate_cannot_grant_capability_it_does_not_hold(institutional_client, asymmetric_env):
    now = asymmetric_env
    created = _register_principal(
        institutional_client,
        ROOT_PRIVATE,
        "root-admin",
        ROOT_KEY_ID,
        "delegate",
        DELEGATE_PUBLIC,
        ["auth:principal:create"],
        now,
    )
    assert created.status_code == 200

    escalation = _register_principal(
        institutional_client,
        DELEGATE_PRIVATE,
        "delegate",
        DELEGATE_KEY_ID,
        "escalated",
        CHILD_PUBLIC,
        ["*"],
        now,
    )
    assert escalation.status_code == 403
    event_types = [event["event_type"] for event in _events()]
    assert event_types.count("AUTH_PRINCIPAL_REGISTERED") == 1


def test_root_rotation_then_old_key_revocation_invalidates_old_credential(institutional_client, asymmetric_env):
    now = asymmetric_env
    assert _ed_request(
        institutional_client,
        ROOT_PRIVATE,
        "root-admin",
        ROOT_KEY_ID,
        "GET",
        "/trust/auth/principals/root-admin",
    ).status_code == 200

    rotate_path = f"/trust/auth/principals/root-admin/keys/{ROOT_KEY_ID}/rotate"
    rotated = _ed_request(
        institutional_client,
        ROOT_PRIVATE,
        "root-admin",
        ROOT_KEY_ID,
        "POST",
        rotate_path,
        {
            "public_key_hex": ROOT_ROTATED_PUBLIC,
            "valid_from": now - 5,
            "valid_until": now + 10800,
        },
    )
    assert rotated.status_code == 200
    assert rotated.json()["key"]["previous_key_id"] == ROOT_KEY_ID
    assert rotated.json()["key"]["key_id"] == ROOT_ROTATED_KEY_ID

    revoke_path = f"/trust/auth/principals/root-admin/keys/{ROOT_KEY_ID}/revoke"
    revoked = _ed_request(
        institutional_client,
        ROOT_PRIVATE,
        "root-admin",
        ROOT_KEY_ID,
        "POST",
        revoke_path,
        {"effective_at": now, "reason": "rotation completed"},
        timestamp=now - 1,
    )
    assert revoked.status_code == 200
    assert revoked.json()["key"]["revoked_at"] == now

    old_credential = _ed_request(
        institutional_client,
        ROOT_PRIVATE,
        "root-admin",
        ROOT_KEY_ID,
        "GET",
        "/trust/auth/principals/root-admin",
        timestamp=now + 1,
    )
    assert old_credential.status_code == 401
    assert old_credential.json()["detail"] == "principal credential revoked"

    new_credential = _ed_request(
        institutional_client,
        ROOT_ROTATED_PRIVATE,
        "root-admin",
        ROOT_ROTATED_KEY_ID,
        "GET",
        "/trust/auth/principals/root-admin",
        timestamp=now + 1,
    )
    assert new_credential.status_code == 200


def test_principal_revocation_is_terminal_for_authentication(institutional_client, asymmetric_env):
    now = asymmetric_env
    created = _register_principal(
        institutional_client,
        ROOT_PRIVATE,
        "root-admin",
        ROOT_KEY_ID,
        "child",
        CHILD_PUBLIC,
        ["auth:principal:read"],
        now,
    )
    assert created.status_code == 200
    child_before = _ed_request(
        institutional_client,
        CHILD_PRIVATE,
        "child",
        CHILD_KEY_ID,
        "GET",
        "/trust/auth/principals/child",
    )
    assert child_before.status_code == 200

    revoked = _ed_request(
        institutional_client,
        ROOT_PRIVATE,
        "root-admin",
        ROOT_KEY_ID,
        "POST",
        "/trust/auth/principals/child/revoke",
        {"effective_at": now, "reason": "access terminated"},
    )
    assert revoked.status_code == 200
    assert revoked.json()["principal"]["status"] == "REVOKED"

    child_after = _ed_request(
        institutional_client,
        CHILD_PRIVATE,
        "child",
        CHILD_KEY_ID,
        "GET",
        "/trust/auth/principals/child",
        timestamp=now + 1,
    )
    assert child_after.status_code == 401
    assert child_after.json()["detail"] == "principal revoked"


def test_public_credential_endpoint_exposes_verifier_material_only(institutional_client, asymmetric_env):
    response = institutional_client.get(f"/v1/auth/credentials/root-admin/{ROOT_KEY_ID}")
    assert response.status_code == 200
    body = response.json()
    assert body["auth_scheme"] == ED25519_AUTH_SCHEME
    assert body["principal"]["principal_id"] == "root-admin"
    assert body["key"]["public_key_hex"] == ROOT_PUBLIC
    assert body["private_material_present"] is False
    serialized = json.dumps(body, sort_keys=True)
    assert "private_key" not in serialized
    assert "secret" not in serialized
