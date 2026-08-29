from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class StateStoreConfigurationError(RuntimeError):
    pass


class TransactionalStateStore(Protocol):
    backend_name: str
    transaction_model: str
    persistence_scope: str

    def connect(self) -> sqlite3.Connection: ...


@dataclass(frozen=True)
class SQLiteStateStore:
    path: Path
    backend_name: str = "sqlite"
    transaction_model: str = "SQLITE_BEGIN_IMMEDIATE"
    persistence_scope: str = "HOST_FILESYSTEM_BOUND"

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30.0)
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


def resolve_transactional_state_store(path: Path) -> TransactionalStateStore:
    backend = os.environ.get("MATVERSE_STATE_BACKEND", "sqlite").strip().lower()
    if backend == "sqlite":
        return SQLiteStateStore(Path(path))
    raise StateStoreConfigurationError(
        f"unsupported MATVERSE_STATE_BACKEND={backend!r}; fail-closed until an atomic compatible backend is implemented"
    )


def describe_state_store(path: Path) -> dict[str, str]:
    store = resolve_transactional_state_store(path)
    return {
        "backend": store.backend_name,
        "transaction_model": store.transaction_model,
        "persistence_scope": store.persistence_scope,
    }
