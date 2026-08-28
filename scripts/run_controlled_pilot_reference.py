from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import uuid

from fastapi.testclient import TestClient

from app import storage
from app.auth import sign_request
from app.evidence import evidence_receipt
from app.institutional_projection import jcs_subset_hash
from app.institutional_service import app

FROZEN_CONTRACT = "67743cbe1f4d65983348401d2061e46dec22d57e232854ff263e1d646f600b26"
PRINCIPAL = "pilot-buyer-reference"
REFERENCE_SECRET = "ci-reference-secret-not-for-production-0001"
RUNTIME_ID = "matverse-controlled-pilot-ci-v1"


def _body(payload: dict | None) -> bytes:
    if payload is None:
        return b""
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _headers(method: str, path: str, payload: dict | None = None) -> dict[str, str]:
    body = _body(payload)
    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex
    content_hash = hashlib.sha256(body).hexdigest()
    signature = sign_request(REFERENCE_SECRET, method, path, timestamp, nonce, content_hash)
    return {
        "X-MatVerse-Principal": PRINCIPAL,
        "X-MatVerse-Timestamp": timestamp,
        "X-MatVerse-Nonce": nonce,
        "X-MatVerse-Content-SHA256": content_hash,
        "X-MatVerse-Signature": signature,
        **({"Content-Type": "application/json"} if payload is not None else {}),
    }


def _request(client: TestClient, method: str, path: str, payload: dict | None = None):
    body = _body(payload)
    return client.request(method, path, content=body, headers=_headers(method, path, payload))


def _configure(build_commit: str, build_ref: str, db_path: Path) -> None:
    if len(build_commit) not in {40, 64} or any(ch not in "0123456789abcdef" for ch in build_commit.lower()):
        raise SystemExit("--build-commit must be a 40- or 64-character Git object id")
    storage.DB_PATH = db_path
    os.environ["MATVERSE_RUNTIME_ID"] = RUNTIME_ID
    os.environ["MATVERSE_BUILD_COMMIT"] = build_commit.lower()
    os.environ["MATVERSE_BUILD_REF"] = build_ref
    os.environ["MATVERSE_FROZEN_CONTRACT_HASH"] = FROZEN_CONTRACT
    os.environ["MATVERSE_BUILD_TIMESTAMP"] = datetime.now(timezone.utc).isoformat()
    os.environ["MATVERSE_PRINCIPALS_JSON"] = json.dumps(
        {
            PRINCIPAL: {
                "secret": REFERENCE_SECRET,
                "capabilities": [
                    "institutional:projection:read",
                    "institutional:intent:submit",
                    "institutional:intent:read",
                ],
            }
        },
        separators=(",", ":"),
    )


