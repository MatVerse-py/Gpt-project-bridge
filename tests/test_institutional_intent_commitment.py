from __future__ import annotations

import json

import pytest

from app import storage
from app.institutional_projection import build_institutional_projection, jcs_subset_hash
from app.institutional_store import get_intent, persist_intent


BUILD_COMMIT = "a" * 40
FROZEN_CONTRACT = "b" * 64


def _intent(parameters=None, *, source: dict | None = None) -> dict:
    payload = {
        "schema_version": "matverse.institutional-intent.v1",
        "intent_id": "commitment-1",
        "requested_operation": "OTHER",
        "actor_id": "admin",
        "target": {"kind": "OTHER", "id": "target-1"},
        "parameters": {"message": "not persisted raw"} if parameters is None else parameters,
        "created_at": "2026-08-25T21:45:00Z",
        "source": source
        or {
            "repository": "MatVerse-py/Gpt-project-bridge",
            "commit_sha": "a" * 40,
            "ref": "main",
            "frozen_contract_hash": "b" * 64,
            "gate_fingerprint": "c" * 64,
            "constitutional_contract_hash": "d" * 64,
            "projection_hash": "e" * 64,
        },
        "hash_algorithm": "SHA-256",
        "canonicalization": "RFC8785_JCS",
        "hash_excludes": ["intent_hash"],
    }
    payload["intent_hash"] = jcs_subset_hash(payload)
    return payload


def _canonical_source(monkeypatch) -> dict:
    monkeypatch.setenv("MATVERSE_BUILD_COMMIT", BUILD_COMMIT)
    monkeypatch.setenv("MATVERSE_BUILD_REF", "main")
    monkeypatch.setenv("MATVERSE_FROZEN_CONTRACT_HASH", FROZEN_CONTRACT)
    monkeypatch.setenv("MATVERSE_BUILD_TIMESTAMP", "2026-08-25T21:45:00Z")
    projection = build_institutional_projection()
    return {**projection["source"], "projection_hash": projection["projection"]["projection_hash"]}


def test_canonical_intent_store_persists_only_parameter_commitment(monkeypatch):
    intent = _intent(source=_canonical_source(monkeypatch))
    result = persist_intent(intent=intent, principal_id="admin")
    assert result["parameter_persistence"] == "HASH_ONLY"
    assert result["parameters_hash"] == jcs_subset_hash(intent["parameters"])

    stored = get_intent("commitment-1")
    assert stored is not None
    assert stored["parameter_persistence"] == "HASH_ONLY"
    assert "parameters" not in stored

    conn = storage._connect()
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(institutional_intents)").fetchall()}
    finally:
        conn.close()
    assert "parameters_json" not in columns

    event = json.loads(storage.read_ledger()[0]["event_json"])
    assert event["parameter_persistence"] == "HASH_ONLY"
    assert event["parameters_hash"] == jcs_subset_hash(intent["parameters"])
    assert "parameters" not in event


def test_internal_store_rejects_actor_bypass_and_hash_tampering():
    intent = _intent()
    with pytest.raises(ValueError, match="actor_id"):
        persist_intent(intent=intent, principal_id="other")

    tampered = dict(intent)
    tampered["parameters"] = {"message": "changed"}
    with pytest.raises(ValueError, match="intent_hash mismatch"):
        persist_intent(intent=tampered, principal_id="admin")
