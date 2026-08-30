from __future__ import annotations

import hashlib
import json
import time
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from app.auth import sign_request_ed25519
from app.institutional_service import app
from app.principal_registry import ED25519_AUTH_SCHEME, principal_key_id

ROOT_PRIVATE = Ed25519PrivateKey.from_private_bytes(bytes.fromhex("81" * 32))
ROTATED_PRIVATE = Ed25519PrivateKey.from_private_bytes(bytes.fromhex("82" * 32))


def _public_hex(private_key: Ed25519PrivateKey) -> str:
    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()


ROOT_PUBLIC = _public_hex(ROOT_PRIVATE)
ROTATED_PUBLIC = _public_hex(ROTATED_PRIVATE)
ROOT_KEY_ID = principal_key_id(ROOT_PUBLIC)
ROTATED_KEY_ID = principal_key_id(ROTATED_PUBLIC)


def _request(
    client: TestClient,
    private_key: Ed25519PrivateKey,
    key_id: str,
    method: str,
    path: str,
    payload=None,
    *,
    timestamp: int,
):
    body = b"" if payload is None else json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    content_hash = hashlib.sha256(body).hexdigest()
    timestamp_text = str(timestamp)
    nonce = uuid.uuid4().hex
    signature = sign_request_ed25519(
        private_key,
        "root-admin",
        key_id,
        method,
        path,
        timestamp_text,
        nonce,
        content_hash,
    )
    headers = {
        "X-MatVerse-Principal": "root-admin",
        "X-MatVerse-Auth-Scheme": ED25519_AUTH_SCHEME,
        "X-MatVerse-Key-Id": key_id,
        "X-MatVerse-Timestamp": timestamp_text,
        "X-MatVerse-Nonce": nonce,
        "X-MatVerse-Content-SHA256": content_hash,
        "X-MatVerse-Signature": signature,
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
    return client.request(method, path, content=body, headers=headers)


def test_backdated_request_cannot_bypass_effective_key_revocation(monkeypatch):
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
    client = TestClient(app)

    rotate_path = f"/trust/auth/principals/root-admin/keys/{ROOT_KEY_ID}/rotate"
    rotated = _request(
        client,
        ROOT_PRIVATE,
        ROOT_KEY_ID,
        "POST",
        rotate_path,
        {
            "public_key_hex": ROTATED_PUBLIC,
            "valid_from": now - 5,
            "valid_until": now + 10800,
        },
        timestamp=now - 1,
    )
    assert rotated.status_code == 200, rotated.text
    assert rotated.json()["key"]["key_id"] == ROTATED_KEY_ID

    revoke_path = f"/trust/auth/principals/root-admin/keys/{ROOT_KEY_ID}/revoke"
    revoked = _request(
        client,
        ROOT_PRIVATE,
        ROOT_KEY_ID,
        "POST",
        revoke_path,
        {"effective_at": now, "reason": "rotation complete"},
        timestamp=now - 1,
    )
    assert revoked.status_code == 200, revoked.text

    # The request is deliberately backdated to before revoked_at while still
    # inside the allowed anti-skew window. Server observation time must win.
    bypass = _request(
        client,
        ROOT_PRIVATE,
        ROOT_KEY_ID,
        "GET",
        "/trust/auth/principals/root-admin",
        timestamp=now - 1,
    )
    assert bypass.status_code == 401
    assert bypass.json()["detail"] == "principal credential revoked"

    replacement = _request(
        client,
        ROTATED_PRIVATE,
        ROTATED_KEY_ID,
        "GET",
        "/trust/auth/principals/root-admin",
        timestamp=now,
    )
    assert replacement.status_code == 200, replacement.text
