from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

SCHEMA = "matverse.no-left-behind.v1"


class CoverageError(RuntimeError):
    pass


class CoverageLifecycle(str, Enum):
    DISCOVERED = "DISCOVERED"
    INDEXED = "INDEXED"
    CLASSIFIED = "CLASSIFIED"
    RELATED = "RELATED"
    ADJUDICATED = "ADJUDICATED"


class CoverageVerdict(str, Enum):
    PENDING = "PENDING"
    CANONICAL = "CANONICAL"
    SUPERSEDED = "SUPERSEDED"
    UNRESOLVED = "UNRESOLVED"
    ARCHIVED = "ARCHIVED"


class PresenceStatus(str, Enum):
    PRESENT = "PRESENT"
    MISSING = "MISSING"


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
    delta_sources: int
    external_refs: tuple[str, ...]
    zero_streak: int
    complete_scope: bool
    status: str

    @property
    def discovery_saturated(self) -> bool:
        return self.status == "DISCOVERY_SATURATED"


class CoverageRegistry:
    """Durable No-Left-Behind inventory with orthogonal state axes.

    Object identity is stable across revisions:
    ``(partition_id, source_uri, artifact_type)``.

    Lifecycle, verdict, presence, membership, lineage, semantic state,
    implementation state and evidence state are deliberately separate.
    ``orphan`` is derived from the canonical relation graph and is never stored
    as a lifecycle/verdict value.

    Discovery saturation is scoped and closed-under-reference: only complete
    sweeps over the same partition/source scope count, and a zero sweep is clean
    only when it has no new/missing objects, no new sources and no unresolved
    external references.
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
                  lifecycle TEXT NOT NULL,
                  verdict TEXT NOT NULL,
                  presence_status TEXT NOT NULL,
                  content_hash TEXT NOT NULL,
                  metadata_json TEXT NOT NULL,
                  membership_status TEXT NOT NULL,
                  lineage_status TEXT NOT NULL,
                  semantic_status TEXT NOT NULL,
                  implementation_status TEXT NOT NULL,
                  evidence_status TEXT NOT NULL,
                  justification_hash TEXT,
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

                CREATE TABLE IF NOT EXISTS coverage_sources(
                  source_id TEXT PRIMARY KEY,
                  first_seen INTEGER NOT NULL,
                  last_seen INTEGER NOT NULL,
                  last_seen_run TEXT NOT NULL
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
                  delta_sources INTEGER NOT NULL,
                  external_refs_json TEXT NOT NULL,
                  complete_scope INTEGER NOT NULL DEFAULT 1,
                  status TEXT NOT NULL
                );
                """
            )
            conn.commit()

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
    def scope_hash(partitions: Sequence[str], source_ids: Sequence[str] = ()) -> str:
        normalized_partitions = sorted({str(item).strip() for item in partitions if str(item).strip()})
        normalized_sources = sorted({str(item).strip() for item in source_ids if str(item).strip()})
        if not normalized_partitions:
            raise ValueError("sweep scope must contain at least one partition")
        return sha256_json(
            {
                "schema": SCHEMA,
                "partitions": normalized_partitions,
                "sources": normalized_sources,
            }
        )

    def _register_source(
        self,
        conn: sqlite3.Connection,
        source_id: str,
        *,
        run_id: str,
        observed_at: int,
    ) -> bool:
        normalized = source_id.strip()
        if not normalized:
            raise CoverageError("source_id must be non-empty")
        current = conn.execute(
            "SELECT 1 FROM coverage_sources WHERE source_id = ?",
            (normalized,),
        ).fetchone()
        created = current is None
        if created:
            conn.execute(
                """
                INSERT INTO coverage_sources(source_id, first_seen, last_seen, last_seen_run)
                VALUES(?, ?, ?, ?)
                """,
                (normalized, observed_at, observed_at, run_id),
            )
        else:
            conn.execute(
                """
                UPDATE coverage_sources
                   SET last_seen = ?, last_seen_run = ?
                 WHERE source_id = ?
                """,
                (observed_at, run_id, normalized),
            )
        return created

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
            "SELECT content_hash FROM coverage_items WHERE item_id = ?",
            (item_id,),
        ).fetchone()
        created = current is None
        changed = current is not None and str(current["content_hash"]) != content_hash

        if created:
            conn.execute(
                """
                INSERT INTO coverage_items(
                  item_id, partition_id, source_uri, artifact_type,
                  lifecycle, verdict, presence_status,
                  content_hash, metadata_json, membership_status, lineage_status,
                  semantic_status, implementation_status, evidence_status,
                  justification_hash, successor_id,
                  discovered_at, updated_at, last_seen_run
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?)
                """,
                (
                    item_id,
                    observation.partition_id.strip(),
                    observation.source_uri.strip(),
                    observation.artifact_type.strip(),
                    CoverageLifecycle.DISCOVERED.value,
                    CoverageVerdict.PENDING.value,
                    PresenceStatus.PRESENT.value,
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
        elif changed:
            # A revised object keeps identity but loses prior adjudication/evidence.
            conn.execute(
                """
                UPDATE coverage_items
                   SET lifecycle = ?, verdict = ?, presence_status = ?,
                       content_hash = ?, metadata_json = ?,
                       semantic_status = ?, evidence_status = ?,
                       justification_hash = NULL,
                       updated_at = ?, last_seen_run = ?
                 WHERE item_id = ?
                """,
                (
                    CoverageLifecycle.DISCOVERED.value,
                    CoverageVerdict.PENDING.value,
                    PresenceStatus.PRESENT.value,
                    content_hash,
                    metadata_json,
                    "UNRESOLVED",
                    "UNVERIFIED",
                    observed_at,
                    run_id,
                    item_id,
                ),
            )
        else:
            conn.execute(
                """
                UPDATE coverage_items
                   SET presence_status = ?, content_hash = ?, metadata_json = ?,
                       updated_at = ?, last_seen_run = ?
                 WHERE item_id = ?
                """,
                (
                    PresenceStatus.PRESENT.value,
                    content_hash,
                    metadata_json,
                    observed_at,
                    run_id,
                    item_id,
                ),
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
        source_ids: Sequence[str] | None = None,
        referenced_sources: Sequence[str] = (),
        complete_scope: bool = True,
    ) -> SweepResult:
        scope_partitions = sorted({str(item).strip() for item in partitions if str(item).strip()})
        declared_sources = sorted(
            {
                str(item).strip()
                for item in (source_ids if source_ids is not None else scope_partitions)
                if str(item).strip()
            }
        )
        scope_hash = self.scope_hash(scope_partitions, declared_sources)
        started_at = time.time_ns()
        run_id = sha256_json(
            {
                "schema": SCHEMA,
                "scope_hash": scope_hash,
                "started_at": started_at,
                "nonce": secrets.token_hex(8),
            }
        )[:24]

        observed = 0
        new_items = 0
        changed_items = 0
        delta_sources = 0
        seen_item_ids: set[str] = set()

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")

            for source_id in declared_sources:
                delta_sources += int(
                    self._register_source(
                        conn,
                        source_id,
                        run_id=run_id,
                        observed_at=started_at,
                    )
                )

            for observation in observations:
                if observation.partition_id not in scope_partitions:
                    raise CoverageError(
                        f"observation partition {observation.partition_id!r} is outside declared sweep scope"
                    )
                stable_id = self.item_id(
                    observation.partition_id,
                    observation.source_uri,
                    observation.artifact_type,
                )
                if stable_id in seen_item_ids:
                    raise CoverageError(f"duplicate coverage identity in one sweep: {stable_id}")
                seen_item_ids.add(stable_id)
                observed += 1
                _, created, changed = self._register_observation(
                    conn,
                    observation,
                    run_id=run_id,
                    observed_at=started_at,
                )
                new_items += int(created)
                changed_items += int(changed)

            known_sources = {
                str(row["source_id"])
                for row in conn.execute("SELECT source_id FROM coverage_sources").fetchall()
            }
            external_refs = tuple(
                sorted(
                    {
                        str(ref).strip()
                        for ref in referenced_sources
                        if str(ref).strip() and str(ref).strip() not in known_sources
                    }
                )
            )

            missing_items = 0
            if complete_scope:
                placeholders = ",".join("?" for _ in scope_partitions)
                rows = conn.execute(
                    f"""
                    SELECT item_id FROM coverage_items
                    WHERE partition_id IN ({placeholders})
                      AND (last_seen_run IS NULL OR last_seen_run != ?)
                      AND presence_status != ?
                    """,
                    (*scope_partitions, run_id, PresenceStatus.MISSING.value),
                ).fetchall()
                missing_items = len(rows)
                for row in rows:
                    conn.execute(
                        """
                        UPDATE coverage_items
                           SET presence_status = ?, updated_at = ?
                         WHERE item_id = ?
                        """,
                        (PresenceStatus.MISSING.value, started_at, row["item_id"]),
                    )

            ended_at = time.time_ns()
            initial_status = "PROGRESSING" if complete_scope else "PARTIAL_SCOPE"
            conn.execute(
                """
                INSERT INTO sweep_runs(
                  run_id, scope_hash, scope_json, started_at, ended_at,
                  observed_items, new_items, changed_items, missing_items,
                  delta_sources, external_refs_json, complete_scope, status
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    scope_hash,
                    canonical_json(
                        {
                            "partitions": scope_partitions,
                            "sources": declared_sources,
                        }
                    ),
                    started_at,
                    ended_at,
                    observed,
                    new_items,
                    changed_items,
                    missing_items,
                    delta_sources,
                    canonical_json(list(external_refs)),
                    int(complete_scope),
                    initial_status,
                ),
            )
            zero_streak = self._zero_streak(conn, scope_hash) if complete_scope else 0
            status = "DISCOVERY_SATURATED" if zero_streak >= 2 else initial_status
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
            delta_sources=delta_sources,
            external_refs=external_refs,
            zero_streak=zero_streak,
            complete_scope=complete_scope,
            status=status,
        )

    @staticmethod
    def _zero_streak(conn: sqlite3.Connection, scope_hash: str) -> int:
        rows = conn.execute(
            """
            SELECT new_items, missing_items, delta_sources, external_refs_json
              FROM sweep_runs
             WHERE scope_hash = ? AND complete_scope = 1
             ORDER BY ended_at DESC
            """,
            (scope_hash,),
        ).fetchall()
        streak = 0
        for row in rows:
            external_refs = json.loads(str(row["external_refs_json"]))
            clean = (
                int(row["new_items"]) == 0
                and int(row["missing_items"]) == 0
                and int(row["delta_sources"]) == 0
                and not external_refs
            )
            if clean:
                streak += 1
            else:
                break
        return streak

    def set_status(
        self,
        item_id: str,
        *,
        lifecycle: CoverageLifecycle | None = None,
        verdict: CoverageVerdict | None = None,
        presence_status: PresenceStatus | None = None,
        membership_status: MembershipStatus | None = None,
        lineage_status: LineageStatus | None = None,
        semantic_status: str | None = None,
        implementation_status: str | None = None,
        evidence_status: str | None = None,
        justification_hash: str | None = None,
        successor_id: str | None = None,
    ) -> None:
        assignments: list[str] = []
        values: list[Any] = []
        for column, value in (
            ("lifecycle", lifecycle.value if lifecycle else None),
            ("verdict", verdict.value if verdict else None),
            ("presence_status", presence_status.value if presence_status else None),
            ("membership_status", membership_status.value if membership_status else None),
            ("lineage_status", lineage_status.value if lineage_status else None),
            ("semantic_status", semantic_status),
            ("implementation_status", implementation_status),
            ("evidence_status", evidence_status),
        ):
            if value is not None:
                assignments.append(f"{column} = ?")
                values.append(str(value))
        if justification_hash is not None:
            assignments.append("justification_hash = ?")
            values.append(justification_hash)
        if successor_id is not None:
            assignments.append("successor_id = ?")
            values.append(successor_id)
        if not assignments:
            return

        with self._connect() as conn:
            if conn.execute(
                "SELECT 1 FROM coverage_items WHERE item_id = ?",
                (item_id,),
            ).fetchone() is None:
                raise CoverageError(f"unknown coverage item: {item_id}")
            if successor_id is not None and conn.execute(
                "SELECT 1 FROM coverage_items WHERE item_id = ?",
                (successor_id,),
            ).fetchone() is None:
                raise CoverageError(f"unknown successor item: {successor_id}")
            assignments.append("updated_at = ?")
            values.append(time.time_ns())
            values.append(item_id)
            conn.execute(
                f"UPDATE coverage_items SET {', '.join(assignments)} WHERE item_id = ?",
                tuple(values),
            )
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
            for candidate in (subject_id, object_id):
                if conn.execute(
                    "SELECT 1 FROM coverage_items WHERE item_id = ?",
                    (candidate,),
                ).fetchone() is None:
                    raise CoverageError(f"unknown coverage item: {candidate}")
            relation_id = sha256_json(
                {
                    "schema": SCHEMA,
                    "subject_id": subject_id,
                    "predicate": predicate.strip(),
                    "object_id": object_id,
                    "evidence_ref": evidence_ref.strip(),
                }
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO coverage_relations(
                  relation_id, subject_id, predicate, object_id,
                  evidence_ref, status, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    relation_id,
                    subject_id,
                    predicate.strip(),
                    object_id,
                    evidence_ref.strip(),
                    status.strip() or "ASSERTED",
                    time.time_ns(),
                ),
            )
            conn.execute(
                """
                UPDATE coverage_items
                   SET lifecycle = CASE
                         WHEN lifecycle IN (?, ?, ?) THEN ?
                         ELSE lifecycle
                       END,
                       updated_at = ?
                 WHERE item_id IN (?, ?)
                """,
                (
                    CoverageLifecycle.DISCOVERED.value,
                    CoverageLifecycle.INDEXED.value,
                    CoverageLifecycle.CLASSIFIED.value,
                    CoverageLifecycle.RELATED.value,
                    time.time_ns(),
                    subject_id,
                    object_id,
                ),
            )
            conn.commit()
        return relation_id

    def is_orphan(self, item_id: str) -> bool:
        """Derived graph property: no non-refuted edge to another canonical item."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM coverage_items WHERE item_id = ?",
                (item_id,),
            ).fetchone()
            if row is None:
                raise CoverageError(f"unknown coverage item: {item_id}")
            linked = conn.execute(
                """
                SELECT 1
                  FROM coverage_relations r
                  JOIN coverage_items other
                    ON other.item_id = CASE
                         WHEN r.subject_id = ? THEN r.object_id
                         ELSE r.subject_id
                       END
                 WHERE (r.subject_id = ? OR r.object_id = ?)
                   AND r.status NOT IN ('REFUTED', 'RETRACTED')
                   AND other.verdict = ?
                 LIMIT 1
                """,
                (
                    item_id,
                    item_id,
                    item_id,
                    CoverageVerdict.CANONICAL.value,
                ),
            ).fetchone()
            return linked is None

    def violations(self, *, partitions: Sequence[str] | None = None) -> list[str]:
        clauses = ""
        params: list[Any] = []
        if partitions:
            normalized = sorted({str(item).strip() for item in partitions if str(item).strip()})
            placeholders = ",".join("?" for _ in normalized)
            clauses = f" WHERE partition_id IN ({placeholders})"
            params.extend(normalized)

        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT item_id, verdict, presence_status, justification_hash, successor_id
                  FROM coverage_items
                  {clauses}
                 ORDER BY item_id
                """,
                tuple(params),
            ).fetchall()

        bad: list[str] = []
        for row in rows:
            item_id = str(row["item_id"])
            verdict = str(row["verdict"])
            presence = str(row["presence_status"])
            justification = row["justification_hash"]
            successor = row["successor_id"]

            if verdict == CoverageVerdict.PENDING.value:
                bad.append(f"{item_id}: verdict PENDING")
                continue

            orphan = self.is_orphan(item_id)
            if orphan:
                if verdict not in {
                    CoverageVerdict.UNRESOLVED.value,
                    CoverageVerdict.ARCHIVED.value,
                }:
                    bad.append(f"{item_id}: orphan with verdict {verdict}")
                elif not justification:
                    bad.append(f"{item_id}: orphan without hashed justification")

            if verdict == CoverageVerdict.SUPERSEDED.value and not successor:
                bad.append(f"{item_id}: SUPERSEDED without successor")

            if presence == PresenceStatus.MISSING.value:
                if verdict not in {
                    CoverageVerdict.UNRESOLVED.value,
                    CoverageVerdict.ARCHIVED.value,
                    CoverageVerdict.SUPERSEDED.value,
                }:
                    bad.append(f"{item_id}: missing from declared complete scope")
                elif not justification and verdict != CoverageVerdict.SUPERSEDED.value:
                    bad.append(f"{item_id}: missing item without hashed justification")

        return bad

    def get_item(self, item_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM coverage_items WHERE item_id = ?",
                (item_id,),
            ).fetchone()
            if row is None:
                raise CoverageError(f"unknown coverage item: {item_id}")
            item = dict(row)
        item["orphan"] = self.is_orphan(item_id)
        return item

    def list_items(self, *, partitions: Sequence[str] | None = None) -> list[dict[str, Any]]:
        clauses = ""
        params: list[Any] = []
        if partitions:
            normalized = sorted({str(item).strip() for item in partitions if str(item).strip()})
            placeholders = ",".join("?" for _ in normalized)
            clauses = f" WHERE partition_id IN ({placeholders})"
            params.extend(normalized)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM coverage_items{clauses} ORDER BY partition_id, source_uri, artifact_type",
                tuple(params),
            ).fetchall()
        items = [dict(row) for row in rows]
        for item in items:
            item["orphan"] = self.is_orphan(str(item["item_id"]))
        return items

    def report(
        self,
        *,
        partitions: Sequence[str],
        source_ids: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        declared_sources = list(source_ids) if source_ids is not None else list(partitions)
        scope_hash = self.scope_hash(partitions, declared_sources)
        with self._connect() as conn:
            latest = conn.execute(
                """
                SELECT *
                  FROM sweep_runs
                 WHERE scope_hash = ?
                 ORDER BY ended_at DESC
                 LIMIT 1
                """,
                (scope_hash,),
            ).fetchone()
        items = self.list_items(partitions=partitions)

        discovery_saturated = bool(
            latest is not None and str(latest["status"]) == "DISCOVERY_SATURATED"
        )
        violations = self.violations(partitions=partitions)

        unresolved_membership = sum(
            item["membership_status"] in {
                MembershipStatus.UNKNOWN.value,
                MembershipStatus.HOLD.value,
            }
            for item in items
        )
        unresolved_lineage = sum(
            item["membership_status"] == MembershipStatus.MEMBER.value
            and item["lineage_status"] == LineageStatus.UNKNOWN.value
            for item in items
        )
        unresolved_semantics = sum(
            item["membership_status"] == MembershipStatus.MEMBER.value
            and item["semantic_status"] == "UNRESOLVED"
            for item in items
        )
        missing_items = sum(
            item["presence_status"] == PresenceStatus.MISSING.value
            for item in items
        )
        orphan_items = sum(bool(item["orphan"]) for item in items)

        adjudication_complete = (
            not violations
            and unresolved_membership == 0
            and unresolved_lineage == 0
            and unresolved_semantics == 0
            and missing_items == 0
        )
        coverage_complete = discovery_saturated and adjudication_complete

        latest_external_refs = (
            tuple(json.loads(str(latest["external_refs_json"])))
            if latest is not None
            else ()
        )
        latest_delta_sources = int(latest["delta_sources"]) if latest is not None else 0

        return {
            "schema": SCHEMA,
            "scope_hash": scope_hash,
            "item_count": len(items),
            "orphan_items": orphan_items,
            "missing_items": missing_items,
            "unresolved_membership": unresolved_membership,
            "unresolved_lineage": unresolved_lineage,
            "unresolved_semantics": unresolved_semantics,
            "violations": violations,
            "latest_delta_sources": latest_delta_sources,
            "latest_external_refs": list(latest_external_refs),
            "discovery_saturated": discovery_saturated,
            "adjudication_complete": adjudication_complete,
            "coverage_complete": coverage_complete,
        }
