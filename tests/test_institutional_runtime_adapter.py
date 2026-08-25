from __future__ import annotations

import hashlib
import json
import time
import uuid
from copy import deepcopy

from fastapi.testclient import TestClient

from app import storage
from app.auth import sign_request
from app.institutional_projection import ProjectionUnavailable, build_institutional_projection, jcs_subset_bytes, jcs_subset_hash
from app.institutional_service import app


PRINCIPALS = {
    "surface": {
        "secret": "surface-secret-00000000000000000001",
        "capabilities": ["institutional:projection:read", "institutional:intent:submit", "institutional:intent:read"],
    },
    "other": {
        "secret": "other-secret-0000000000000000000002",
        "capabilities": ["institutional:projection:read", "institutional:intent:submit", "institutional:intent:read"],
    },
    "viewer": {
        "secret": "viewer-secret-000000000000000000001",
        "capabilities": ["institutional:projection:read"],
    },
}
BUILD_COMMIT = "e975b2794f3f17402d0dd97b1a7cffd55bfcbaa2"
FROZEN_CONTRACT = "67743cbe1f4d65983348401d2061e46dec22d57e232854ff263e1d646f600b26"


def _request(client: TestClient, principal: str, method: str, path: str, payload=None):
    body = b"" if payload is None else json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    content_hash = hashlib.sha256(body).hexdigest()
    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex
    signature = sign_request(PRINCIPALS[principal]["secret"], method, path, timestamp, nonce, content_hash)
    headers = {
        "X-MatVerse-Principal": principal,
        "X-MatVerse-Timestamp": timestamp,
        "X-MatVerse-Nonce": nonce,
        "X-MatVerse-Content-SHA256": content_hash,
        "X-MatVerse-Signature": signature,
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
    return client.request(method, path, content=body, headers=headers)


def _source_from_projection(projection: dict) -> dict:
    return {
        **projection["source"],
        "projection_hash": projection["projection"]["projection_hash"],
    }


def _intent(projection: dict, *, intent_id: str = "intent-1", actor_id: str = "surface", parameters=None) -> dict:
    payload = {
        "schema_version": "matverse.institutional-intent.v1",
        "intent_id": intent_id,
        "requested_operation": "REGISTER_ARTIFACT",
        "actor_id": actor_id,
        "target": {"kind": "ARTIFACT", "id": "artifact-1"},
        "parameters": {"content_hash": "a" * 64} if parameters is None else parameters,
        "created_at": "2026-08-25T21:30:00Z",
        "source": _source_from_projection(projection),
        "hash_algorithm": "SHA-256",
        "canonicalization": "RFC8785_JCS",
        "hash_excludes": ["intent_hash"],
    }
    payload["intent_hash"] = jcs_subset_hash(payload)
    return payload


def _configure(monkeypatch) -> None:
    monkeypatch.setenv("MATVERSE_PRINCIPALS_JSON", json.dumps(PRINCIPALS))
    monkeypatch.setenv("MATVERSE_BUILD_COMMIT", BUILD_COMMIT)
    monkeypatch.setenv("MATVERSE_BUILD_REF", "main")
    monkeypatch.setenv("MATVERSE_FROZEN_CONTRACT_HASH", FROZEN_CONTRACT)
    monkeypatch.setenv("MATVERSE_BUILD_TIMESTAMP", "2026-08-25T21:30:00Z")


def test_projection_fails_closed_without_build_binding(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.delenv("MATVERSE_BUILD_COMMIT")
    try:
        build_institutional_projection()
    except ProjectionUnavailable as exc:
        assert "MATVERSE_BUILD_COMMIT" in str(exc)
    else:
        raise AssertionError("projection must fail closed without build commit")


def test_projection_is_deterministic_for_unchanged_state(monkeypatch):
    _configure(monkeypatch)
    first = build_institutional_projection()
    second = build_institutional_projection()
    assert first == second
    assert first["projection"]["projection_hash"] == second["projection"]["projection_hash"]
    assert first["source"]["commit_sha"] == BUILD_COMMIT
    assert first["source"]["frozen_contract_hash"] == FROZEN_CONTRACT


def test_projection_hash_changes_after_canonical_ledger_change(monkeypatch):
    _configure(monkeypatch)
    before = build_institutional_projection()
    storage.append_event({"event_type": "TEST_EVENT", "created_at": "2026-08-25T21:31:00Z"}, "PASS")
    after = build_institutional_projection()
    assert before["projection"]["projection_hash"] != after["projection"]["projection_hash"]
    assert len(after["receipts"]) == 1


def test_projection_refuses_tampered_ledger(monkeypatch):
    _configure(monkeypatch)
    storage.append_event({"event_type": "TEST_EVENT"}, "PASS")
    conn = storage._connect()
    try:
        conn.execute("UPDATE ledger SET event_hash=? WHERE seq=1", ("0" * 64,))
        conn.commit()
    finally:
        conn.close()
    try:
        build_institutional_projection()
    except ProjectionUnavailable as exc:
        assert "ledger integrity failure" in str(exc)
    else:
        raise AssertionError("tampered ledger must block projection")


def test_jcs_subset_uses_utf16_property_order_and_rejects_ambiguous_numbers(monkeypatch):
    _configure(monkeypatch)
    # RFC 8785 sorts object property names by UTF-16 code units. U+1F600 starts
    # with surrogate D83D and therefore sorts before U+E000, unlike Python's
    # default Unicode code-point ordering.
    assert jcs_subset_bytes({"\ue000": 1, "😀": 2}) == '{"😀":2,"\ue000":1}'.encode("utf-8")
    for value in ({"x": 1.5}, {"x": 9_007_199_254_740_992}):
        try:
            jcs_subset_hash(value)
        except ValueError:
            pass
        else:
            raise AssertionError("ambiguous/non-interoperable number must be rejected")


def test_projection_endpoint_requires_auth_and_capability(monkeypatch):
    _configure(monkeypatch)
    client = TestClient(app)
    assert client.get("/institutional/projection").status_code == 401
    response = _request(client, "surface", "GET", "/institutional/projection")
    assert response.status_code == 200, response.text
    assert response.json()["projection_policy"]["write_authority"] == "NONE"


def test_intent_acceptance_is_authenticated_source_bound_and_not_execution(monkeypatch):
    _configure(monkeypatch)
    client = TestClient(app)
    projection = _request(client, "surface", "GET", "/institutional/projection").json()
    intent = _intent(projection)
    response = _request(client, "surface", "POST", "/institutional/intents", intent)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["acceptance_decision"] == "PASS"
    assert data["execution_decision"] == "HOLD"
    assert data["status"] == "PENDING_EVALUATION"
    rows = storage.read_ledger()
    assert len(rows) == 1
    event = json.loads(rows[0]["event_json"])
    assert event["event_type"] == "INSTITUTIONAL_INTENT_ACCEPTED"
    assert event["execution_decision"] == "HOLD"
    assert event["parameters_hash"] == jcs_subset_hash(intent["parameters"])
    assert "parameters" not in event


def test_exact_intent_retry_is_idempotent_after_projection_advances(monkeypatch):
    _configure(monkeypatch)
    client = TestClient(app)
    projection = _request(client, "surface", "GET", "/institutional/projection").json()
    intent = _intent(projection)
    first = _request(client, "surface", "POST", "/institutional/intents", intent)
    second = _request(client, "surface", "POST", "/institutional/intents", intent)
    assert first.status_code == 200
    assert second.status_code == 200, second.text
    assert second.json()["idempotent"] is True
    assert len(storage.read_ledger()) == 1


def test_mutated_reuse_of_intent_id_is_blocked(monkeypatch):
    _configure(monkeypatch)
    client = TestClient(app)
    projection = _request(client, "surface", "GET", "/institutional/projection").json()
    original = _intent(projection)
    assert _request(client, "surface", "POST", "/institutional/intents", original).status_code == 200
    mutated = deepcopy(original)
    mutated["parameters"] = {"content_hash": "b" * 64}
    mutated.pop("intent_hash")
    mutated["intent_hash"] = jcs_subset_hash(mutated)
    response = _request(client, "surface", "POST", "/institutional/intents", mutated)
    assert response.status_code == 409
    assert len(storage.read_ledger()) == 1


def test_new_intent_from_stale_projection_is_held(monkeypatch):
    _configure(monkeypatch)
    client = TestClient(app)
    projection = _request(client, "surface", "GET", "/institutional/projection").json()
    stale_intent = _intent(projection, intent_id="intent-stale")
    storage.append_event({"event_type": "CANONICAL_STATE_ADVANCED", "created_at": "2026-08-25T21:31:00Z"}, "PASS")
    response = _request(client, "surface", "POST", "/institutional/intents", stale_intent)
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["decision"] == "HOLD"
    assert "stale" in detail["reason"]


def test_authenticated_actor_cannot_impersonate_another_actor(monkeypatch):
    _configure(monkeypatch)
    client = TestClient(app)
    projection = _request(client, "surface", "GET", "/institutional/projection").json()
    intent = _intent(projection, actor_id="surface")
    response = _request(client, "other", "POST", "/institutional/intents", intent)
    assert response.status_code == 403


def test_intent_rejects_hidden_private_or_secret_state(monkeypatch):
    _configure(monkeypatch)
    client = TestClient(app)
    projection = _request(client, "surface", "GET", "/institutional/projection").json()
    for parameters in ({"credentials": {"value": "x"}}, {"nested": {"chain_of_thought": "private"}}):
        intent = _intent(projection, intent_id=uuid.uuid4().hex, parameters=parameters)
        response = _request(client, "surface", "POST", "/institutional/intents", intent)
        assert response.status_code == 422
        assert "forbidden hidden/private field" in response.text


def test_intent_rejects_floats_and_unsafe_integers(monkeypatch):
    _configure(monkeypatch)
    client = TestClient(app)
    projection = _request(client, "surface", "GET", "/institutional/projection").json()
    for parameters in ({"value": 1.25}, {"value": 9_007_199_254_740_992}):
        raw = {
            "schema_version": "matverse.institutional-intent.v1",
            "intent_id": uuid.uuid4().hex,
            "requested_operation": "OTHER",
            "actor_id": "surface",
            "target": {"kind": "OTHER", "id": "x"},
            "parameters": parameters,
            "created_at": "2026-08-25T21:30:00Z",
            "source": _source_from_projection(projection),
            "intent_hash": "0" * 64,
            "hash_algorithm": "SHA-256",
            "canonicalization": "RFC8785_JCS",
            "hash_excludes": ["intent_hash"],
        }
        response = _request(client, "surface", "POST", "/institutional/intents", raw)
        assert response.status_code == 422


def test_viewer_cannot_submit_or_read_intents(monkeypatch):
    _configure(monkeypatch)
    client = TestClient(app)
    projection = _request(client, "viewer", "GET", "/institutional/projection")
    assert projection.status_code == 200
    intent = _intent(projection.json(), actor_id="viewer")
    assert _request(client, "viewer", "POST", "/institutional/intents", intent).status_code == 403
    assert _request(client, "viewer", "GET", "/institutional/intents").status_code == 403


def test_intent_read_is_actor_scoped(monkeypatch):
    _configure(monkeypatch)
    client = TestClient(app)
    projection = _request(client, "surface", "GET", "/institutional/projection").json()
    intent = _intent(projection)
    assert _request(client, "surface", "POST", "/institutional/intents", intent).status_code == 200
    own = _request(client, "surface", "GET", "/institutional/intents/intent-1")
    other = _request(client, "other", "GET", "/institutional/intents/intent-1")
    assert own.status_code == 200
    assert other.status_code == 403
