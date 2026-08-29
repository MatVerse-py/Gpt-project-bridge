from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.auth import sign_request
from app.institutional_projection import jcs_subset_hash


BASE_URL = os.environ.get("MATVERSE_PROBE_BASE_URL", "").rstrip("/")
PRINCIPAL = os.environ.get("MATVERSE_PROBE_PRINCIPAL", "pilot-remote")
SECRET = os.environ.get("MATVERSE_PROBE_SECRET", "")
EXPECTED_COMMIT = os.environ.get("MATVERSE_EXPECTED_COMMIT", "")
EVIDENCE_PATH = Path(
    os.environ.get(
        "MATVERSE_REMOTE_EVIDENCE_PATH",
        "evidence/cloudflare_remote_pilot.json",
    )
)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _request(
    method: str,
    path: str,
    *,
    body: bytes = b"",
    nonce: str | None = None,
    timestamp: str | None = None,
) -> tuple[int, Any, dict[str, str]]:
    if not BASE_URL.startswith("https://"):
        raise RuntimeError("MATVERSE_PROBE_BASE_URL must be HTTPS")
    if not SECRET:
        raise RuntimeError("MATVERSE_PROBE_SECRET is required")
    nonce = nonce or ("remote-" + secrets.token_hex(16))
    timestamp = timestamp or str(int(time.time()))
    content_sha256 = hashlib.sha256(body).hexdigest()
    signature = sign_request(SECRET, method, path, timestamp, nonce, content_sha256)
    headers = {
        "Accept": "application/json",
        "X-MatVerse-Principal": PRINCIPAL,
        "X-MatVerse-Timestamp": timestamp,
        "X-MatVerse-Nonce": nonce,
        "X-MatVerse-Content-SHA256": content_sha256,
        "X-MatVerse-Signature": signature,
    }
    if body:
        headers["Content-Type"] = "application/json"
    request = Request(BASE_URL + path, data=body if body else None, headers=headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read()
            status = response.status
    except HTTPError as exc:
        raw = exc.read()
        status = exc.code
    payload: Any = None
    if raw:
        payload = json.loads(raw.decode("utf-8"))
    return status, payload, {"nonce": nonce, "timestamp": timestamp, "content_sha256": content_sha256}


def _require(status: int, expected: int, payload: Any, label: str) -> None:
    if status != expected:
        raise RuntimeError(f"{label}: expected HTTP {expected}, got {status}: {payload!r}")


def main() -> None:
    if not EXPECTED_COMMIT:
        raise RuntimeError("MATVERSE_EXPECTED_COMMIT is required")

    status, runtime, _ = _request("GET", "/institutional/runtime")
    _require(status, 200, runtime, "runtime handshake")
    if not isinstance(runtime, dict) or runtime.get("status") != "READY":
        raise RuntimeError(f"runtime is not READY: {runtime!r}")
    if runtime.get("intent_execution") != "HOLD":
        raise RuntimeError("runtime improperly promotes intent execution")
    source = runtime.get("source")
    if not isinstance(source, dict) or source.get("commit_sha") != EXPECTED_COMMIT:
        raise RuntimeError(
            f"remote runtime commit mismatch: expected {EXPECTED_COMMIT}, got {source!r}"
        )

    replay_nonce = "remote-replay-" + secrets.token_hex(12)
    replay_timestamp = str(int(time.time()))
    status, projection, replay_auth = _request(
        "GET",
        "/institutional/projection",
        nonce=replay_nonce,
        timestamp=replay_timestamp,
    )
    _require(status, 200, projection, "remote projection")
    if not isinstance(projection, dict):
        raise RuntimeError("remote projection is not an object")
    if projection.get("source", {}).get("commit_sha") != EXPECTED_COMMIT:
        raise RuntimeError("remote projection commit binding mismatch")

    intent_source = {
        **projection["source"],
        "projection_hash": projection["projection"]["projection_hash"],
    }
    intent_id = "remote-pilot-" + secrets.token_hex(12)
    intent: dict[str, Any] = {
        "schema_version": "matverse.institutional-intent.v1",
        "intent_id": intent_id,
        "requested_operation": "REQUEST_AUTHORIZATION",
        "actor_id": PRINCIPAL,
        "target": {"kind": "SYSTEM", "id": "cloudflare-remote-controlled-pilot"},
        "parameters": {"probe": "remote-cloudflare-controlled-pilot", "version": 1},
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": intent_source,
        "hash_algorithm": "SHA-256",
        "canonicalization": "RFC8785_JCS",
        "hash_excludes": ["intent_hash"],
    }
    intent["intent_hash"] = jcs_subset_hash(intent)
    body = _canonical_json(intent)

    status, accepted, _ = _request("POST", "/institutional/intents", body=body)
    _require(status, 200, accepted, "remote intent acceptance")
    if not isinstance(accepted, dict) or accepted.get("acceptance_decision") != "PASS":
        raise RuntimeError(f"remote intent was not accepted: {accepted!r}")
    if accepted.get("execution_decision") != "HOLD":
        raise RuntimeError("remote acceptance improperly promoted execution")
    if accepted.get("status") != "PENDING_EVALUATION":
        raise RuntimeError("remote intent did not remain PENDING_EVALUATION")

    status, stored, _ = _request("GET", f"/institutional/intents/{intent_id}")
    _require(status, 200, stored, "remote intent readback")
    if not isinstance(stored, dict) or stored.get("intent_hash") != intent["intent_hash"]:
        raise RuntimeError("remote intent readback mismatch")

    status, after_projection, _ = _request("GET", "/institutional/projection")
    _require(status, 200, after_projection, "remote projection after acceptance")
    receipt_hash = accepted.get("receipt", {}).get("event_hash")
    source_receipt = after_projection.get("projection", {}).get("source_receipt")
    if not isinstance(receipt_hash, str) or source_receipt != receipt_hash:
        raise RuntimeError("remote ledger head does not match acceptance receipt")

    status, replay_payload, _ = _request(
        "GET",
        "/institutional/projection",
        nonce=replay_auth["nonce"],
        timestamp=replay_auth["timestamp"],
    )
    _require(status, 409, replay_payload, "remote nonce replay")
    if not isinstance(replay_payload, dict) or replay_payload.get("detail") != "authentication nonce replayed":
        raise RuntimeError(f"unexpected remote nonce replay response: {replay_payload!r}")

    evidence = {
        "gate": "REMOTE_CLOUDFLARE_CONTROLLED_PILOT_ENDPOINT",
        "status": "PASS",
        "endpoint": BASE_URL,
        "https": True,
        "runtime_id": runtime.get("runtime_id"),
        "commit_sha": EXPECTED_COMMIT,
        "authenticated_principal_id": runtime.get("authenticated_principal_id"),
        "intent_id": intent_id,
        "intent_hash": intent["intent_hash"],
        "receipt_hash": receipt_hash,
        "ledger_head": source_receipt,
        "acceptance_decision": "PASS",
        "execution_decision": "HOLD",
        "intent_status": "PENDING_EVALUATION",
        "nonce_replay_protection": "PASS",
        "remote_restart_proven": False,
        "paid_persistent_pilot": "HOLD_REMOTE_RESTART_NOT_PROVEN",
    }
    EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE_PATH.write_text(json.dumps(evidence, sort_keys=True, indent=2), encoding="utf-8")
    digest = hashlib.sha256(EVIDENCE_PATH.read_bytes()).hexdigest()
    print(json.dumps({"evidence_sha256": digest, **evidence}, sort_keys=True))


if __name__ == "__main__":
    main()
