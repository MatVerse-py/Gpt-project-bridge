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
from conftest import auth_request


ADMIN_SECRET = "admin-secret-32-bytes-minimum-0001"
GPT_SECRET = "gpt-secret-32-bytes-minimum-0000002"

ONTOLOGY_CONTENT = {
    "ontology_id": "matverse.bridge.ingress",
    "version": "1",
    "allowed_actions": ["READ", "COMMIT", "EXECUTE", "EXPORT", "PUBLISH"],
}
TRANSITION_CONTENT = {
    "transition_id": "matverse.bridge.ingress.transitions",
    "version": "1",
    "transitions": [
        {"action": "EXECUTE", "from_state": "AUTHORIZED", "to_state": "EXECUTION_REQUESTED"},
        {"action": "READ", "from_state": "REQUESTED", "to_state": "OBSERVED"},
    ],
}


@pytest.fixture(autouse=True)
def isolate_ingress_from_remote_state(monkeypatch):
    # Keep this isolation local to ingress tests; production code and global
    # fixture semantics are unchanged. Test requests must not reach remote state.
    monkeypatch.delenv("MATVERSE_INSTITUTIONAL_STATE_URL", raising=False)
    monkeypatch.setenv("MATVERSE_STATE_BACKEND", "sqlite")
    assert remote_state_enabled() is False


def _seed_ingress_contracts(
    *,
    ontology_content: dict | None = None,
    transition_content: dict | None = None,
    ontology_kind: str = "ontology",
    transition_kind: str = "transition",
) -> tuple[str, str]:
    ontology = ontology_content or ONTOLOGY_CONTENT
    transition = transition_content or TRANSITION_CONTENT
    ontology_hash = storage.stable_hash(ontology)
    transition_hash = storage.stable_hash(transition)
    conn = storage._connect()
    try:
        now = storage._now()
        conn.execute(
            "INSERT OR IGNORE INTO contract_artifacts(artifact_hash,kind,version,content_json,created_by,created_at) VALUES (?,?,?,?,?,?)",
            (ontology_hash, ontology_kind, "1", storage._canonical_json(ontology), "test", now),
        )
        conn.execute(
            "INSERT OR IGNORE INTO contract_artifacts(artifact_hash,kind,version,content_json,created_by,created_at) VALUES (?,?,?,?,?,?)",
            (transition_hash, transition_kind, "1", storage._canonical_json(transition), "test", now),
        )
        conn.commit()
    finally:
        conn.close()
    return ontology_hash, transition_hash


def _valid_payload(
    *,
    action: str = "EXECUTE",
    source_state: str | None = "AUTHORIZED",
    target_state: str | None = "EXECUTION_REQUESTED",
) -> dict:
    ontology_hash, transition_hash = _seed_ingress_contracts()
    return {
        "payload": {"operation": action},
        "action": action,
        "ontology_hash": ontology_hash,
        "transition_hash": transition_hash,
        "source_state": source_state,
        "target_state": target_state,
    }


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
    response = _post_ingress(client, _valid_payload())
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["decision"] == "PASS"
    assert data["executed"] is False
    assert data["quarantined"] is False
    assert data["contract_validation"]["status"] == "PASS"
    assert data["contract_validation"]["transition_semantics"] == "DECLARED_CONTRACT_EDGE_ONLY_NOT_CURRENT_STATE_PROOF"
    assert data["receipt"]["decision"] == "PASS"
    assert len(storage.read_ledger()) == 1
    assert storage.verify_chain()["ok"] is True
    assert storage.replay()["accepted"] == 1


def test_missing_contracts_hold_and_never_pass(client):
    response = _post_ingress(client, {"payload": {"operation": "EXECUTE"}, "action": "EXECUTE"})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["decision"] == "HOLD"
    assert data["executed"] is False
    assert data["contract_validation"]["status"] == "HOLD"
    assert data["receipt"]["decision"] == "HOLD"


def test_unregistered_contract_hashes_hold(client):
    payload = {
        "payload": {"operation": "EXECUTE"}, "action": "EXECUTE",
        "ontology_hash": "a" * 64, "transition_hash": "b" * 64,
        "source_state": "AUTHORIZED", "target_state": "EXECUTION_REQUESTED",
    }
    response = _post_ingress(client, payload)
    assert response.status_code == 200
    assert response.json()["decision"] == "HOLD"


