import sqlite3

from app import storage


def test_legacy_model_sessions_schema_is_migrated(tmp_path, monkeypatch):
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE model_sessions (
            session_id TEXT PRIMARY KEY,
            protocol_version TEXT NOT NULL,
            contract_json TEXT NOT NULL,
            contract_hash TEXT NOT NULL,
            participants_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(storage, "DB_PATH", db)

    connection = storage._connect()
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(model_sessions)").fetchall()}
        assert "created_by" in columns
        row = connection.execute("PRAGMA table_info(model_sessions)").fetchall()
        created = next(item for item in row if item[1] == "created_by")
        assert created[4] == "'legacy'"
    finally:
        connection.close()
