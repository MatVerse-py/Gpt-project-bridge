from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json

from fastapi.testclient import TestClient

from app.main import app
from app import storage


def _h(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _session_payload() -> dict:
    return {
        "participants": [
            {
                "participant_id": "gpt",
                "provider": "openai",
                "model": "gpt-model",
                "revision": "r1",
                "endpoint_class": "REMOTE",
            },
            {
                "participant_id": "local",
                "provider": "local",
                "model": "qwen",
                "revision": "q4",
                "endpoint_class": "LOCAL",
            },
        ],
        "contract": {
            "ontology_hash": _h("ontology"),
            "policy_hash": _h("policy"),
            "task_hash": _h("task"),
            "rubric_hash": _h("rubric"),
            "memory_policy_hash": _h("memory"),
        },
        "ontology_ok": True,
        "signature_valid": True,
        "transition_valid": True,
    }


def _create_session(client: TestClient) -> dict:
    response = client.post("/model-bridge/sessions", json=_session_payload())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["decision"] == "PASS"
    assert body["protocol_version"] == "matverse.model-bridge.v1"
    return body


def setup_function() -> None:
    if storage.DB_PATH.exists():
        storage.DB_PATH.unlink()


def test_protocol_excludes_hidden_reasoning() -> None:
    client = TestClient(app)
    protocol = client.get("/model-bridge/protocol").json()
    assert protocol["state_boundary"] == "observable-explicit-only"
    assert "chain_of_thought" in protocol["forbidden"]


def test_cross_model_session_handoff_inbox_and_ack() -> None:
    client = TestClient(app)
    session = _create_session(client)

    handoff_response = client.post(
        f"/model-bridge/sessions/{session['session_id']}/handoffs",
        json={
            "from_participant": "gpt",
            "to_participant": "local",
            "expected_contract_hash": session["contract_hash"],
            "payload": {
                "kind": "MODEL_OUTPUT",
                "public_summary": "observable result",
                "decision": "PASS",
                "claims": ["c1", "c2"],
            },
        },
    )
    assert handoff_response.status_code == 200, handoff_response.text
    handoff = handoff_response.json()
    assert handoff["decision"] == "PASS"
    assert handoff["stored"] is True
    assert handoff["status"] == "PENDING"

    inbox = client.get(
        f"/model-bridge/sessions/{session['session_id']}/inbox/local"
    ).json()
    assert len(inbox["handoffs"]) == 1
    assert inbox["handoffs"][0]["payload"]["public_summary"] == "observable result"

    ack = client.post(
        f"/model-bridge/handoffs/{handoff['handoff_id']}/ack",
        json={"participant_id": "local"},
    )
    assert ack.status_code == 200, ack.text
    assert ack.json()["status"] == "ACKED"

    inbox_after = client.get(
        f"/model-bridge/sessions/{session['session_id']}/inbox/local"
    ).json()
    assert inbox_after["handoffs"] == []
    assert client.get("/ready").json()["ready"] is True


def test_hidden_reasoning_is_rejected_before_storage() -> None:
    client = TestClient(app)
    session = _create_session(client)
    response = client.post(
        f"/model-bridge/sessions/{session['session_id']}/handoffs",
        json={
            "from_participant": "gpt",
            "to_participant": "local",
            "expected_contract_hash": session["contract_hash"],
            "payload": {
                "public_summary": "safe",
                "chain_of_thought": "must never cross the bridge",
            },
        },
    )
    assert response.status_code == 422
    inbox = client.get(
        f"/model-bridge/sessions/{session['session_id']}/inbox/local"
    ).json()
    assert inbox["handoffs"] == []


def test_contract_drift_is_blocked() -> None:
    client = TestClient(app)
    session = _create_session(client)
    response = client.post(
        f"/model-bridge/sessions/{session['session_id']}/handoffs",
        json={
            "from_participant": "gpt",
            "to_participant": "local",
            "expected_contract_hash": _h("wrong-contract"),
            "payload": {"public_summary": "attempted drift"},
        },
    )
    assert response.status_code == 409
    assert "contract hash mismatch" in response.text


def test_secret_human_data_is_blocked_without_payload_retention() -> None:
    client = TestClient(app)
    session = _create_session(client)
    secret_value = "raw-secret-human-content"
    response = client.post(
        f"/model-bridge/sessions/{session['session_id']}/handoffs",
        json={
            "from_participant": "gpt",
            "to_participant": "local",
            "expected_contract_hash": session["contract_hash"],
            "payload": {"public_summary": secret_value},
            "human": {
                "consent": True,
                "purpose": "cross-model-test",
                "sensitivity": "SECRET",
            },
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "BLOCK"
    assert body["stored"] is False

    ledger = client.get("/ledger").json()
    serialized = json.dumps(ledger, sort_keys=True)
    assert secret_value not in serialized
    inbox = client.get(
        f"/model-bridge/sessions/{session['session_id']}/inbox/local"
    ).json()
    assert inbox["handoffs"] == []


def test_portability_uses_predeclared_invariants_not_text_equality() -> None:
    client = TestClient(app)
    response = client.post(
        "/model-bridge/compare",
        json={
            "left": {
                "summary": "Model A wording",
                "decision": "PASS",
                "safety": {"gate": "PASS"},
                "claims": ["c1", "c2"],
            },
            "right": {
                "summary": "Completely different Model B wording",
                "decision": "PASS",
                "safety": {"gate": "PASS"},
                "claims": ["c2", "c1"],
            },
            "rules": [
                {"path": "decision", "mode": "exact"},
                {"path": "safety.gate", "mode": "exact"},
                {"path": "claims", "mode": "set_equal"},
            ],
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["portable"] is True
    assert result["passed"] == 3
    assert result["total"] == 3


def test_portability_fails_when_declared_safety_invariant_changes() -> None:
    client = TestClient(app)
    result = client.post(
        "/model-bridge/compare",
        json={
            "left": {"decision": "PASS", "safety": {"gate": "PASS"}},
            "right": {"decision": "PASS", "safety": {"gate": "BLOCK"}},
            "rules": [
                {"path": "decision", "mode": "exact"},
                {"path": "safety.gate", "mode": "exact"},
            ],
        },
    ).json()
    assert result["portable"] is False
    assert result["passed"] == 1
    assert result["total"] == 2


def test_blocked_ingress_does_not_retain_raw_payload() -> None:
    client = TestClient(app)
    secret_value = "must-not-enter-ledger"
    response = client.post(
        "/bridge/ingress",
        json={
            "payload": {"value": secret_value},
            "action": "EXPORT",
            "human": {
                "consent": True,
                "purpose": "test",
                "sensitivity": "SECRET",
            },
        },
    ).json()
    assert response["decision"] == "BLOCK"
    assert response["payload_retained"] is False
    ledger = client.get("/ledger").json()
    assert secret_value not in json.dumps(ledger, sort_keys=True)


def test_concurrent_ledger_appends_keep_single_chain() -> None:
    def write_event(index: int) -> str:
        return storage.append_event({"index": index}, "PASS")["event_hash"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        hashes = list(pool.map(write_event, range(32)))

    assert len(hashes) == len(set(hashes)) == 32
    integrity = storage.verify_chain()
    assert integrity["ok"] is True
    assert integrity["events"] == 32
