from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    attribution_basis TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS documents (
    document_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    title TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(project_id),
    project_name TEXT NOT NULL,
    attribution_basis TEXT NOT NULL,
    created_at_epoch REAL,
    updated_at_epoch REAL,
    source_file TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    body TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    ingested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_documents_project ON documents(project_id);
CREATE INDEX IF NOT EXISTS idx_documents_updated ON documents(updated_at_epoch DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
    document_id UNINDEXED,
    title,
    project_name,
    body,
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    run_id TEXT PRIMARY KEY,
    source_name TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    imported_documents INTEGER NOT NULL,
    assigned_documents INTEGER NOT NULL,
    unassigned_documents INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    subject TEXT NOT NULL,
    action TEXT NOT NULL,
    resource_id TEXT,
    request_id TEXT,
    details_json TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            connection.execute(
                "INSERT OR IGNORE INTO projects(project_id, display_name, attribution_basis) VALUES (?, ?, ?)",
                ("unassigned", "UNASSIGNED", "no_explicit_project_metadata"),
            )
            connection.commit()

    def upsert_project(self, project_id: str, display_name: str, basis: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO projects(project_id, display_name, attribution_basis)
                VALUES (?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    display_name=excluded.display_name,
                    attribution_basis=excluded.attribution_basis,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (project_id, display_name, basis),
            )
            connection.commit()

    def upsert_document(self, document: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO documents(
                    document_id, conversation_id, title, project_id, project_name,
                    attribution_basis, created_at_epoch, updated_at_epoch, source_file,
                    source_hash, body, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(document_id) DO UPDATE SET
                    conversation_id=excluded.conversation_id,
                    title=excluded.title,
                    project_id=excluded.project_id,
                    project_name=excluded.project_name,
                    attribution_basis=excluded.attribution_basis,
                    created_at_epoch=excluded.created_at_epoch,
                    updated_at_epoch=excluded.updated_at_epoch,
                    source_file=excluded.source_file,
                    source_hash=excluded.source_hash,
                    body=excluded.body,
                    metadata_json=excluded.metadata_json,
                    ingested_at=CURRENT_TIMESTAMP
                """,
                (
                    document["document_id"],
                    document["conversation_id"],
                    document["title"],
                    document["project_id"],
                    document["project_name"],
                    document["attribution_basis"],
                    document["created_at_epoch"],
                    document["updated_at_epoch"],
                    document["source_file"],
                    document["source_hash"],
                    document["body"],
                    json.dumps(document["metadata"], ensure_ascii=False, sort_keys=True),
                ),
            )
            connection.execute("DELETE FROM documents_fts WHERE document_id = ?", (document["document_id"],))
            connection.execute(
                "INSERT INTO documents_fts(document_id, title, project_name, body) VALUES (?, ?, ?, ?)",
                (
                    document["document_id"],
                    document["title"],
                    document["project_name"],
                    document["body"],
                ),
            )
            connection.commit()

    def search(self, fts_query: str, limit: int, project_id: str | None = None) -> list[sqlite3.Row]:
        sql = """
            SELECT d.document_id, d.title, d.project_id, d.project_name,
                   d.updated_at_epoch, d.attribution_basis,
                   snippet(documents_fts, 3, '[', ']', ' … ', 30) AS excerpt,
                   bm25(documents_fts, 8.0, 3.0, 1.0) AS score
            FROM documents_fts
            JOIN documents d ON d.document_id = documents_fts.document_id
            WHERE documents_fts MATCH ?
        """
        params: list[Any] = [fts_query]
        if project_id:
            sql += " AND d.project_id = ?"
            params.append(project_id)
        sql += " ORDER BY score ASC, d.updated_at_epoch DESC LIMIT ?"
        params.append(limit)
        with self.connect() as connection:
            return list(connection.execute(sql, params).fetchall())

    def fetch(self, document_id: str) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM documents WHERE document_id = ?", (document_id,)
            ).fetchone()

    def list_projects(self) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    """
                    SELECT p.project_id, p.display_name, p.attribution_basis,
                           COUNT(d.document_id) AS document_count,
                           MAX(d.updated_at_epoch) AS last_document_update
                    FROM projects p
                    LEFT JOIN documents d ON d.project_id = p.project_id
                    GROUP BY p.project_id, p.display_name, p.attribution_basis
                    ORDER BY CASE WHEN p.project_id='unassigned' THEN 1 ELSE 0 END,
                             p.display_name COLLATE NOCASE
                    """
                ).fetchall()
            )

    def stats(self) -> dict[str, int]:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT
                  (SELECT COUNT(*) FROM projects WHERE project_id != 'unassigned') AS projects,
                  (SELECT COUNT(*) FROM documents) AS documents,
                  (SELECT COUNT(*) FROM documents
                     WHERE COALESCE(json_extract(metadata_json, '$.source_type'), 'conversation') != 'file') AS conversations,
                  (SELECT COALESCE(SUM(CAST(COALESCE(json_extract(metadata_json, '$.message_count'), 0) AS INTEGER)), 0)
                     FROM documents
                     WHERE COALESCE(json_extract(metadata_json, '$.source_type'), 'conversation') != 'file') AS messages,
                  (SELECT COUNT(*) FROM documents
                     WHERE json_extract(metadata_json, '$.source_type') = 'file') AS files,
                  (SELECT COUNT(*) FROM documents WHERE project_id = 'unassigned') AS unassigned,
                  (SELECT COUNT(*) FROM ingestion_runs) AS ingestion_runs
                """
            ).fetchone()
            return dict(row) if row else {}

    def list_ingestions(self, limit: int = 100) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(connection.execute(
                """
                SELECT run_id, source_name, source_hash, imported_documents,
                       assigned_documents, unassigned_documents, started_at,
                       completed_at, metadata_json
                FROM ingestion_runs
                ORDER BY completed_at DESC
                LIMIT ?
                """,
                (max(1, min(500, int(limit))),),
            ).fetchall())

    def list_unassigned(self, limit: int = 200) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(connection.execute(
                """
                SELECT document_id, title, project_id, project_name,
                       attribution_basis, updated_at_epoch, source_file,
                       metadata_json
                FROM documents
                WHERE project_id = 'unassigned'
                ORDER BY updated_at_epoch DESC
                LIMIT ?
                """,
                (max(1, min(1000, int(limit))),),
            ).fetchall())

    def assign_document(self, document_id: str, project_id: str, subject: str) -> None:
        with self.connect() as connection:
            project = connection.execute(
                "SELECT project_id, display_name FROM projects WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            if project is None:
                raise KeyError(project_id)
            document = connection.execute(
                "SELECT title, body FROM documents WHERE document_id = ?",
                (document_id,),
            ).fetchone()
            if document is None:
                raise KeyError(document_id)
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE documents
                SET project_id = ?, project_name = ?, attribution_basis = ?,
                    ingested_at = CURRENT_TIMESTAMP
                WHERE document_id = ?
                """,
                (project_id, project['display_name'], 'owner_manual_assignment', document_id),
            )
            connection.execute("DELETE FROM documents_fts WHERE document_id = ?", (document_id,))
            connection.execute(
                "INSERT INTO documents_fts(document_id, title, project_name, body) VALUES (?, ?, ?, ?)",
                (document_id, document['title'], project['display_name'], document['body']),
            )
            connection.execute(
                "INSERT INTO audit_log(subject, action, resource_id, request_id, details_json) VALUES (?, ?, ?, ?, ?)",
                (subject, 'assign_document', document_id, None,
                 json.dumps({'project_id': project_id}, ensure_ascii=False, sort_keys=True)),
            )
            connection.commit()

    def record_ingestion(self, run: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO ingestion_runs(
                    run_id, source_name, source_hash, imported_documents,
                    assigned_documents, unassigned_documents, started_at,
                    completed_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run["run_id"], run["source_name"], run["source_hash"],
                    run["imported_documents"], run["assigned_documents"],
                    run["unassigned_documents"], run["started_at"],
                    run["completed_at"], json.dumps(run["metadata"], ensure_ascii=False, sort_keys=True),
                ),
            )
            connection.commit()

    def audit(self, subject: str, action: str, resource_id: str | None, request_id: str | None, details: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO audit_log(subject, action, resource_id, request_id, details_json) VALUES (?, ?, ?, ?, ?)",
                (subject, action, resource_id, request_id, json.dumps(details, ensure_ascii=False, sort_keys=True)),
            )
            connection.commit()