def _build_intent(projection: dict) -> dict:
    source = {
        **projection["source"],
        "projection_hash": projection["projection"]["projection_hash"],
    }
    intent = {
        "schema_version": "matverse.institutional-intent.v1",
        "intent_id": "pilot-reference-authz-001",
        "requested_operation": "REQUEST_AUTHORIZATION",
        "actor_id": PRINCIPAL,
        "target": {"kind": "SYSTEM", "id": "customer-agent-reference"},
        "parameters": {
            "action_class": "DOCUMENT_EXPORT",
            "approval_mode": "HUMAN_REQUIRED",
            "resource": "customer-reference/document-001",
            "scope": "SANDBOX_ONLY",
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "hash_algorithm": "SHA-256",
        "canonicalization": "RFC8785_JCS",
        "hash_excludes": ["intent_hash"],
    }
    intent["intent_hash"] = jcs_subset_hash(intent)
    return intent


def run(build_commit: str, build_ref: str, output: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="matverse-pilot-reference-") as tmp:
        _configure(build_commit, build_ref, Path(tmp) / "pilot.db")
        client = TestClient(app)

        runtime_response = _request(client, "GET", "/institutional/runtime")
        if runtime_response.status_code != 200:
            raise SystemExit(f"runtime handshake failed: {runtime_response.status_code} {runtime_response.text}")
        runtime = runtime_response.json()
        if runtime.get("status") != "READY" or runtime.get("intent_execution") != "HOLD":
            raise SystemExit("runtime violated controlled-pilot authority boundary")

        projection_response = _request(client, "GET", "/institutional/projection")
        if projection_response.status_code != 200:
            raise SystemExit(f"projection failed: {projection_response.status_code} {projection_response.text}")
        projection = projection_response.json()
        if runtime["source"] != projection["source"]:
            raise SystemExit("runtime and projection source bindings differ")
        if runtime["projection_hash"] != projection["projection"]["projection_hash"]:
            raise SystemExit("runtime and projection hashes differ")

        intent = _build_intent(projection)
        response = _request(client, "POST", "/institutional/intents", intent)
        if response.status_code != 200:
            raise SystemExit(f"intent submission failed: {response.status_code} {response.text}")
        accepted = response.json()
        expected = ("PASS", "HOLD", "PENDING_EVALUATION")
        observed = (
            accepted.get("acceptance_decision"),
            accepted.get("execution_decision"),
            accepted.get("status"),
        )
        if observed != expected:
            raise SystemExit(f"intent boundary mismatch: observed={observed!r} expected={expected!r}")

        chain = storage.verify_chain()
        replay = storage.replay()
        ledger = storage.read_ledger()
        if not chain.get("ok") or len(ledger) != 1:
            raise SystemExit(f"ledger integrity failed: {chain!r}")
        event = json.loads(ledger[0]["event_json"])
        if event.get("event_type") != "INSTITUTIONAL_INTENT_ACCEPTED":
            raise SystemExit("unexpected ledger event type")
        if "parameters" in event or event.get("parameters_hash") != accepted.get("parameters_hash"):
            raise SystemExit("parameter persistence boundary violated")
        if replay.get("accepted") != 1 or replay.get("blocked") != 0:
            raise SystemExit(f"unexpected replay state: {replay!r}")

        scenario_inputs = {
            "protocol": runtime["protocol_version"],
            "runtime_id": runtime["runtime_id"],
            "source": runtime["source"],
            "projection_hash": runtime["projection_hash"],
            "intent_hash": intent["intent_hash"],
            "parameters_hash": accepted["parameters_hash"],
        }
        scenario_outputs = {
            "acceptance_decision": accepted["acceptance_decision"],
            "execution_decision": accepted["execution_decision"],
            "status": accepted["status"],
            "ledger_head": chain["head"],
            "ledger_events": chain["events"],
            "replay": replay,
        }
        receipt = evidence_receipt("CONTROLLED_PILOT_REFERENCE", scenario_inputs, scenario_outputs)

        pack = {
            "schema_version": "matverse.controlled-pilot-evidence.v1",
            "evidence_type": "MATVERSE_CONTROLLED_PILOT_REFERENCE",
            "scope": "CI_CUSTOMER_LIKE_REFERENCE_NOT_EXTERNAL",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "product": "MatVerse Trust Runtime — Controlled Pilot v1",
            "scenario": {
                "description": "Authenticated customer-like request asks for authorization to export one sandbox document; the runtime accepts the source-bound intent but deliberately leaves execution on HOLD pending an operation-specific authorization path.",
                "action_class": "DOCUMENT_EXPORT",
                "approval_mode": "HUMAN_REQUIRED",
                "resource": "customer-reference/document-001",
                "scope": "SANDBOX_ONLY",
            },
            "runtime": {
                "runtime_id": runtime["runtime_id"],
                "protocol_version": runtime["protocol_version"],
                "authentication": runtime["authentication"],
                "authenticated_principal_id": runtime["authenticated_principal_id"],
                "source": runtime["source"],
                "projection_hash": runtime["projection_hash"],
                "status": runtime["status"],
                "intent_execution": runtime["intent_execution"],
            },
            "intent": {
                "intent_id": intent["intent_id"],
                "intent_hash": intent["intent_hash"],
                "requested_operation": intent["requested_operation"],
                "target": intent["target"],
                "parameters_hash": accepted["parameters_hash"],
                "parameter_persistence": accepted["parameter_persistence"],
                "acceptance_decision": accepted["acceptance_decision"],
                "execution_decision": accepted["execution_decision"],
                "status": accepted["status"],
                "ledger_receipt": accepted["receipt"],
            },
            "integrity": {
                "ledger_chain": chain,
                "replay": replay,
                "raw_parameters_in_ledger": False,
                "evidence_receipt": receipt,
            },
            "claims": [
                {"claim": "authenticated runtime handshake", "status": "PASS"},
                {"claim": "canonical source-bound projection", "status": "PASS"},
                {"claim": "source-bound intent acceptance", "status": "PASS"},
                {"claim": "execution remains fail-closed without operation-specific authorization", "status": "PASS"},
                {"claim": "hash-chained ledger integrity", "status": "PASS"},
                {"claim": "observable replay of the accepted intent event", "status": "PASS"},
                {"claim": "live HTTPS deployment", "status": "HOLD"},
                {"claim": "real customer deployment", "status": "HOLD"},
                {"claim": "EXTERNAL_PASS", "status": "HOLD"},
                {"claim": "WORLD_REAL_PASS", "status": "HOLD"},
                {"claim": "unrestricted production readiness", "status": "HOLD"},
            ],
            "promotion": {
                "CONTROLLED_PILOT_REFERENCE": "PASS",
                "LIVE_CUSTOMER_PILOT": "HOLD",
                "EXTERNAL_PASS": "HOLD",
                "WORLD_REAL_PASS": "HOLD",
            },
            "non_claims": [
                "No external-provider independence is claimed.",
                "No unrestricted production readiness is claimed.",
                "No OCG, autopoiesis, digital-life, or scientific validation claim follows from this evidence pack.",
                "The HMAC secret used by this CI reference is synthetic and must never be reused in production.",
            ],
        }

        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(pack, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        return pack


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-commit", default=os.environ.get("GITHUB_SHA", ""))
    parser.add_argument("--build-ref", default=os.environ.get("GITHUB_REF_NAME", "ci-reference"))
    parser.add_argument("--output", default="evidence/pilot-v1/EVIDENCE_PACK.json")
    args = parser.parse_args()
    pack = run(args.build_commit, args.build_ref, Path(args.output))
    print(json.dumps(pack["promotion"], sort_keys=True))
    print(f"evidence_pack={args.output}")


if __name__ == "__main__":
    main()
