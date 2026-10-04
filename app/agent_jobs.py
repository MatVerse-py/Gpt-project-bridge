"""Durable dispatch, reply and acknowledgement on the existing transactional store."""
from __future__ import annotations
import json
import time
from typing import Any

from . import storage
from .core import stable_hash
from .model_bridge import assert_transferable_state, build_handoff_digest


class AgentConflict(RuntimeError):
    pass

def job_for_handoff(handoff_id: str) -> dict[str, Any] | None:
    conn = storage._connect()
    try:
        _schema(conn)
        row = conn.execute("SELECT job_id FROM agent_jobs WHERE handoff_id=?", (handoff_id,)).fetchone()
        return get_job(row["job_id"]) if row else None
    finally:
        conn.close()



def _schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS agent_jobs(
        job_id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, session_id TEXT NOT NULL,
        handoff_id TEXT NOT NULL UNIQUE, contract_hash TEXT NOT NULL, actor TEXT NOT NULL,
        transport TEXT NOT NULL, requested_model TEXT NOT NULL, status TEXT NOT NULL,
        deadline INTEGER NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL,
        FOREIGN KEY(handoff_id) REFERENCES model_handoffs(handoff_id))""")
    conn.commit()


def get_job(job_id: str) -> dict[str, Any] | None:
    conn = storage._connect()
    try:
        _schema(conn)
        row = conn.execute("SELECT * FROM agent_jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None: return None
        value = dict(row)
        value["result"] = json.loads(value.pop("result_json"))
        return value
    finally:
        conn.close()


def claim_job(*, agent_id: str, session_id: str, handoff_id: str, contract_hash: str,
              actor: str, transport: str, model: str, ttl_seconds: int = 600) -> tuple[dict[str, Any], bool]:
    conn = storage._connect()
    try:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT job_id FROM agent_jobs WHERE handoff_id=?", (handoff_id,)).fetchone()
        if row:
            conn.commit()
            return get_job(row["job_id"]), False
        handoff = conn.execute("SELECT * FROM model_handoffs WHERE handoff_id=?", (handoff_id,)).fetchone()
        if handoff is None or handoff["session_id"] != session_id or handoff["status"] != "PENDING":
            raise AgentConflict("handoff is not pending in this session")
        if handoff["contract_hash"] != contract_hash:
            raise PermissionError("frozen contract hash mismatch")
        job_id = "aj_" + stable_hash({"agent_id": agent_id, "handoff_id": handoff_id, "contract_hash": contract_hash})[:32]
        deadline = int(time.time()) + ttl_seconds
        conn.execute("INSERT INTO agent_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                     (job_id, agent_id, session_id, handoff_id, contract_hash, actor, transport, model,
                      "DISPATCHING", deadline, "{}", storage._now()))
        # Exact-once local claim, no promise of exact-once external execution.
        storage._append_ledger_tx(conn, {"event_type": "AGENT_DISPATCH_CLAIMED", "job_id": job_id,
            "agent_id": agent_id, "handoff_id": handoff_id, "actor": actor,
            "requested_model": model, "transport": transport}, "PASS")
        conn.commit()
        return get_job(job_id), True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_job(job_id: str, status: str, result: dict[str, Any]) -> dict[str, Any]:
    if status not in {"RUNNING", "WAITING_EXTERNAL", "WAITING", "PARTIAL", "UNKNOWN", "UNCERTAIN", "FAILED"}:
        raise ValueError("invalid agent job transition")
    conn = storage._connect()
    try:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute("SELECT status FROM agent_jobs WHERE job_id=?", (job_id,)).fetchone()
        if current is None: raise LookupError("agent job not found")
        if current["status"] == "COMPLETE": raise AgentConflict("completed job is immutable")
        conn.execute("UPDATE agent_jobs SET status=?,result_json=? WHERE job_id=?",
                     (status, storage._canonical_json(result), job_id))
        storage._append_ledger_tx(conn, {"event_type": "AGENT_DISPATCH_STATE", "job_id": job_id,
            "status": status, "result_hash": stable_hash(result)}, "HOLD" if status in {"UNKNOWN","UNCERTAIN","FAILED","PARTIAL"} else "PASS")
        conn.commit()
        return get_job(job_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def finish_job(job_id: str, payload: dict[str, Any], provenance: str) -> dict[str, Any]:
    assert_transferable_state(payload)
    if not isinstance(payload.get("public_summary"), str) or not payload["public_summary"].strip():
        raise ValueError("agent reply requires a nonempty public_summary")
    conn = storage._connect()
    try:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        job = conn.execute("SELECT * FROM agent_jobs WHERE job_id=?", (job_id,)).fetchone()
        if job is None: raise LookupError("agent job not found")
        if job["status"] == "COMPLETE":
            previous = json.loads(job["result_json"])
            if previous["response_hash"] != stable_hash(payload):
                raise AgentConflict("completed reply cannot be replaced")
            conn.commit()
            return get_job(job_id)
        if int(time.time()) > job["deadline"]: raise AgentConflict("agent job deadline expired")
        source = conn.execute("SELECT * FROM model_handoffs WHERE handoff_id=?", (job["handoff_id"],)).fetchone()
        session = conn.execute("SELECT * FROM model_sessions WHERE session_id=?", (job["session_id"],)).fetchone()
        if source["status"] != "PENDING": raise AgentConflict("source handoff was already acknowledged")
        if session["contract_hash"] != job["contract_hash"]: raise PermissionError("frozen contract hash mismatch")
        participants = {p["participant_id"] for p in json.loads(session["participants_json"])}
        if source["to_participant"] not in participants or source["from_participant"] not in participants:
            raise PermissionError("reply participants are no longer enrolled")
        sequence = conn.execute("SELECT COALESCE(MAX(session_seq),0)+1 n FROM model_handoffs WHERE session_id=?",
                                (job["session_id"],)).fetchone()["n"]
        digest = build_handoff_digest(session_id=job["session_id"], sequence=sequence,
            from_participant=source["to_participant"], to_participant=source["from_participant"],
            parent_handoff_id=source["handoff_id"], payload=payload, frozen_contract_hash=job["contract_hash"])
        reply_id, now = "mh_" + digest[:32], storage._now()
        conn.execute("""INSERT INTO model_handoffs(
            handoff_id,session_id,session_seq,from_participant,to_participant,parent_handoff_id,
            payload_json,payload_hash,contract_hash,status,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,'PENDING',?)""",
            (reply_id,job["session_id"],sequence,source["to_participant"],source["from_participant"],
             source["handoff_id"],storage._canonical_json(payload),digest,job["contract_hash"],now))
        conn.execute("UPDATE model_handoffs SET status='ACKED',acked_at=? WHERE handoff_id=?", (now,source["handoff_id"]))
        result = {"reply_handoff_id": reply_id, "response_hash": stable_hash(payload),
                  "provenance": provenance, "completed_at": now}
        conn.execute("UPDATE agent_jobs SET status='COMPLETE',result_json=? WHERE job_id=?",
                     (storage._canonical_json(result),job_id))
        storage._append_ledger_tx(conn, {"event_type":"AGENT_REPLY_COMMITTED","job_id":job_id,
            "source_handoff_id":source["handoff_id"],"reply_handoff_id":reply_id,
            "response_hash":stable_hash(payload),"provenance":provenance,"actor":job["actor"]}, "PASS")
        conn.commit()
        return get_job(job_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
