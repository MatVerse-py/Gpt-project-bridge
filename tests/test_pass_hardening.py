from __future__ import annotations

from app.core import stable_hash
from conftest import auth_request


def _register_contract(client):
    values = {}
    for kind, field in [
        ("ontology", "ontology_hash"),
        ("policy", "policy_hash"),
        ("task", "task_hash"),
        ("rubric", "rubric_hash"),
        ("memory_policy", "memory_policy_hash"),
    ]:
        content = {"kind": kind, "version": 1}
        response = auth_request(client, "admin", "POST", "/registry/contracts", {"kind": kind, "version": "1.0.0", "content": content})
        assert response.status_code == 200, response.text
        assert response.json()["artifact_hash"] == stable_hash(content)
        values[field] = response.json()["artifact_hash"]
    return values


def _session_payload(client):
    return {
        "participants": [
            {"participant_id": "gpt", "provider": "openai", "model": "gpt-model", "revision": "r1", "endpoint_class": "REMOTE"},
            {"participant_id": "local", "provider": "local", "model": "qwen", "revision": "q4", "endpoint_class": "LOCAL"},
        ],
        "contract": _register_contract(client),
    }


def _create_session(client):
    response = auth_request(client, "admin", "POST", "/model-bridge/sessions", _session_payload(client))
    assert response.status_code == 200, response.text
    return response.json()


def test_missing_auth_is_rejected(client):
    assert client.get("/ledger").status_code == 401


def test_bad_signature_is_rejected(client):
    response = auth_request(client, "admin", "GET", "/ledger", signature_override="0" * 64)
    assert response.status_code == 401


def test_nonce_replay_is_rejected(client):
    nonce = "n" * 32
    first = auth_request(client, "admin", "GET", "/ledger", nonce=nonce)
    second = auth_request(client, "admin", "GET", "/ledger", nonce=nonce)
    assert first.status_code == 200
    assert second.status_code == 409


def test_contract_hashes_must_resolve_to_registry(client):
    payload = {
        "participants": [
            {"participant_id": "gpt", "provider": "openai", "model": "a"},
            {"participant_id": "local", "provider": "local", "model": "b"},
        ],
        "contract": {key: "a" * 64 for key in ["ontology_hash", "policy_hash", "task_hash", "rubric_hash", "memory_policy_hash"]},
    }
    response = auth_request(client, "admin", "POST", "/model-bridge/sessions", payload)
    assert response.status_code == 409
    assert "unregistered" in response.text


def test_cross_model_handoff_requires_authenticated_sender(client):
    session = _create_session(client)
    payload = {
        "from_participant": "gpt",
        "to_participant": "local",
        "expected_contract_hash": session["contract_hash"],
        "payload": {"kind": "MODEL_OUTPUT", "public_summary": "safe", "decision": "PASS", "claims": ["c1"]},
    }
    response = auth_request(client, "local", "POST", f"/model-bridge/sessions/{session['session_id']}/handoffs", payload)
    assert response.status_code == 403


def test_hidden_reasoning_aliases_and_unknown_root_fields_fail_closed(client):
    session = _create_session(client)
    base = {"from_participant": "gpt", "to_participant": "local", "expected_contract_hash": session["contract_hash"]}
    for key in ["chain_of_thought", "chainOfThought", "Chain-Of-Thought", "reasoningTrace", "hiddenState", "private-memory", "apiKey", "access-token", "password"]:
        response = auth_request(client, "gpt", "POST", f"/model-bridge/sessions/{session['session_id']}/handoffs", {**base, "payload": {"public_summary": "safe", key: "secret"}})
        assert response.status_code == 422, (key, response.text)
    unknown = auth_request(client, "gpt", "POST", f"/model-bridge/sessions/{session['session_id']}/handoffs", {**base, "payload": {"public_summary": "safe", "arbitrary_private_blob": "x"}})
    assert unknown.status_code == 422


def test_handoff_inbox_ack_and_authorization(client):
    session = _create_session(client)
    handoff_payload = {
        "from_participant": "gpt",
        "to_participant": "local",
        "expected_contract_hash": session["contract_hash"],
        "payload": {"kind": "MODEL_OUTPUT", "public_summary": "observable result", "decision": "PASS", "claims": ["c1", "c2"]},
    }
    handoff = auth_request(client, "gpt", "POST", f"/model-bridge/sessions/{session['session_id']}/handoffs", handoff_payload)
    assert handoff.status_code == 200, handoff.text
    handoff_id = handoff.json()["handoff_id"]
    wrong_inbox = auth_request(client, "gpt", "GET", f"/model-bridge/sessions/{session['session_id']}/inbox/local")
    assert wrong_inbox.status_code == 403
    inbox = auth_request(client, "local", "GET", f"/model-bridge/sessions/{session['session_id']}/inbox/local")
    assert inbox.status_code == 200 and len(inbox.json()["handoffs"]) == 1
    wrong_ack = auth_request(client, "gpt", "POST", f"/model-bridge/handoffs/{handoff_id}/ack", {"participant_id": "local"})
    assert wrong_ack.status_code == 403
    ack = auth_request(client, "local", "POST", f"/model-bridge/handoffs/{handoff_id}/ack", {"participant_id": "local"})
    assert ack.status_code == 200 and ack.json()["status"] == "ACKED"


