from __future__ import annotations

import hashlib
import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app import storage
from app.auth import sign_request
from app.main import app

PRINCIPALS = {
    "admin": {"secret": "admin-secret-32-bytes-minimum-0001", "capabilities": ["*"]},
    "gpt": {"secret": "gpt-secret-32-bytes-minimum-0000002", "capabilities": ["session:read", "handoff:send", "compare:execute"]},
    "local": {"secret": "local-secret-32-bytes-minimum-00003", "capabilities": ["session:read", "inbox:read", "handoff:ack", "compare:execute"]},
}


@pytest.fixture(autouse=True)
def clean_db(tmp_path, monkeypatch):
    db = tmp_path / "matverse-test.db"
    monkeypatch.setattr(storage, "DB_PATH", db)
    monkeypatch.setenv("MATVERSE_PRINCIPALS_JSON", json.dumps(PRINCIPALS))
    yield


@pytest.fixture
def client():
    return TestClient(app)


def auth_request(client: TestClient, principal: str, method: str, path: str, payload=None, *, nonce: str | None = None, signature_override: str | None = None):
    body = b"" if payload is None else json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    content_hash = hashlib.sha256(body).hexdigest()
    timestamp = str(int(time.time()))
    nonce_value = nonce or uuid.uuid4().hex
    signature = sign_request(PRINCIPALS[principal]["secret"], method, path, timestamp, nonce_value, content_hash)
    headers = {
        "X-MatVerse-Principal": principal,
        "X-MatVerse-Timestamp": timestamp,
        "X-MatVerse-Nonce": nonce_value,
        "X-MatVerse-Content-SHA256": content_hash,
        "X-MatVerse-Signature": signature_override or signature,
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
    return client.request(method, path, content=body, headers=headers)
