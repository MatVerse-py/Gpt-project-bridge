from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

from app.auth import sign_request
from app.institutional_projection import jcs_subset_bytes, jcs_subset_hash


VECTORS = Path("contracts/institutional-http-v1-vectors.json")


def _load() -> dict:
    return json.loads(VECTORS.read_text(encoding="utf-8"))


def test_jcs_vector_matches_python_canonicalizer():
    vector = _load()["jcs"]
    assert jcs_subset_bytes(vector["input"]).decode("utf-8") == vector["canonical_utf8_text"]
    assert jcs_subset_hash(vector["input"]) == vector["sha256"]


def test_hmac_vector_matches_canonical_auth():
    vector = _load()["hmac_request"]
    body = vector["body_utf8"].encode("utf-8")
    assert hashlib.sha256(body).hexdigest() == vector["content_sha256"]
    expected = sign_request(
        vector["fixture_secret"],
        vector["method"],
        vector["path"],
        vector["timestamp"],
        vector["nonce"],
        vector["content_sha256"],
    )
    assert hmac.compare_digest(expected, vector["signature"])


def test_intent_vector_matches_python_canonicalizer():
    vector = _load()["intent"]
    assert jcs_subset_hash(vector["payload_without_intent_hash"]) == vector["intent_hash"]
