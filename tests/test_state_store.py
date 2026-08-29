from pathlib import Path

import pytest

from app import storage
from app.state_store import (
    SQLiteStateStore,
    StateStoreConfigurationError,
    describe_state_store,
    resolve_transactional_state_store,
)


def test_default_backend_is_sqlite_and_filesystem_bound(tmp_path, monkeypatch):
    monkeypatch.delenv("MATVERSE_STATE_BACKEND", raising=False)
    db = tmp_path / "state.db"
    store = resolve_transactional_state_store(db)
    assert isinstance(store, SQLiteStateStore)
    assert store.path == db
    assert describe_state_store(db) == {
        "backend": "sqlite",
        "transaction_model": "SQLITE_BEGIN_IMMEDIATE",
        "persistence_scope": "HOST_FILESYSTEM_BOUND",
    }


def test_unknown_backend_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("MATVERSE_STATE_BACKEND", "durable-object")
    with pytest.raises(StateStoreConfigurationError, match="fail-closed"):
        resolve_transactional_state_store(tmp_path / "state.db")


def test_storage_db_path_injection_is_preserved(tmp_path, monkeypatch):
    monkeypatch.delenv("MATVERSE_STATE_BACKEND", raising=False)
    db = tmp_path / "injected.db"
    monkeypatch.setattr(storage, "DB_PATH", db)
    conn = storage._connect()
    try:
        row = conn.execute("PRAGMA database_list").fetchone()
        assert Path(row[2]) == db
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert {"ledger", "auth_nonces", "contract_artifacts", "model_sessions", "model_handoffs"} <= tables
    finally:
        conn.close()
