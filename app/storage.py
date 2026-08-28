from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from .core import stable_hash
from .model_bridge import build_handoff_digest

DB_PATH = Path(os.environ.get("MATVERSE_DB", "matverse.db"))
CONTRACT_KINDS = {"ontology", "policy", "task", "rubric", "memory_policy"}
_GIT_OBJECT_ID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("CREATE TABLE IF NOT EXISTS ledger (seq INTEGER PRIMARY KEY AUTOINCREMENT, prev_hash TEXT NOT NULL, event_hash TEXT NOT NULL UNIQUE, event_json TEXT NOT NULL, decision TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS auth_nonces (principal_id TEXT NOT NULL, nonce TEXT NOT NULL, expires_at INTEGER NOT NULL, PRIMARY KEY(principal_id,nonce))")
    conn.execute("CREATE TABLE IF NOT EXISTS contract_artifacts (artifact_hash TEXT PRIMARY KEY, kind TEXT NOT NULL, version TEXT NOT NULL, content_json TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS model_sessions (session_id TEXT PRIMARY KEY, protocol_version TEXT NOT NULL, contract_json TEXT NOT NULL, contract_hash TEXT NOT NULL, participants_json TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL)")
    session_columns = {row[1] for row in conn.execute("PRAGMA table_info(model_sessions)").fetchall()}
    if "created_by" not in session_columns:
        conn.execute("ALTER TABLE model_sessions ADD COLUMN created_by TEXT NOT NULL DEFAULT 'legacy'")
    conn.execute("""CREATE TABLE IF NOT EXISTS model_handoffs (
        handoff_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, session_seq INTEGER NOT NULL,
        from_participant TEXT NOT NULL, to_participant TEXT NOT NULL, parent_handoff_id TEXT,
        payload_json TEXT NOT NULL, payload_hash TEXT NOT NULL, contract_hash TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('PENDING','ACKED')), created_at TEXT NOT NULL, acked_at TEXT,
        FOREIGN KEY(session_id) REFERENCES model_sessions(session_id),
        FOREIGN KEY(parent_handoff_id) REFERENCES model_handoffs(handoff_id),
        UNIQUE(session_id,session_seq), UNIQUE(session_id,payload_hash)
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_model_handoffs_inbox ON model_handoffs(session_id,to_participant,status,session_seq)")
    conn.commit()
    return conn


def consume_auth_nonce(principal_id: str, nonce: str, expires_at: int) -> bool:
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM auth_nonces WHERE expires_at < strftime('%s','now')")
        try:
            conn.execute("INSERT INTO auth_nonces(principal_id,nonce,expires_at) VALUES (?,?,?)", (principal_id, nonce, expires_at))
        except sqlite3.IntegrityError:
            conn.rollback()
            return False
        conn.commit()
        return True
    finally:
        conn.close()


def _append_ledger_tx(conn: sqlite3.Connection, event: dict[str, Any], decision: str) -> dict[str, Any]:
    """Append one hash-chained event with immutable operational provenance.

    `ledger_at` is injected into the hashed event so every canonical append has
    a monotonic observation timestamp for projections. When a real deployment
    commit is provisioned, `source_commit` is also injected into the hashed
    event. A caller-supplied source commit must itself be a valid Git object id.
    """

    event_to_store = dict(event)
    event_to_store.setdefault("ledger_at", _now())

    configured_commit = os.environ.get("MATVERSE_BUILD_COMMIT", "").lower()
    supplied_commit = event_to_store.get("source_commit")
    if supplied_commit is not None:
        if not isinstance(supplied_commit, str) or _GIT_OBJECT_ID.fullmatch(supplied_commit.lower()) is None:
            raise ValueError("event source_commit must be a 40- or 64-character Git object id")
        event_to_store["source_commit"] = supplied_commit.lower()
    elif _GIT_OBJECT_ID.fullmatch(configured_commit) is not None:
        event_to_store["source_commit"] = configured_commit

    last = conn.execute("SELECT event_hash FROM ledger ORDER BY seq DESC LIMIT 1").fetchone()
    prev_hash = last["event_hash"] if last else "GENESIS"
    payload = _canonical_json(event_to_store)
    event_hash = sha256((prev_hash + payload + decision).encode("utf-8")).hexdigest()
    cur = conn.execute("INSERT INTO ledger(prev_hash,event_hash,event_json,decision) VALUES (?,?,?,?)", (prev_hash, event_hash, payload, decision))
    return {"seq": cur.lastrowid, "prev_hash": prev_hash, "event_hash": event_hash, "decision": decision}


def append_event(event: dict[str, Any], decision: str) -> dict[str, Any]:
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        receipt = _append_ledger_tx(conn, event, decision)
        conn.commit()
        return receipt
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def read_ledger() -> list[dict[str, Any]]:
    conn = _connect()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM ledger ORDER BY seq").fetchall()]
    finally:
        conn.close()


def verify_chain() -> dict[str, Any]:
    rows = read_ledger()
    prev = "GENESIS"
    for row in rows:
        expected = sha256((prev + row["event_json"] + row["decision"]).encode("utf-8")).hexdigest()
        if row["prev_hash"] != prev or row["event_hash"] != expected:
            return {"ok": False, "failed_seq": row["seq"]}
        prev = row["event_hash"]
    return {"ok": True, "events": len(rows), "head": prev}


def replay() -> dict[str, Any]:
    state: dict[str, Any] = {"accepted": 0, "blocked": 0, "held": 0, "last_event": None}
    for row in read_ledger():
        event = json.loads(row["event_json"])
        if row["decision"] == "PASS":
            state["accepted"] += 1
            state["last_event"] = event
        elif row["decision"] == "BLOCK":
            state["blocked"] += 1
        else:
            state["held"] += 1
    return state


def register_contract_artifact(*, kind: str, version: str, content: dict[str, Any], created_by: str) -> dict[str, Any]:
    if kind not in CONTRACT_KINDS:
        raise ValueError(f"unsupported contract artifact kind: {kind}")
    artifact_hash = stable_hash(content)
    created_at = _now()
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute("SELECT * FROM contract_artifacts WHERE artifact_hash=?", (artifact_hash,)).fetchone()
        if existing is not None:
            if existing["kind"] != kind or existing["content_json"] != _canonical_json(content):
                raise ValueError("contract artifact hash collision or kind mismatch")
            conn.commit()
            return {"artifact_hash": artifact_hash, "kind": existing["kind"], "version": existing["version"], "created_by": existing["created_by"], "created_at": existing["created_at"], "idempotent": True}
        conn.execute("INSERT INTO contract_artifacts(artifact_hash,kind,version,content_json,created_by,created_at) VALUES (?,?,?,?,?,?)", (artifact_hash, kind, version, _canonical_json(content), created_by, created_at))
        receipt = _append_ledger_tx(conn, {"event_type": "CONTRACT_ARTIFACT_REGISTERED", "artifact_hash": artifact_hash, "kind": kind, "version": version, "created_by": created_by, "created_at": created_at}, "PASS")
        conn.commit()
        return {"artifact_hash": artifact_hash, "kind": kind, "version": version, "created_by": created_by, "created_at": created_at, "idempotent": False, "receipt": receipt}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_contract_artifact(artifact_hash: str) -> dict[str, Any] | None:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM contract_artifacts WHERE artifact_hash=?", (artifact_hash,)).fetchone()
        if row is None:
            return None
        return {"artifact_hash": row["artifact_hash"], "kind": row["kind"], "version": row["version"], "content": json.loads(row["content_json"]), "created_by": row["created_by"], "created_at": row["created_at"]}
    finally:
        conn.close()


def validate_contract_registry(contract: dict[str, str]) -> None:
    mapping = {
        "ontology_hash": "ontology",
        "policy_hash": "policy",
        "task_hash": "task",
        "rubric_hash": "rubric",
        "memory_policy_hash": "memory_policy",
    }
    missing: list[str] = []
    wrong: list[str] = []
    for field, kind in mapping.items():
        artifact = get_contract_artifact(contract[field])
        if artifact is None:
            missing.append(field)
        elif artifact["kind"] != kind:
            wrong.append(f"{field}:{artifact['kind']}!=expected:{kind}")
    if missing or wrong:
        detail = []
        if missing:
            detail.append("unregistered=" + ",".join(sorted(missing)))
        if wrong:
            detail.append("kind_mismatch=" + ",".join(sorted(wrong)))
        raise PermissionError("contract registry validation failed: " + "; ".join(detail))


def create_model_session(*, session_id: str, protocol_version: str, contract: dict[str, str], frozen_contract_hash: str, participants: list[dict[str, Any]], created_by: str) -> dict[str, Any]:
    validate_contract_registry(contract)
    conn = _connect()
    created_at = _now()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO model_sessions(session_id,protocol_version,contract_json,contract_hash,participants_json,created_by,created_at) VALUES (?,?,?,?,?,?,?)", (session_id, protocol_version, _canonical_json(contract), frozen_contract_hash, _canonical_json(participants), created_by, created_at))
        receipt = _append_ledger_tx(conn, {"event_type": "MODEL_BRIDGE_SESSION_CREATED", "session_id": session_id, "protocol_version": protocol_version, "contract_hash": frozen_contract_hash, "participants": [p["participant_id"] for p in participants], "created_by": created_by, "created_at": created_at}, "PASS")
        conn.commit()
        return {"session_id": session_id, "protocol_version": protocol_version, "contract_hash": frozen_contract_hash, "participants": participants, "created_by": created_by, "created_at": created_at, "receipt": receipt}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_model_session(session_id: str) -> dict[str, Any] | None:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM model_sessions WHERE session_id=?", (session_id,)).fetchone()
        if row is None:
            return None
        return {"session_id": row["session_id"], "protocol_version": row["protocol_version"], "contract": json.loads(row["contract_json"]), "contract_hash": row["contract_hash"], "participants": json.loads(row["participants_json"]), "created_by": row["created_by"], "created_at": row["created_at"]}
    finally:
        conn.close()


def append_model_handoff(*, session_id: str, from_participant: str, to_participant: str, parent_handoff_id: str | None, payload: dict[str, Any], expected_contract_hash: str) -> dict[str, Any]:
    conn = _connect()
    created_at = _now()
    try:
        conn.execute("BEGIN IMMEDIATE")
        session = conn.execute("SELECT * FROM model_sessions WHERE session_id=?", (session_id,)).fetchone()
        if session is None:
            raise LookupError("model bridge session not found")
        if session["contract_hash"] != expected_contract_hash:
            raise PermissionError("frozen contract hash mismatch")
        participants = json.loads(session["participants_json"])
        participant_ids = {p["participant_id"] for p in participants}
        if from_participant not in participant_ids or to_participant not in participant_ids:
            raise PermissionError("handoff participant is not enrolled in session")
        if from_participant == to_participant:
            raise ValueError("cross-model handoff requires distinct participants")
        if parent_handoff_id is not None:
            parent = conn.execute("SELECT handoff_id,session_id FROM model_handoffs WHERE handoff_id=?", (parent_handoff_id,)).fetchone()
            if parent is None or parent["session_id"] != session_id:
                raise ValueError("parent handoff does not belong to session")
        sequence = int(conn.execute("SELECT COALESCE(MAX(session_seq),0)+1 AS next_seq FROM model_handoffs WHERE session_id=?", (session_id,)).fetchone()["next_seq"])
        payload_hash = build_handoff_digest(session_id=session_id, sequence=sequence, from_participant=from_participant, to_participant=to_participant, parent_handoff_id=parent_handoff_id, payload=payload, frozen_contract_hash=expected_contract_hash)
        handoff_id = f"mh_{payload_hash[:32]}"
        conn.execute("INSERT INTO model_handoffs(handoff_id,session_id,session_seq,from_participant,to_participant,parent_handoff_id,payload_json,payload_hash,contract_hash,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,'PENDING',?)", (handoff_id, session_id, sequence, from_participant, to_participant, parent_handoff_id, _canonical_json(payload), payload_hash, expected_contract_hash, created_at))
        receipt = _append_ledger_tx(conn, {"event_type": "MODEL_BRIDGE_HANDOFF", "session_id": session_id, "handoff_id": handoff_id, "session_seq": sequence, "from_participant": from_participant, "to_participant": to_participant, "parent_handoff_id": parent_handoff_id, "payload_hash": payload_hash, "contract_hash": expected_contract_hash, "created_at": created_at}, "PASS")
        conn.commit()
        return {"handoff_id": handoff_id, "session_id": session_id, "session_seq": sequence, "from_participant": from_participant, "to_participant": to_participant, "parent_handoff_id": parent_handoff_id, "payload_hash": payload_hash, "contract_hash": expected_contract_hash, "status": "PENDING", "created_at": created_at, "receipt": receipt}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_model_inbox(session_id: str, participant_id: str) -> list[dict[str, Any]]:
    session = get_model_session(session_id)
    if session is None:
        raise LookupError("model bridge session not found")
    if participant_id not in {p["participant_id"] for p in session["participants"]}:
        raise PermissionError("participant is not enrolled in session")
    conn = _connect()
    try:
        rows = conn.execute("SELECT * FROM model_handoffs WHERE session_id=? AND to_participant=? AND status='PENDING' ORDER BY session_seq", (session_id, participant_id)).fetchall()
        return [{"handoff_id": row["handoff_id"], "session_id": row["session_id"], "session_seq": row["session_seq"], "from_participant": row["from_participant"], "to_participant": row["to_participant"], "parent_handoff_id": row["parent_handoff_id"], "payload": json.loads(row["payload_json"]), "payload_hash": row["payload_hash"], "contract_hash": row["contract_hash"], "status": row["status"], "created_at": row["created_at"]} for row in rows]
    finally:
        conn.close()


def acknowledge_model_handoff(handoff_id: str, participant_id: str) -> dict[str, Any]:
    conn = _connect()
    acked_at = _now()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM model_handoffs WHERE handoff_id=?", (handoff_id,)).fetchone()
        if row is None:
            raise LookupError("model handoff not found")
        if row["to_participant"] != participant_id:
            raise PermissionError("only the target participant can acknowledge this handoff")
        if row["status"] == "ACKED":
            conn.commit()
            return {"handoff_id": handoff_id, "status": "ACKED", "acked_at": row["acked_at"], "idempotent": True}
        conn.execute("UPDATE model_handoffs SET status='ACKED',acked_at=? WHERE handoff_id=? AND status='PENDING'", (acked_at, handoff_id))
        receipt = _append_ledger_tx(conn, {"event_type": "MODEL_BRIDGE_HANDOFF_ACK", "session_id": row["session_id"], "handoff_id": handoff_id, "participant_id": participant_id, "acked_at": acked_at}, "PASS")
        conn.commit()
        return {"handoff_id": handoff_id, "status": "ACKED", "acked_at": acked_at, "idempotent": False, "receipt": receipt}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