def test_secret_human_data_is_blocked_without_raw_payload_retention(client):
    session = _create_session(client)
    secret = "raw-secret-human-content"
    response = auth_request(client, "gpt", "POST", f"/model-bridge/sessions/{session['session_id']}/handoffs", {
        "from_participant": "gpt", "to_participant": "local", "expected_contract_hash": session["contract_hash"],
        "payload": {"public_summary": secret},
        "human": {"consent": True, "purpose": "test", "sensitivity": "SECRET"},
    })
    assert response.status_code == 200 and response.json()["decision"] == "BLOCK"
    ledger = auth_request(client, "admin", "GET", "/ledger").text
    assert secret not in ledger


def test_portability_is_invariant_based_not_text_equality(client):
    response = auth_request(client, "gpt", "POST", "/model-bridge/compare", {
        "left": {"public_summary": "A", "decision": "PASS", "safety": {"gate": "PASS"}, "claims": ["c1", "c2"]},
        "right": {"public_summary": "B", "decision": "PASS", "safety": {"gate": "PASS"}, "claims": ["c2", "c1"]},
        "rules": [
            {"path": "decision", "mode": "exact"},
            {"path": "safety.gate", "mode": "exact"},
            {"path": "claims", "mode": "set_equal"},
        ],
    })
    assert response.status_code == 200 and response.json()["portable"] is True


def test_federation_hard_gate_route_receipt_and_ledger(client):
    request = {
        "nodes": [
            {"node_id": "cpu", "layer": "infrastructure", "raw": {"psi": 0.94, "evidence": 0.92, "latency_ms": 40000, "cost": 0.4, "cvar": 0.02}, "attrs": {"residency": "local"}},
            {"node_id": "gpu", "layer": "infrastructure", "raw": {"psi": 0.94, "evidence": 0.90, "latency_ms": 3000, "cost": 2.1, "cvar": 0.02}, "attrs": {"residency": "local"}},
            {"node_id": "qpu", "layer": "infrastructure", "raw": {"psi": 0.80, "evidence": 0.48, "latency_ms": 480000, "cost": 220, "cvar": 0.19}, "attrs": {"residency": "cloud"}},
        ],
        "crossings": [{"src": "cpu", "dst": "gpu", "cost": 0.02}, {"src": "gpu", "dst": "cpu", "cost": 0.02}, {"src": "cpu", "dst": "qpu", "cost": 0.4}],
        "criteria": [
            {"name": "psi", "direction": "higher_is_better", "lo": 0, "hi": 1},
            {"name": "evidence", "direction": "higher_is_better", "lo": 0, "hi": 1},
            {"name": "latency_ms", "direction": "lower_is_better", "lo": 1, "hi": 600000, "scale": "log"},
            {"name": "cost", "direction": "lower_is_better", "lo": 0.01, "hi": 1000, "scale": "log"},
            {"name": "cvar", "direction": "lower_is_better", "lo": 0, "hi": 1},
        ],
        "weights": {"psi": 0.35, "evidence": 0.25, "latency_ms": 0.2, "cost": 0.1, "cvar": 0.1},
        "constraints": [
            {"kind": "metric_floor", "name": "psi", "value": 0.85},
            {"kind": "metric_ceiling", "name": "cvar", "value": 0.05},
            {"kind": "attr_equal", "name": "residency", "value": "local"},
        ],
        "origin": "cpu",
    }
    response = auth_request(client, "admin", "POST", "/federation/route", request)
    assert response.status_code == 200, response.text
    body = response.json()
    assert "qpu" in body["blocked"]
    assert len(body["route_receipt_sha256"]) == 64
    assert len(body["evidence_receipt"]["receipt_hash"]) == 64
    ledger = auth_request(client, "admin", "GET", "/ledger").json()
    assert ledger["integrity"]["ok"] is True
    assert any("FEDERATION_ROUTE" in row["event_json"] for row in ledger["events"])
