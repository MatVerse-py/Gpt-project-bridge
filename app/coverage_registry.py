from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

SCHEMA = "matverse.no-left-behind.v1"


class CoverageError(RuntimeError):
    pass


class CoverageState(str, Enum):
    DISCOVERED = "DISCOVERED"
    INDEXED = "INDEXED"
    CLASSIFIED = "CLASSIFIED"
    RELATED = "RELATED"
    ADJUDICATED = "ADJUDICATED"
    SUPERSEDED = "SUPERSEDED"
    CANONICAL = "CANONICAL"
    UNRESOLVED = "UNRESOLVED"
    ORPHAN = "ORPHAN"


class MembershipStatus(str, Enum):
    UNKNOWN = "UNKNOWN"
    HOLD = "HOLD"
    MEMBER = "MEMBER"
    NON_MEMBER = "NON_MEMBER"
    UNASSIGNED = "UNASSIGNED"


class LineageStatus(str, Enum):
    UNKNOWN = "UNKNOWN"
    ROOT = "ROOT"
    LINKED = "LINKED"
    SUPERSEDED = "SUPERSEDED"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_sha256(value: str, *, field: str) -> str:
    normalized = str(value).strip().lower()
    if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return normalized


@dataclass(frozen=True)
class SweepObservation:
    partition_id: str
    source_uri: str
    artifact_type: str
    content_hash: str
    metadata: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.partition_id.strip() or not self.source_uri.strip() or not self.artifact_type.strip():
            raise ValueError("partition_id, source_uri and artifact_type are required")
        _require_sha256(self.content_hash, field="content_hash")

    @classmethod
    def from_content(
        cls,
        *,
        partition_id: str,
        source_uri: str,
        artifact_type: str,
        content: bytes | str,
        metadata: Mapping[str, Any] | None = None,
    ) -> "SweepObservation":
        raw = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        return cls(
            partition_id=partition_id,
            source_uri=source_uri,
            artifact_type=artifact_type,
            content_hash=sha256_bytes(raw),
            metadata=dict(metadata or {}),
        )


@dataclass(frozen=True)
class SweepResult:
    schema: str
    run_id: str
    scope_hash: str
    observed_items: int
    new_items: int
    changed_items: int
    missing_items: int
    zero_streak: int
    status: str

    @property
    def discovery_saturated(self) -> bool:
        return self.status == "DISCOVERY_SATURATED"


