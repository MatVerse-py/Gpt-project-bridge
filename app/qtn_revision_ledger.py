"""Durable *local* revision observation ledger, never a scientific adjudicator.

Uses a caller-supplied SQLite database. GitHub Actions ephemeral disk does not
make this persistent across hosted runs; deployment must mount durable storage.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from app.qtn_source_lineage import metadata_fingerprint, pointer_identity


def record_observations(database: str | Path, source_items: list[object], *,
                        observed_at: str | None = None) -> list[dict]:
    """Transactionally classify repeated/pinned observations across invocations.

    A metadata variation is not evidence that the paper's scientific claims
    changed. Unpinned and unrecognized URLs are excluded, not fabricated.
    """
    target = str(database)
    if not target or target == ":memory:":
        raise ValueError("persistent SQLite file path required")
    timestamp = observed_at or datetime.now(timezone.utc).isoformat()
    # UTC parse guards against unsuitable unbounded timestamps
    try:
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("timezone required")
    except (TypeError, ValueError) as exc:
        raise ValueError("timezone-aware observed_at required") from exc
    if not Path(target).parent.is_dir():
        raise ValueError("ledger parent directory does not exist")
    results: list[dict] = []
    with sqlite3.connect(target, timeout=15) as db:
        db.execute("PRAGMA busy_timeout = 15000")
        db.execute("""
            CREATE TABLE IF NOT EXISTS qtn_observations (
                work_id TEXT NOT NULL,
                version_id TEXT NOT NULL,
                metadata_fingerprint TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                version_pointer TEXT NOT NULL,
                PRIMARY KEY (version_id, metadata_fingerprint)
            )
        """)
        db.execute("BEGIN IMMEDIATE")
        for source in source_items:
            info = pointer_identity(getattr(source, "url", None))
            work, version = info["work_id"], info["version_id"]
            if not work or not version:
                results.append({"source_url": getattr(source, "url", None),
                                "status": "HOLD_UNPINNED_OR_UNKNOWN_VERSION"})
                continue
            fingerprint = metadata_fingerprint(source)
            prior_work = db.execute(
                "SELECT 1 FROM qtn_observations WHERE work_id=? LIMIT 1", (work,)
            ).fetchone()
            prior_version = db.execute(
                "SELECT 1 FROM qtn_observations WHERE version_id=? LIMIT 1", (version,)
            ).fetchone()
            prior_exact = db.execute(
                "SELECT 1 FROM qtn_observations WHERE version_id=? AND metadata_fingerprint=?",
                (version, fingerprint)
            ).fetchone()
            if prior_exact:
                event = "SEEN_AGAIN"
            elif prior_version:
                event = "VERSION_METADATA_VARIATION"
            elif prior_work:
                event = "NEW_VERSION"
            else:
                event = "NEW_WORK"
            if not prior_exact:
                db.execute(
                    "INSERT INTO qtn_observations "
                    "(work_id, version_id, metadata_fingerprint, observed_at, version_pointer) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (work, version, fingerprint, timestamp, info["version_pointer"])
                )
            results.append({
                "work_id": work, "version_id": version,
                "metadata_fingerprint": fingerprint, "status": event,
                "claim_adjudication": "HOLD_MANUAL_ADJUDICATION",
            })
        db.commit()
    return results
