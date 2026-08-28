from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import time
import uuid

from fastapi.testclient import TestClient

from app import institutional_store, storage
from app.auth import sign_request
from app.institutional_projection import ProjectionUnavailable, build_institutional_projection, jcs_subset_bytes, jcs_subset_hash
from app.institutional_service import app
from app.institutional_store import list_intents_for_principal, persist_intent


BUILD_A = "a" * 40
BUILD_B = "b" * 40
FROZEN_CONTRACT = "67743cbe1f4d65983348401d2061e46dec22d57e232854ff263e1d646f600b26"
PRINCIPALS = {
    "surface": {
        "secret": "surface-review-secret-000000000000001",
        "capabilities": ["institutional:projection:read", "institutional:intent:submit", "institutional:intent:read"],
    },
    "delegator": {
        "secret": "delegator-review-secret-0000000000001",
        "capabilities": [
            "institutional:projection:read",
            "institutional:intent:submit",
            "institutional:intent:submit:any",
            "institutional:intent:read",
        ],
    },
    "actor": {
        "secret": "actor-review-secret-00000000000000001",
        "capabilities": ["institutional:projection:read", "institutional:intent:read"],
    },
}


def _configure(monkeypatch, *, commit: str = BUILD_A, ref: str = "review-hardening") -> None:
    monkeypatch.setenv("MATVERSE_PRINCIPALS_JSON", json.dumps(PRINCIPALS))
    monkeypatch.setenv("MATVERSE_BUILD_COMMIT", commit)
    monkeypatch.setenv("MATVERSE_BUILD_REF", ref)
    monkeypatch.setenv("MATVERSE_FROZEN_CONTRACT_HASH", FROZEN_CONTRACT)
    monkeypatch.setenv("MATVERSE_BUILD_TIMESTAMP", "2026-08-25T21:30:00Z")


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


def _source(projection: dict) -> dict:
    return {**projection["source"], "projection_hash": projection["projection"]["projection_hash"]}


def _intent(projection: dict, *, intent_id: str, actor_id: str = "surface", created_at: str = "2026-08-25T21:30:00Z") -> dict:
    payload = {
        "schema_version": "matverse.institutional-intent.v1",
        "intent_id": intent_id,
        "requested_operation": "REQUEST_AUTHORIZATION",
        "actor_id": actor_id,
        "target": {"kind": "SYSTEM", "id": "review-system"},
        "parameters": {"value": 1},
        "created_at": created_at,
        "source": _source(projection),
        "hash_algorithm": "SHA-256",
        "canonicalization": "RFC8785_JCS",
        "hash_excludes": ["intent_hash"],
    }
    payload["intent_hash"] = jcs_subset_hash(payload)
    return payload


def test_atomic_freshness_allows_only_one_new_intent_from_same_projection(monkeypatch):
    _configure(monkeypatch)
    projection = build_institutional_projection()
    intents = [_intent(projection, intent_id="race-a"), _intent(projection, intent_id="race-b")]

    def submit(item):
        try:
            persist_intent(intent=item, principal_id="surface")
            return "PASS"
        except ValueError as exc:
            assert "stale" in str(exc)
            return "HOLD"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(submit, intents))
    assert sorted(outcomes) == ["HOLD", "PASS"]
    assert len(storage.read_ledger()) == 1


def test_delegated_submit_records_principal_and_actor_and_actor_can_read(monkeypatch):
    _configure(monkeypatch)
    client = TestClient(app)
    projection = _request(client, "delegator", "GET", "/institutional/projection").json()
    intent = _intent(projection, intent_id="delegated-1", actor_id="actor")
    response = _request(client, "delegator", "POST", "/institutional/intents", intent)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["principal_id"] == "delegator"
    assert data["actor_id"] == "actor"
    actor_read = _request(client, "actor", "GET", "/institutional/intents/delegated-1")
    assert actor_read.status_code == 200, actor_read.text


def test_projection_rejects_invalid_build_ref(monkeypatch):
    for ref in ("", "x" * 257):
        _configure(monkeypatch, ref=ref)
        try:
            build_institutional_projection()
        except ProjectionUnavailable as exc:
            assert "MATVERSE_BUILD_REF" in str(exc)
        else:
            raise AssertionError("invalid build ref must fail closed")


def test_integral_json_float_matches_integer_wire_contract(monkeypatch):
    _configure(monkeypatch)
    assert jcs_subset_bytes({"value": 1.0}) == b'{"value":1}'
    assert jcs_subset_hash({"value": 1.0}) == jcs_subset_hash({"value": 1})


def test_service_rejects_non_rfc3339_timestamp_even_if_iso_parser_would_accept(monkeypatch):
    _configure(monkeypatch)
    client = TestClient(app)
    projection = _request(client, "surface", "GET", "/institutional/projection").json()
    intent = _intent(
        projection,
        intent_id="bad-time",
        created_at="2026-08-25 21:30:00+00:00",
    )
    response = _request(client, "surface", "POST", "/institutional/intents", intent)
    assert response.status_code == 422
    assert "RFC 3339" in response.text


def test_list_intents_uses_one_connection_and_supports_pagination(monkeypatch):
    _configure(monkeypatch)
    projection = build_institutional_projection()
    first = _intent(projection, intent_id="page-a")
    persist_intent(intent=first, principal_id="surface")
    projection = build_institutional_projection()
    second = _intent(projection, intent_id="page-b")
    persist_intent(intent=second, principal_id="surface")

    original_connect = institutional_store._connect
    calls = 0

    def counted_connect():
        nonlocal calls
        calls += 1
        return original_connect()

    monkeypatch.setattr(institutional_store, "_connect", counted_connect)
    page = list_intents_for_principal("surface", limit=1, offset=1)
    assert calls == 1
    assert len(page) == 1
    assert page[0]["intent_id"] == "page-b"


def test_receipt_preserves_originating_commit_across_runtime_upgrade(monkeypatch):
    _configure(monkeypatch, commit=BUILD_A)
    storage.append_event({"event_type": "ORIGIN_TEST"}, "PASS")
    _configure(monkeypatch, commit=BUILD_B)
    projection = build_institutional_projection()
    assert projection["source"]["commit_sha"] == BUILD_B
    assert projection["receipts"][0]["source_commit"] == BUILD_A


def test_untimestamped_event_advances_projection_time_via_hashed_ledger_at(monkeypatch):
    _configure(monkeypatch)
    before = build_institutional_projection()
    storage.append_event({"event_type": "UNTIMESTAMPED_TEST"}, "PASS")
    rows = storage.read_ledger()
    stored_event = json.loads(rows[-1]["event_json"])
    assert "ledger_at" in stored_event
    after = build_institutional_projection()
    assert after["projection"]["generated_at"] == stored_event["ledger_at"]
    assert after["projection"]["generated_at"] != before["projection"]["generated_at"]
