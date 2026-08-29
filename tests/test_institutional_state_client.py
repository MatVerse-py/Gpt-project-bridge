from __future__ import annotations

import hashlib
import json
from io import BytesIO

import pytest

from app import institutional_state_client as client
from app.institutional_projection import build_institutional_projection_from_snapshot, jcs_subset_hash


class _Response:
    def __init__(self, value):
        self._body = json.dumps(value, separators=(",", ":")).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._body


def test_remote_state_is_opt_in(monkeypatch):
    monkeypatch.delenv("MATVERSE_INSTITUTIONAL_STATE_URL", raising=False)
    assert client.remote_state_enabled() is False
    monkeypatch.setenv("MATVERSE_INSTITUTIONAL_STATE_URL", "http://state.matverse.internal")
    assert client.remote_state_enabled() is True


def test_remote_snapshot_transport(monkeypatch):
    monkeypatch.setenv("MATVERSE_INSTITUTIONAL_STATE_URL", "http://state.matverse.internal")

    def fake_urlopen(request, timeout):
        assert request.full_url == "http://state.matverse.internal/v1/snapshot"
        assert timeout == 5.0
        return _Response({"ledger": [], "contract_artifacts": []})

    monkeypatch.setattr(client, "urlopen", fake_urlopen)
    assert client.fetch_state_snapshot() == {"ledger": [], "contract_artifacts": []}


def test_remote_nonce_transport(monkeypatch):
    monkeypatch.setenv("MATVERSE_INSTITUTIONAL_STATE_URL", "http://state.matverse.internal")

    def fake_urlopen(request, timeout):
        body = json.loads(request.data.decode("utf-8"))
        assert body == {"expires_at": 123, "nonce": "nonce-0123456789", "principal_id": "principal"}
        return _Response({"consumed": True})

    monkeypatch.setattr(client, "urlopen", fake_urlopen)
    assert client.consume_auth_nonce("principal", "nonce-0123456789", 123) is True


def test_invalid_remote_url_fails_closed(monkeypatch):
    monkeypatch.setenv("MATVERSE_INSTITUTIONAL_STATE_URL", "state.internal")
    with pytest.raises(client.InstitutionalStateUnavailable, match="http:// or https://"):
        client.fetch_state_snapshot()


def test_projection_from_durable_snapshot_verifies_chain(monkeypatch):
    commit = "a" * 40
    frozen = "b" * 64
    monkeypatch.setenv("MATVERSE_BUILD_COMMIT", commit)
    monkeypatch.setenv("MATVERSE_BUILD_REF", "main")
    monkeypatch.setenv("MATVERSE_FROZEN_CONTRACT_HASH", frozen)
    monkeypatch.setenv("MATVERSE_BUILD_TIMESTAMP", "2026-08-29T00:00:00+00:00")

    event = {
        "event_type": "INSTITUTIONAL_INTENT_ACCEPTED",
        "source_commit": commit,
        "created_at": "2026-08-29T00:00:00+00:00",
    }
    event_json = json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    event_hash = hashlib.sha256(("GENESIS" + event_json + "PASS").encode("utf-8")).hexdigest()
    snapshot = {
        "ledger": [
            {
                "seq": 1,
                "prev_hash": "GENESIS",
                "event_hash": event_hash,
                "event_json": event_json,
                "decision": "PASS",
            }
        ],
        "contract_artifacts": [],
    }
    projection = build_institutional_projection_from_snapshot(snapshot)
    assert projection["projection"]["source_receipt"] == event_hash
    assert projection["receipts"][0]["receipt_hash"] == event_hash
    assert len(projection["projection"]["projection_hash"]) == 64


def test_projection_from_snapshot_rejects_broken_chain(monkeypatch):
    monkeypatch.setenv("MATVERSE_BUILD_COMMIT", "a" * 40)
    monkeypatch.setenv("MATVERSE_BUILD_REF", "main")
    monkeypatch.setenv("MATVERSE_FROZEN_CONTRACT_HASH", "b" * 64)
    snapshot = {
        "ledger": [
            {
                "seq": 1,
                "prev_hash": "GENESIS",
                "event_hash": "0" * 64,
                "event_json": "{}",
                "decision": "PASS",
            }
        ],
        "contract_artifacts": [],
    }
    from app.institutional_projection import ProjectionUnavailable

    with pytest.raises(ProjectionUnavailable, match="ledger integrity failure"):
        build_institutional_projection_from_snapshot(snapshot)