def test_ontology_kind_mismatch_blocks(client):
    ontology_hash, transition_hash = _seed_ingress_contracts(ontology_kind="policy")
    payload = {
        "payload": {"operation": "EXECUTE"}, "action": "EXECUTE",
        "ontology_hash": ontology_hash, "transition_hash": transition_hash,
        "source_state": "AUTHORIZED", "target_state": "EXECUTION_REQUESTED",
    }
    response = _post_ingress(client, payload)
    assert response.status_code == 200
    assert response.json()["decision"] == "BLOCK"


def test_malformed_ontology_blocks(client):
    malformed = {"ontology_id": "matverse.bridge.ingress", "version": "1", "allowed_actions": "EXECUTE"}
    ontology_hash, transition_hash = _seed_ingress_contracts(ontology_content=malformed)
    payload = {
        "payload": {"operation": "EXECUTE"}, "action": "EXECUTE",
        "ontology_hash": ontology_hash, "transition_hash": transition_hash,
        "source_state": "AUTHORIZED", "target_state": "EXECUTION_REQUESTED",
    }
    response = _post_ingress(client, payload)
    assert response.status_code == 200
    assert response.json()["decision"] == "BLOCK"


def test_action_not_allowed_by_ontology_blocks(client):
    ontology = {"ontology_id": "matverse.bridge.ingress", "version": "1", "allowed_actions": ["READ"]}
    ontology_hash, transition_hash = _seed_ingress_contracts(ontology_content=ontology)
    payload = {
        "payload": {"operation": "EXECUTE"}, "action": "EXECUTE",
        "ontology_hash": ontology_hash, "transition_hash": transition_hash,
        "source_state": "AUTHORIZED", "target_state": "EXECUTION_REQUESTED",
    }
    response = _post_ingress(client, payload)
    assert response.status_code == 200
    assert response.json()["decision"] == "BLOCK"


def test_missing_transition_states_hold(client):
    response = _post_ingress(client, _valid_payload(source_state=None, target_state="EXECUTION_REQUESTED"))
    assert response.status_code == 200
    assert response.json()["decision"] == "HOLD"


def test_transition_edge_mismatch_blocks(client):
    response = _post_ingress(client, _valid_payload(target_state="EXECUTED"))
    assert response.status_code == 200
    assert response.json()["decision"] == "BLOCK"


def test_transition_artifact_kind_is_available_through_registry(client):
    response = auth_request(
        client, "admin", "POST", "/registry/contracts",
        {"kind": "transition", "version": "1", "content": TRANSITION_CONTENT},
    )
    assert response.status_code == 200, response.text
    assert response.json()["kind"] == "transition"


def test_hold_records_ingress_without_execution(client):
    payload = _valid_payload()
    payload["human"] = {"consent": False}
    response = _post_ingress(client, payload)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["decision"] == "HOLD"
    assert data["executed"] is False
    assert data["quarantined"] is True
    assert data["receipt"]["decision"] == "HOLD"
    assert storage.replay()["held"] == 1


def test_block_records_ingress_without_execution(client):
    payload = _valid_payload()
    payload["human"] = {"serialize_human": True}
    response = _post_ingress(client, payload)
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
    response = _post_ingress(client, _valid_payload())
    assert response.status_code == 500
    assert "executed" not in response.text
    assert storage.read_ledger() == []


def test_actual_sqlite_ledger_write_failure_rolls_back():
    _valid_payload()
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
    response = _post_ingress(client, _valid_payload())
    assert response.status_code == 500
    assert "executed" not in response.text
    assert storage.read_ledger() == []
    assert storage.verify_chain()["ok"] is True


def test_invalid_hmac_rejected_before_ingress(client):
    response = _post_ingress(
        client, _valid_payload(),
        signature_override="0" * 64,
    )
    assert response.status_code == 401
    assert storage.read_ledger() == []


def test_reused_nonce_rejected_without_second_ledger_event(client):
    payload = _valid_payload()
    nonce = "ingress-replay-nonce-fixed-12345"
    first = _post_ingress(client, payload, nonce=nonce)
    second = _post_ingress(client, payload, nonce=nonce)
    assert first.status_code == 200, first.text
    assert second.status_code == 409, second.text
    assert len(storage.read_ledger()) == 1


def test_principal_without_ingress_capability_is_rejected(client):
    response = _post_ingress(
        client, _valid_payload(),
        principal="gpt", secret=GPT_SECRET,
    )
    assert response.status_code == 403, response.text
    assert storage.read_ledger() == []
