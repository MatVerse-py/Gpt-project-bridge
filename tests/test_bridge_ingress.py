from __future__ import annotations

import hashlib
import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app import main, storage
from app.auth import sign_request
from app.institutional_state_client import remote_state_enabled
from app.main import app


ADMIN_SECRET = "admin-secret-32-bytes-minimum-0001"
GPT_SECRET = "gpt-secret-32-bytes-minimum-0000002"


@pytest.fixture(autouse=True)
def isolate_ingress_from_remote_state(monkeypatch):
    # Keep this isolation local to ingress tests; production code and global
    # fixture semantics are unchanged. Test requests must not reach remote state.
    monkeypatch.delenv("MATVERSE_INSTITUTIONAL_STATE_URL", raising=False)
    monkeypatch.setenv("MATVERSE_STATE_BACKEND", "sqlite")
    assert remote_state_enabled() is False


def _post_ingress(
    client: TestClient,
    payload: dict,
    *,
    nonce: str | None = None,
    principal: str = "admin",
    secret: str = ADMIN_SECRET,
    signature_override: str | None = None,
):
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    timestamp = str(int(time.time()))
    actual_nonce = nonce or uuid.uuid4().hex
    content_hash = hashlib.sha256(body).hexdigest()
    headers = {
        "X-MatVerse-Principal": principal,
        "X-MatVerse-Timestamp": timestamp,
        "X-MatVerse-Nonce": actual_nonce,
        "X-MatVerse-Content-SHA256": content_hash,
        "X-MatVerse-Signature": signature_override or sign_request(
            secret, "POST", "/bridge/ingress", timestamp, actual_nonce, content_hash
        ),
        "Content-Type": "application/json",
    }
    return client.post("/bridge/ingress", content=body, headers=headers)


def test_pass_records_ingress_but_not_operational_execution(client):
    response = _post_ingress(client, {"payload": {"operation": "EXECUTE"}, "action": "EXECUTE"})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["decision"] == "PASS"
    assert data["executed"] is False
    assert data["quarantined"] is False
    assert data["receipt"]["decision"] == "PASS"
    assert len(storage.read_ledger()) == 1
    assert storage.verify_chain()["ok"] is True
    assert storage.replay()["accepted"] == 1


def test_hold_records_ingress_without_execution(client):
    response = _post_ingress(client, {"payload": {"operation": "EXECUTE"}, "human": {"consent": False}})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["decision"] == "HOLD"
    assert data["executed"] is False
    assert data["quarantined"] is True
    assert data["receipt"]["decision"] == "HOLD"
    assert storage.replay()["held"] == 1


def test_block_records_ingress_without_execution(client):
    response = _post_ingress(client, {"payload": {"operation": "EXECUTE"}, "human": {"serialize_human": True}})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["decision"] == "BLOCK"
    assert data["executed"] is False
    assert data["quarantined"] is True
    assert data["receipt"]["decision"] == "BLOCK"
    assert storage.replay()["blocked"] == 1


def test_simulated_persistence_failure_is_not_reported_as_success(monkeypatch):
    def fail_append_event(*args, **kwargs):
        raise RuntimeError("persistence unavailable")

    monkeypatch.setattr(main, "append_event", fail_append_event)
    client = TestClient(app, raise_server_exceptions=False)
    response = _post_ingress(client, {"payload": {"operation": "EXECUTE"}, "action": "EXECUTE"})
    assert response.status_code == 500
    assert "executed" not in response.text
    assert storage.read_ledger() == []


def test_actual_sqlite_ledger_write_failure_rolls_back():
    # The nonce is persisted first, then a real SQLite trigger rejects the
    # later ledger insert. This checks the real ledger rollback path.
    conn = storage._connect()
    try:
        conn.execute("""
            CREATE TRIGGER ingress_ledger_failure BEFORE INSERT ON ledger
            BEGIN SELECT RAISE(ABORT, 'ledger insert denied for test'); END;
        """)
        conn.commit()
    finally:
        conn.close()

    client = TestClient(app, raise_server_exceptions=False)
    response = _post_ingress(client, {"payload": {"operation": "EXECUTE"}, "action": "EXECUTE"})
    assert response.status_code == 500
    assert "executed" not in response.text
    assert storage.read_ledger() == []
    assert storage.verify_chain()["ok"] is True


def test_invalid_hmac_rejected_before_ingress(client):
    response = _post_ingress(
        client, {"payload": {"operation": "READ"}, "action": "READ"},
        signature_override="0" * 64,
    )
    assert response.status_code == 401
    assert storage.read_ledger() == []


def test_reused_nonce_rejected_without_second_ledger_event(client):
    nonce = "ingress-replay-nonce-fixed-12345"
    payload = {"payload": {"operation": "READ"}, "action": "READ"}
    first = _post_ingress(client, payload, nonce=nonce)
    second = _post_ingress(client, payload, nonce=nonce)
    assert first.status_code == 200, first.text
    assert second.status_code == 409, second.text
    assert len(storage.read_ledger()) == 1


def test_principal_without_ingress_capability_is_rejected(client):
    response = _post_ingress(
        client, {"payload": {"operation": "READ"}, "action": "READ"},
        principal="gpt", secret=GPT_SECRET,
    )
    assert response.status_code == 403, response.text
    assert storage.read_ledger() == []