class CoverageRegistry:
    """Durable inventory for exhaustive No-Left-Behind sweeps.

    Identity is stable across content revisions: `(partition_id, source_uri,
    artifact_type)`. Content revisions are tracked separately. A sweep may be
    declared discovery-saturated only after two consecutive complete sweeps over
    the same scope produce neither new nor missing items.
    """

    def __init__(self, db_path: str | Path = "coverage_registry.db") -> None:
        self.db_path = Path(db_path)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS coverage_items(
                  item_id TEXT PRIMARY KEY,
                  partition_id TEXT NOT NULL,
                  source_uri TEXT NOT NULL,
                  artifact_type TEXT NOT NULL,
                  state TEXT NOT NULL,
                  content_hash TEXT NOT NULL,
                  metadata_json TEXT NOT NULL,
                  membership_status TEXT NOT NULL,
                  lineage_status TEXT NOT NULL,
                  semantic_status TEXT NOT NULL,
                  implementation_status TEXT NOT NULL,
                  evidence_status TEXT NOT NULL,
                  successor_id TEXT,
                  discovered_at INTEGER NOT NULL,
                  updated_at INTEGER NOT NULL,
                  last_seen_run TEXT,
                  UNIQUE(partition_id, source_uri, artifact_type)
                );

                CREATE TABLE IF NOT EXISTS coverage_versions(
                  item_id TEXT NOT NULL,
                  content_hash TEXT NOT NULL,
                  observed_at INTEGER NOT NULL,
                  metadata_json TEXT NOT NULL,
                  PRIMARY KEY(item_id, content_hash),
                  FOREIGN KEY(item_id) REFERENCES coverage_items(item_id)
                );

                CREATE TABLE IF NOT EXISTS coverage_relations(
                  relation_id TEXT PRIMARY KEY,
                  subject_id TEXT NOT NULL,
                  predicate TEXT NOT NULL,
                  object_id TEXT NOT NULL,
                  evidence_ref TEXT NOT NULL,
                  status TEXT NOT NULL,
                  created_at INTEGER NOT NULL,
                  FOREIGN KEY(subject_id) REFERENCES coverage_items(item_id),
                  FOREIGN KEY(object_id) REFERENCES coverage_items(item_id)
                );

                CREATE TABLE IF NOT EXISTS sweep_runs(
                  run_id TEXT PRIMARY KEY,
                  scope_hash TEXT NOT NULL,
                  scope_json TEXT NOT NULL,
                  started_at INTEGER NOT NULL,
                  ended_at INTEGER NOT NULL,
                  observed_items INTEGER NOT NULL,
                  new_items INTEGER NOT NULL,
                  changed_items INTEGER NOT NULL,
                  missing_items INTEGER NOT NULL,
                  status TEXT NOT NULL
                );
                """
            )

    @staticmethod
    def item_id(partition_id: str, source_uri: str, artifact_type: str) -> str:
        return sha256_json(
            {
                "schema": SCHEMA,
                "partition_id": partition_id.strip(),
                "source_uri": source_uri.strip(),
                "artifact_type": artifact_type.strip(),
            }
        )

    @staticmethod
    def scope_hash(partitions: Sequence[str]) -> str:
        normalized = sorted({str(item).strip() for item in partitions if str(item).strip()})
        if not normalized:
            raise ValueError("sweep scope must contain at least one partition")
        return sha256_json({"schema": SCHEMA, "partitions": normalized})

    def _register_observation(
        self,
        conn: sqlite3.Connection,
        observation: SweepObservation,
        *,
        run_id: str,
        observed_at: int,
    ) -> tuple[str, bool, bool]:
        item_id = self.item_id(observation.partition_id, observation.source_uri, observation.artifact_type)
        content_hash = _require_sha256(observation.content_hash, field="content_hash")
        metadata_json = canonical_json(dict(observation.metadata))
        current = conn.execute(
            "SELECT content_hash, state FROM coverage_items WHERE item_id = ?",
            (item_id,),
        ).fetchone()
        created = current is None
        changed = current is not None and str(current["content_hash"]) != content_hash

        if created:
            conn.execute(
                """
                INSERT INTO coverage_items(
                  item_id, partition_id, source_uri, artifact_type, state,
                  content_hash, metadata_json, membership_status, lineage_status,
                  semantic_status, implementation_status, evidence_status,
                  successor_id, discovered_at, updated_at, last_seen_run
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
                """,
                (
                    item_id,
                    observation.partition_id.strip(),
                    observation.source_uri.strip(),
                    observation.artifact_type.strip(),
                    CoverageState.DISCOVERED.value,
                    content_hash,
                    metadata_json,
                    MembershipStatus.UNKNOWN.value,
                    LineageStatus.UNKNOWN.value,
                    "UNRESOLVED",
                    "UNRESOLVED",
                    "UNVERIFIED",
                    observed_at,
                    observed_at,
                    run_id,
                ),
            )
        else:
            restore_state = CoverageState.DISCOVERED.value if str(current["state"]) == CoverageState.ORPHAN.value else str(current["state"])
            conn.execute(
                """
                UPDATE coverage_items
                   SET state = ?, content_hash = ?, metadata_json = ?, updated_at = ?, last_seen_run = ?
                 WHERE item_id = ?
                """,
                (restore_state, content_hash, metadata_json, observed_at, run_id, item_id),
            )

        conn.execute(
            """
            INSERT OR IGNORE INTO coverage_versions(item_id, content_hash, observed_at, metadata_json)
            VALUES(?, ?, ?, ?)
            """,
            (item_id, content_hash, observed_at, metadata_json),
        )
        return item_id, created, changed

    def execute_sweep(
        self,
        observations: Iterable[SweepObservation],
        *,
        partitions: Sequence[str],
        complete_scope: bool = True,
    ) -> SweepResult:
        scope = sorted({str(item).strip() for item in partitions if str(item).strip()})
        scope_hash = self.scope_hash(scope)
        started_at = time.time_ns()
        run_id = sha256_json(
            {"schema": SCHEMA, "scope_hash": scope_hash, "started_at": started_at, "nonce": secrets.token_hex(8)}
        )[:24]
        observed = 0
        new_items = 0
        changed_items = 0

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for observation in observations:
                if observation.partition_id not in scope:
                    raise CoverageError(
                        f"observation partition {observation.partition_id!r} is outside declared sweep scope"
                    )
                observed += 1
                _, created, changed = self._register_observation(
                    conn,
                    observation,
                    run_id=run_id,
                    observed_at=started_at,
                )
                new_items += int(created)
                changed_items += int(changed)

            missing_items = 0
            if complete_scope:
                placeholders = ",".join("?" for _ in scope)
                rows = conn.execute(
                    f"""
                    SELECT item_id, state FROM coverage_items
                    WHERE partition_id IN ({placeholders})
                      AND (last_seen_run IS NULL OR last_seen_run != ?)
                      AND state != ?
                    """,
                    (*scope, run_id, CoverageState.SUPERSEDED.value),
                ).fetchall()
                missing_items = len(rows)
                for row in rows:
                    conn.execute(
                        "UPDATE coverage_items SET state = ?, updated_at = ? WHERE item_id = ?",
                        (CoverageState.ORPHAN.value, started_at, row["item_id"]),
                    )

            ended_at = time.time_ns()
            conn.execute(
                """
                INSERT INTO sweep_runs(
                  run_id, scope_hash, scope_json, started_at, ended_at,
                  observed_items, new_items, changed_items, missing_items, status
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    scope_hash,
                    canonical_json(scope),
                    started_at,
                    ended_at,
                    observed,
                    new_items,
                    changed_items,
                    missing_items,
                    "PROGRESSING",
                ),
            )
            zero_streak = self._zero_streak(conn, scope_hash)
            status = "DISCOVERY_SATURATED" if zero_streak >= 2 else "PROGRESSING"
            conn.execute("UPDATE sweep_runs SET status = ? WHERE run_id = ?", (status, run_id))
            conn.commit()

        return SweepResult(
            schema=SCHEMA,
            run_id=run_id,
            scope_hash=scope_hash,
            observed_items=observed,
            new_items=new_items,
            changed_items=changed_items,
            missing_items=missing_items,
            zero_streak=zero_streak,
            status=status,
        )

    @staticmethod
    def _zero_streak(conn: sqlite3.Connection, scope_hash: str) -> int:
        rows = conn.execute(
            """
            SELECT new_items, missing_items FROM sweep_runs
            WHERE scope_hash = ?
            ORDER BY ended_at DESC
            """,
            (scope_hash,),
        ).fetchall()
        streak = 0
        for row in rows:
            if int(row["new_items"]) == 0 and int(row["missing_items"]) == 0:
                streak += 1
            else:
                break
        return streak

    def set_status(
        self,
        item_id: str,
        *,
        state: CoverageState | None = None,
        membership_status: MembershipStatus | None = None,
        lineage_status: LineageStatus | None = None,
        semantic_status: str | None = None,
        implementation_status: str | None = None,
        evidence_status: str | None = None,
        successor_id: str | None = None,
    ) -> None:
        assignments: list[str] = []
        values: list[Any] = []
        for column, value in (
            ("state", state.value if state else None),
            ("membership_status", membership_status.value if membership_status else None),
            ("lineage_status", lineage_status.value if lineage_status else None),
            ("semantic_status", semantic_status),
            ("implementation_status", implementation_status),
            ("evidence_status", evidence_status),
        ):
            if value is not None:
                assignments.append(f"{column} = ?")
                values.append(str(value))
        if successor_id is not None:
            assignments.append("successor_id = ?")
            values.append(successor_id)
        if not assignments:
            return
        with self._connect() as conn:
            if conn.execute("SELECT 1 FROM coverage_items WHERE item_id = ?", (item_id,)).fetchone() is None:
                raise CoverageError(f"unknown coverage item: {item_id}")
            if successor_id is not None and conn.execute(
                "SELECT 1 FROM coverage_items WHERE item_id = ?", (successor_id,)
            ).fetchone() is None:
                raise CoverageError(f"unknown successor item: {successor_id}")
            assignments.append("updated_at = ?")
            values.append(time.time_ns())
            values.append(item_id)
            conn.execute(f"UPDATE coverage_items SET {', '.join(assignments)} WHERE item_id = ?", tuple(values))
            conn.commit()

    def record_relation(
        self,
        *,
        subject_id: str,
        predicate: str,
        object_id: str,
        evidence_ref: str,
        status: str = "ASSERTED",
    ) -> str:
        if not predicate.strip() or not evidence_ref.strip():
            raise CoverageError("relations require a predicate and independent evidence_ref")
        with self._connect() as conn:
            for item_id in (subject_id, object_id):
                if conn.execute("SELECT 1 FROM coverage_items WHERE item_id = ?", (item_id,)).fetchone() is None:
                    raise CoverageError(f"relation endpoint is unknown: {item_id}")
            core = {
                "schema": SCHEMA,
                "subject_id": subject_id,
                "predicate": predicate.strip(),
                "object_id": object_id,
                "evidence_ref": evidence_ref.strip(),
                "status": status.strip(),
            }
            relation_id = sha256_json(core)
            conn.execute(
                """
                INSERT OR IGNORE INTO coverage_relations(
                  relation_id, subject_id, predicate, object_id, evidence_ref, status, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    relation_id,
                    subject_id,
                    predicate.strip(),
                    object_id,
                    evidence_ref.strip(),
                    status.strip(),
                    time.time_ns(),
                ),
            )
            conn.commit()
        return relation_id

    def get_item(self, item_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM coverage_items WHERE item_id = ?", (item_id,)).fetchone()
            if row is None:
                raise CoverageError(f"unknown coverage item: {item_id}")
            result = dict(row)
            result["metadata"] = json.loads(result.pop("metadata_json"))
            return result

    def report(self, *, partitions: Sequence[str]) -> dict[str, Any]:
        scope = sorted({str(item).strip() for item in partitions if str(item).strip()})
        scope_hash = self.scope_hash(scope)
        placeholders = ",".join("?" for _ in scope)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT state, membership_status FROM coverage_items WHERE partition_id IN ({placeholders})",
                tuple(scope),
            ).fetchall()
            counts: dict[str, int] = {}
            orphan_count = 0
            unresolved_membership = 0
            for row in rows:
                state = str(row["state"])
                counts[state] = counts.get(state, 0) + 1
                orphan_count += int(state == CoverageState.ORPHAN.value)
                unresolved_membership += int(
                    str(row["membership_status"]) in {MembershipStatus.UNKNOWN.value, MembershipStatus.HOLD.value}
                )
            zero_streak = self._zero_streak(conn, scope_hash)
            discovery_saturated = zero_streak >= 2
            complete = discovery_saturated and orphan_count == 0 and unresolved_membership == 0
            return {
                "schema": SCHEMA,
                "scope": scope,
                "scope_hash": scope_hash,
                "total_items": len(rows),
                "states": counts,
                "orphan_count": orphan_count,
                "unresolved_membership_count": unresolved_membership,
                "zero_streak": zero_streak,
                "discovery_saturated": discovery_saturated,
                "coverage_complete": complete,
            }
