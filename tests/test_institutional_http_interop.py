from __future__ import annotations

import hashlib
import json
import time

from fastapi.testclient import TestClient

from app.auth import sign_request
from app.institutional_projection import jcs_subset_hash
from app.institutional_protocol import INTENT_OPERATIONS, PROTOCOL_VERSION, TARGET_KINDS
from app.institutional_service import app


PRINCIPAL = "surface"
SECRET = "surface-secret-00000000000000000001"
BUILD_COMMIT = "e975b2794f3f17402d0dd97b1a7cffd55bfcbaa2"
FROZEN_CONTRACT = "67743cbe1f4d65983348401d2061e46dec22d57e232854ff263e1d646f600b26"


def _configure(monkeypatch, *, runtime_id: str | None = "runtime-main") -> None:
    monkeypatch.setenv(
        "MATVERSE_PRINCIPALS_JSON",
        json.dumps(
            {
                PRINCIPAL: {
                    "secret": SECRET,
                    "capabilities": [
                        "institutional:projection:read",
                        "institutional:intent:submit",
                        "institutional:intent:read",
                    ],
                }
            }
        ),
    )
    monkeypatch.setenv("MATVERSE_BUILD_COMMIT", BUILD_COMMIT)
    monkeypatch.setenv("MATVERSE_BUILD_REF", "main")
    monkeypatch.setenv("MATVERSE_FROZEN_CONTRACT_HASH", FROZEN_CONTRACT)
    monkeypatch.setenv("MATVERSE_BUILD_TIMESTAMP", "2026-08-25T21:30:00Z")
    if runtime_id is None:
        monkeypatch.delenv("MATVERSE_RUNTIME_ID", raising=False)
    else:
        monkeypatch.setenv("MATVERSE_RUNTIME_ID", runtime_id)


def _headers(method: str, path: str, body: bytes, *, nonce: str) -> dict[str, str]:
    timestamp = str(int(time.time()))
    content_hash = hashlib.sha256(body).hexdigest()
    signature = sign_request(SECRET, method, path, timestamp, nonce, content_hash)
    return {
        "X-MatVerse-Principal": PRINCIPAL,
        "X-MatVerse-Timestamp": timestamp,
        "X-MatVerse-Nonce": nonce,
        "X-MatVerse-Content-SHA256": content_hash,
        "X-MatVerse-Signature": signature,
    }


def _get(client: TestClient, path: str, *, nonce: str):
    return client.get(path, headers=_headers("GET", path, b"", nonce=nonce))


def _post(client: TestClient, path: str, payload: dict, *, nonce: str):
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    headers = _headers("POST", path, body, nonce=nonce)
    headers["Content-Type"] = "application/json"
    return client.post(path, content=body, headers=headers)


def _intent(projection: dict) -> dict:
    source = {**projection["source"], "projection_hash": projection["projection"]["projection_hash"]}
    intent = {
        "schema_version": "matverse.institutional-intent.v1",
        "intent_id": "lta-intent-1",
        "requested_operation": "REGISTER_TWIN_FINDING",
        "actor_id": PRINCIPAL,
        "target": {"kind": "TWIN", "id": "TWIN-GATE-001"},
        "parameters": {
            "livingUnitId": "LTA-GATE-001",
            "twinId": "TWIN-GATE-001",
            "challengeClass": "COUNTEREXAMPLE",
            "statement": "counterexample registered as an intent only",
            "severity": "HIGH",
        },
        "created_at": "2026-08-25T21:30:00Z",
        "source": source,
        "hash_algorithm": "SHA-256",
        "canonicalization": "RFC8785_JCS",
        "hash_excludes": ["intent_hash"],
    }
    intent["intent_hash"] = jcs_subset_hash(intent)
    return intent


def test_runtime_identity_fails_closed_until_runtime_id_is_provisioned(monkeypatch):
    _configure(monkeypatch, runtime_id=None)
    response = _get(TestClient(app), "/institutional/runtime", nonce="runtime-missing-0000000001")
    assert response.status_code == 503
    assert response.json()["detail"]["decision"] == "HOLD"


def test_runtime_identity_proves_registered_principal_and_current_projection(monkeypatch):
    _configure(monkeypatch)
    client = TestClient(app)
    response = _get(client, "/institutional/runtime", nonce="runtime-ok-00000000000001")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["schema_version"] == "matverse.institutional-runtime.v1"
    assert data["protocol_version"] == PROTOCOL_VERSION
    assert data["runtime_id"] == "runtime-main"
    assert data["authentication"] == "HMAC-SHA256"
    assert data["authenticated_principal_id"] == PRINCIPAL
    assert data["status"] == "READY"
    assert data["intent_execution"] == "HOLD"
    projection = _get(client, "/institutional/projection", nonce="projection-ok-000000000001")
    assert projection.status_code == 200
    assert data["source"] == projection.json()["source"]
    assert data["projection_hash"] == projection.json()["projection"]["projection_hash"]


def test_runtime_request_nonce_replay_is_rejected(monkeypatch):
    _configure(monkeypatch)
    client = TestClient(app)
    path = "/institutional/runtime"
    nonce = "runtime-replay-00000000001"
    headers = _headers("GET", path, b"", nonce=nonce)
    first = client.get(path, headers=headers)
    second = client.get(path, headers=headers)
    assert first.status_code == 200
    assert second.status_code == 409
    assert "nonce replayed" in second.text


def test_surface_and_runtime_share_intent_vocabulary_without_execution_promotion(monkeypatch):
    _configure(monkeypatch)
    assert "REGISTER_TWIN_FINDING" in INTENT_OPERATIONS
    assert "TWIN" in TARGET_KINDS
    client = TestClient(app)
    projection = _get(client, "/institutional/projection", nonce="projection-intent-000000001").json()
    response = _post(client, "/institutional/intents", _intent(projection), nonce="post-lta-intent-0000000001")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["acceptance_decision"] == "PASS"
    assert data["execution_decision"] == "HOLD"
    assert data["status"] == "PENDING_EVALUATION"
