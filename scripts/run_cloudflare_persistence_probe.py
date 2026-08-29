from __future__ import annotations

import argparse
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


BASE_URL = os.environ.get("MATVERSE_PROBE_BASE_URL", "http://127.0.0.1:8787").rstrip("/")
PRINCIPAL = os.environ.get("MATVERSE_PROBE_PRINCIPAL", "pilot-local")
SECRET = os.environ.get("MATVERSE_PROBE_SECRET", "")


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
    if not SECRET:
        raise RuntimeError("MATVERSE_PROBE_SECRET is required")
    nonce = nonce or ("probe-" + secrets.token_hex(16))
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
        with urlopen(request, timeout=20) as response:
            raw = response.read()
            status = response.status
    except HTTPError as exc:
        raw = exc.read()
        status = exc.code
    parsed: Any = None
    if raw:
        parsed = json.loads(raw.decode("utf-8"))
    return status, parsed, {"nonce": nonce, "timestamp": timestamp, "content_sha256": content_sha256}


def _require(status: int, expected: int, payload: Any, label: str) -> None:
    if status != expected:
        raise RuntimeError(f"{label}: expected HTTP {expected}, got {status}: {payload!r}")


def phase_create(checkpoint_path: Path, evidence_path: Path) -> None:
    replay_nonce = "persist-replay-" + secrets.token_hex(12)
    replay_timestamp = str(int(time.time()))
    status, projection, replay_auth = _request(
        "GET",
        "/institutional/projection",
        nonce=replay_nonce,
        timestamp=replay_timestamp,
    )
    _require(status, 200, projection, "initial projection")
    if not isinstance(projection, dict):
        raise RuntimeError("initial projection is not an object")

    source = {
        **projection["source"],
        "projection_hash": projection["projection"]["projection_hash"],
    }
    intent_id = "restart-probe-" + secrets.token_hex(12)
    intent: dict[str, Any] = {
        "schema_version": "matverse.institutional-intent.v1",
        "intent_id": intent_id,
        "requested_operation": "REQUEST_AUTHORIZATION",
        "actor_id": PRINCIPAL,
        "target": {"kind": "SYSTEM", "id": "cloudflare-restart-probe"},
        "parameters": {"probe": "durable-object-restart", "version": 1},
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": source,
        "hash_algorithm": "SHA-256",
        "canonicalization": "RFC8785_JCS",
        "hash_excludes": ["intent_hash"],
    }
    intent["intent_hash"] = jcs_subset_hash(intent)
    body = _canonical_json(intent)
    status, accepted, _ = _request("POST", "/institutional/intents", body=body)
    _require(status, 200, accepted, "intent acceptance")
    if not isinstance(accepted, dict) or accepted.get("acceptance_decision") != "PASS":
        raise RuntimeError(f"intent was not accepted: {accepted!r}")
    if accepted.get("execution_decision") != "HOLD":
        raise RuntimeError("acceptance improperly promoted execution")

    status, stored, _ = _request("GET", f"/institutional/intents/{intent_id}")
    _require(status, 200, stored, "intent readback")
    if not isinstance(stored, dict) or stored.get("intent_hash") != intent["intent_hash"]:
        raise RuntimeError("intent readback mismatch")

    status, after_projection, _ = _request("GET", "/institutional/projection")
    _require(status, 200, after_projection, "projection after acceptance")
    source_receipt = after_projection["projection"]["source_receipt"]
    receipt_hash = accepted["receipt"]["event_hash"]
    if source_receipt != receipt_hash:
        raise RuntimeError("projection ledger head does not match acceptance receipt")

    checkpoint = {
        "intent_id": intent_id,
        "intent_hash": intent["intent_hash"],
        "receipt_hash": receipt_hash,
        "source_receipt": source_receipt,
        "replay_auth": replay_auth,
        "initial_projection_hash": projection["projection"]["projection_hash"],
        "post_accept_projection_hash": after_projection["projection"]["projection_hash"],
    }
    checkpoint_path.write_text(json.dumps(checkpoint, sort_keys=True, indent=2), encoding="utf-8")
    evidence_path.write_text(
        json.dumps(
            {
                "gate": "CLOUDFLARE_DURABLE_RESTART_LOCAL",
                "phase": "PRE_RESTART",
                "status": "PENDING_RESTART_VERIFICATION",
                **checkpoint,
            },
            sort_keys=True,
            indent=2,
        ),
        encoding="utf-8",
    )


def phase_verify(checkpoint_path: Path, evidence_path: Path) -> None:
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))

    replay_auth = checkpoint["replay_auth"]
    status, replay_payload, _ = _request(
        "GET",
        "/institutional/projection",
        nonce=replay_auth["nonce"],
        timestamp=replay_auth["timestamp"],
    )
    _require(status, 409, replay_payload, "nonce replay after restart")
    if not isinstance(replay_payload, dict) or replay_payload.get("detail") != "authentication nonce replayed":
        raise RuntimeError(f"unexpected replay rejection: {replay_payload!r}")

    intent_id = checkpoint["intent_id"]
    status, stored, _ = _request("GET", f"/institutional/intents/{intent_id}")
    _require(status, 200, stored, "intent after restart")
    if not isinstance(stored, dict):
        raise RuntimeError("post-restart intent is not an object")
    if stored.get("intent_hash") != checkpoint["intent_hash"]:
        raise RuntimeError("post-restart intent hash mismatch")
    if stored.get("receipt", {}).get("event_hash") != checkpoint["receipt_hash"]:
        raise RuntimeError("post-restart receipt hash mismatch")

    status, projection, _ = _request("GET", "/institutional/projection")
    _require(status, 200, projection, "projection after restart")
    if projection["projection"]["source_receipt"] != checkpoint["source_receipt"]:
        raise RuntimeError("ledger head changed across restart without a canonical event")

    final_evidence = {
        "gate": "CLOUDFLARE_DURABLE_RESTART_LOCAL",
        "status": "PASS",
        "persistence": {
            "intent_survived_restart": True,
            "receipt_survived_restart": True,
            "ledger_head_survived_restart": True,
            "nonce_replay_protection_survived_restart": True,
        },
        "intent_id": intent_id,
        "intent_hash": checkpoint["intent_hash"],
        "receipt_hash": checkpoint["receipt_hash"],
        "source_receipt": checkpoint["source_receipt"],
        "post_restart_projection_hash": projection["projection"]["projection_hash"],
        "execution_decision": stored.get("execution_decision"),
    }
    evidence_path.write_text(json.dumps(final_evidence, sort_keys=True, indent=2), encoding="utf-8")
    digest = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    print(json.dumps({"status": "PASS", "evidence_sha256": digest, **final_evidence}, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("create", "verify"))
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    if args.phase == "create":
        phase_create(args.checkpoint, args.evidence)
    else:
        phase_verify(args.checkpoint, args.evidence)


if __name__ == "__main__":
    main()
