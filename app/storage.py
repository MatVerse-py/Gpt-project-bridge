from __future__ import annotations

import json
import os
import sqlite3
from hashlib import sha256
from pathlib import Path
from typing import Any

DB_PATH = Path(os.environ.get("MATVERSE_DB", "matverse.db"))


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ledger (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            prev_hash TEXT NOT NULL,
            event_hash TEXT NOT NULL UNIQUE,
            event_json TEXT NOT NULL,
            decision TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def append_event(event: dict[str, Any], decision: str) -> dict[str, Any]:
    conn = _connect()
    last = conn.execute("SELECT event_hash FROM ledger ORDER BY seq DESC LIMIT 1").fetchone()
    prev_hash = last["event_hash"] if last else "GENESIS"
    payload = json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    event_hash = sha256((prev_hash + payload + decision).encode("utf-8")).hexdigest()
    cur = conn.execute(
        "INSERT INTO ledger(prev_hash,event_hash,event_json,decision) VALUES (?,?,?,?)",
        (prev_hash, event_hash, payload, decision),
    )
    conn.commit()
    seq = cur.lastrowid
    conn.close()
    return {"seq": seq, "prev_hash": prev_hash, "event_hash": event_hash, "decision": decision}


def read_ledger() -> list[dict[str, Any]]:
    conn = _connect()
    rows = conn.execute("SELECT * FROM ledger ORDER BY seq").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def verify_chain() -> dict[str, Any]:
    rows = read_ledger()
    prev = "GENESIS"
    for r in rows:
        expected = sha256((prev + r["event_json"] + r["decision"]).encode("utf-8")).hexdigest()
        if r["prev_hash"] != prev or r["event_hash"] != expected:
            return {"ok": False, "failed_seq": r["seq"]}
        prev = r["event_hash"]
    return {"ok": True, "events": len(rows), "head": prev}


def replay() -> dict[str, Any]:
    rows = read_ledger()
    state: dict[str, Any] = {"accepted": 0, "blocked": 0, "held": 0, "last_event": None}
    for r in rows:
        event = json.loads(r["event_json"])
        if r["decision"] == "PASS":
            state["accepted"] += 1
            state["last_event"] = event
        elif r["decision"] == "BLOCK":
            state["blocked"] += 1
        else:
            state["held"] += 1
    return state
