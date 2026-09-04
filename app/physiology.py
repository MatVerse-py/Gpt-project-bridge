from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import threading
import time
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from .core import Decision, stable_hash
from .evidence import canonical_json, evidence_receipt
from .organism_loop import GovernedOrganism

SCHEMA_VERSION = "matverse.physiology.v1"


class HealthState(str, Enum):
    NORMAL = "NORMAL"
    DEGRADED = "DEGRADED"
    CRITICAL = "CRITICAL"
    RECOVERING = "RECOVERING"
    SAFE_MODE = "SAFE_MODE"


class RecoveryAction(str, Enum):
    NONE = "NONE"
    THROTTLE = "THROTTLE"
    ENTER_SAFE_MODE = "ENTER_SAFE_MODE"
    EXIT_SAFE_MODE = "EXIT_SAFE_MODE"


@dataclass(frozen=True)
class JournalEvent:
    seq: int
    event_id: str
    topic: str
    event_type: str
    payload: Mapping[str, Any]
    created_ns: int
    causation_id: str | None
    correlation_id: str | None
    receipt_hash: str


@dataclass(frozen=True)
class TelemetrySample:
    monotonic_ns: int
    process_time_ns: int
    disk_total_bytes: int
    disk_used_bytes: int
    disk_free_bytes: int
    rss_bytes: int | None
    load_1m: float | None
    instruments: Mapping[str, str]

    @property
    def disk_free_ratio(self) -> float:
        if self.disk_total_bytes <= 0:
            return 0.0
        return self.disk_free_bytes / self.disk_total_bytes


@dataclass(frozen=True)
class HomeostaticPolicy:
    min_disk_free_ratio: float = 0.05
    max_rss_bytes: int | None = None
    degraded_disk_free_ratio: float = 0.10
    normal_streak_to_exit_safe_mode: int = 3

    def __post_init__(self) -> None:
        if not 0.0 <= self.min_disk_free_ratio < self.degraded_disk_free_ratio <= 1.0:
            raise ValueError("disk thresholds must satisfy 0 <= critical < degraded <= 1")
        if self.max_rss_bytes is not None and self.max_rss_bytes <= 0:
            raise ValueError("max_rss_bytes must be positive when configured")
        if self.normal_streak_to_exit_safe_mode < 1:
            raise ValueError("normal_streak_to_exit_safe_mode must be >= 1")


@dataclass(frozen=True)
class HealthAssessment:
    state: HealthState
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ExecutionResult:
    status: str
    effect: Mapping[str, Any]


@dataclass(frozen=True)
class CycleResult:
    cycle_id: str
    cycle_seq: int
    health: HealthState
    recovery_action: RecoveryAction
    decision: Decision | None
    executed: bool
    state_root: str
    receipt_hash: str


class Executor(Protocol):
    def __call__(self, proposal: Mapping[str, Any]) -> ExecutionResult: ...


class DurableEventJournal:
    """SQLite/WAL append-only event spine with idempotent event ids and durable consumer offsets.

    It deliberately guarantees local durability and at-least-once consumption, not distributed
    exactly-once semantics. Side effects remain governed by the caller and must be idempotent.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), timeout=5.0, isolation_level=None, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=FULL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._create_schema()

    def _create_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                topic TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_ns INTEGER NOT NULL,
                causation_id TEXT,
                correlation_id TEXT,
                receipt_hash TEXT NOT NULL UNIQUE
            );
            CREATE INDEX IF NOT EXISTS idx_events_topic_seq ON events(topic, seq);
            CREATE TABLE IF NOT EXISTS consumer_offsets (
                consumer_id TEXT PRIMARY KEY,
                last_seq INTEGER NOT NULL CHECK(last_seq >= 0),
                updated_ns INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS runtime_state (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_ns INTEGER NOT NULL
            );
            """
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "DurableEventJournal":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    @staticmethod
    def _validate_label(value: str, name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")
        return value.strip()

    def append(
        self,
        *,
        event_id: str,
        topic: str,
        event_type: str,
        payload: Mapping[str, Any],
        causation_id: str | None = None,
        correlation_id: str | None = None,
        created_ns: int | None = None,
    ) -> JournalEvent:
        event_id = self._validate_label(event_id, "event_id")
        topic = self._validate_label(topic, "topic")
        event_type = self._validate_label(event_type, "event_type")
        payload_copy = json.loads(canonical_json(dict(payload)))
        created = time.time_ns() if created_ns is None else int(created_ns)
        if created < 0:
            raise ValueError("created_ns must be non-negative")
        core = {
            "schema": SCHEMA_VERSION,
            "event_id": event_id,
            "topic": topic,
            "event_type": event_type,
            "payload": payload_copy,
            "created_ns": created,
            "causation_id": causation_id,
            "correlation_id": correlation_id,
        }
        receipt = evidence_receipt("PHYSIOLOGY_EVENT", core, {"accepted": True})
        payload_json = canonical_json(payload_copy)
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    """
                    INSERT INTO events(event_id, topic, event_type, payload_json, created_ns, causation_id, correlation_id, receipt_hash)
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (event_id, topic, event_type, payload_json, created, causation_id, correlation_id, receipt["receipt_hash"]),
                )
                row = self._conn.execute("SELECT * FROM events WHERE event_id = ?", (event_id,)).fetchone()
                self._conn.execute("COMMIT")
            except sqlite3.IntegrityError:
                self._conn.execute("ROLLBACK")
                row = self._conn.execute("SELECT * FROM events WHERE event_id = ?", (event_id,)).fetchone()
                if row is None:
                    raise
                existing_payload = json.loads(row["payload_json"])
                if (
                    row["topic"] != topic
                    or row["event_type"] != event_type
                    or existing_payload != payload_copy
                    or row["causation_id"] != causation_id
                    or row["correlation_id"] != correlation_id
                ):
                    raise ValueError("event_id collision with different event content")
            if row is None:
                raise RuntimeError("journal append failed without a row")
            return self._row_to_event(row)

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> JournalEvent:
        return JournalEvent(
            seq=int(row["seq"]),
            event_id=str(row["event_id"]),
            topic=str(row["topic"]),
            event_type=str(row["event_type"]),
            payload=json.loads(row["payload_json"]),
            created_ns=int(row["created_ns"]),
            causation_id=row["causation_id"],
            correlation_id=row["correlation_id"],
            receipt_hash=str(row["receipt_hash"]),
        )

    def read(self, *, after_seq: int = 0, limit: int = 100, topic: str | None = None) -> tuple[JournalEvent, ...]:
        if after_seq < 0:
            raise ValueError("after_seq must be >= 0")
        if limit < 1 or limit > 10_000:
            raise ValueError("limit must be between 1 and 10000")
        with self._lock:
            if topic is None:
                rows = self._conn.execute(
                    "SELECT * FROM events WHERE seq > ? ORDER BY seq ASC LIMIT ?", (after_seq, limit)
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM events WHERE seq > ? AND topic = ? ORDER BY seq ASC LIMIT ?",
                    (after_seq, topic, limit),
                ).fetchall()
        return tuple(self._row_to_event(row) for row in rows)

    def consumer_offset(self, consumer_id: str) -> int:
        consumer_id = self._validate_label(consumer_id, "consumer_id")
        with self._lock:
            row = self._conn.execute("SELECT last_seq FROM consumer_offsets WHERE consumer_id = ?", (consumer_id,)).fetchone()
        return 0 if row is None else int(row["last_seq"])

    def consume(self, consumer_id: str, *, limit: int = 100, topic: str | None = None) -> tuple[JournalEvent, ...]:
        return self.read(after_seq=self.consumer_offset(consumer_id), limit=limit, topic=topic)

    def ack(self, consumer_id: str, seq: int) -> None:
        consumer_id = self._validate_label(consumer_id, "consumer_id")
        if seq < 0:
            raise ValueError("seq must be >= 0")
        with self._lock:
            max_row = self._conn.execute("SELECT COALESCE(MAX(seq), 0) AS max_seq FROM events").fetchone()
            max_seq = int(max_row["max_seq"])
            if seq > max_seq:
                raise ValueError("cannot ack a sequence that does not exist")
            current = self.consumer_offset(consumer_id)
            if seq < current:
                raise ValueError("consumer offsets are monotonic")
            self._conn.execute(
                """
                INSERT INTO consumer_offsets(consumer_id, last_seq, updated_ns) VALUES(?, ?, ?)
                ON CONFLICT(consumer_id) DO UPDATE SET last_seq=excluded.last_seq, updated_ns=excluded.updated_ns
                """,
                (consumer_id, seq, time.time_ns()),
            )

    def set_state(self, key: str, value: Any) -> None:
        key = self._validate_label(key, "key")
        value_json = canonical_json(value)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO runtime_state(key, value_json, updated_ns) VALUES(?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_ns=excluded.updated_ns
                """,
                (key, value_json, time.time_ns()),
            )

    def get_state(self, key: str, default: Any = None) -> Any:
        key = self._validate_label(key, "key")
        with self._lock:
            row = self._conn.execute("SELECT value_json FROM runtime_state WHERE key = ?", (key,)).fetchone()
        return default if row is None else json.loads(row["value_json"])

    def integrity_check(self) -> bool:
        with self._lock:
            row = self._conn.execute("PRAGMA integrity_check").fetchone()
        return bool(row and str(row[0]).lower() == "ok")


class NativeTelemetry:
    """Dependency-free instruments. Missing platform metrics are reported as unavailable, never fabricated."""

    def __init__(self, *, disk_path: str | os.PathLike[str] = ".") -> None:
        self.disk_path = Path(disk_path).resolve()

    @staticmethod
    def _rss_bytes() -> tuple[int | None, str]:
        try:
            import resource  # POSIX stdlib
        except ImportError:
            return None, "unavailable"
        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if sys.platform == "darwin":
            return int(usage), "resource.getrusage.ru_maxrss(bytes)"
        return int(usage) * 1024, "resource.getrusage.ru_maxrss(kib)"

    @staticmethod
    def _load_1m() -> tuple[float | None, str]:
        getter = getattr(os, "getloadavg", None)
        if getter is None:
            return None, "unavailable"
        try:
            return float(getter()[0]), "os.getloadavg"
        except OSError:
            return None, "unavailable"

    def sample(self) -> TelemetrySample:
        disk = shutil.disk_usage(self.disk_path)
        rss, rss_instrument = self._rss_bytes()
        load, load_instrument = self._load_1m()
        return TelemetrySample(
            monotonic_ns=time.monotonic_ns(),
            process_time_ns=time.process_time_ns(),
            disk_total_bytes=int(disk.total),
            disk_used_bytes=int(disk.used),
            disk_free_bytes=int(disk.free),
            rss_bytes=rss,
            load_1m=load,
            instruments={
                "monotonic_ns": "time.monotonic_ns",
                "process_time_ns": "time.process_time_ns",
                "disk": "shutil.disk_usage",
                "rss": rss_instrument,
                "load_1m": load_instrument,
            },
        )


class HomeostaticController:
    def __init__(self, policy: HomeostaticPolicy) -> None:
        self.policy = policy

    def assess(self, sample: TelemetrySample, *, journal_ok: bool) -> HealthAssessment:
        critical: list[str] = []
        degraded: list[str] = []
        if not journal_ok:
            critical.append("journal_integrity_failure")
        if sample.disk_free_ratio < self.policy.min_disk_free_ratio:
            critical.append("disk_free_below_critical")
        elif sample.disk_free_ratio < self.policy.degraded_disk_free_ratio:
            degraded.append("disk_free_below_degraded")
        if self.policy.max_rss_bytes is not None and sample.rss_bytes is not None:
            if sample.rss_bytes > self.policy.max_rss_bytes:
                critical.append("rss_above_configured_limit")
        if critical:
            return HealthAssessment(HealthState.CRITICAL, tuple(critical))
        if degraded:
            return HealthAssessment(HealthState.DEGRADED, tuple(degraded))
        return HealthAssessment(HealthState.NORMAL, ())


class PhysiologyEngine:
    """Headless MAPE-K-inspired physiology with an explicit constitutional authorization step.

    Sense -> Analyze -> Plan -> Authorize(Ω via GovernedOrganism) -> Execute -> ObserveEffect -> Remember.
    """

    def __init__(
        self,
        *,
        organism: GovernedOrganism,
        journal: DurableEventJournal,
        telemetry: NativeTelemetry,
        policy: HomeostaticPolicy | None = None,
        executor: Executor | None = None,
    ) -> None:
        self.organism = organism
        self.journal = journal
        self.telemetry = telemetry
        self.controller = HomeostaticController(policy or HomeostaticPolicy())
        self.executor = executor
        self._state_key = f"physiology:{self.organism.organism_id}"
        persisted = self.journal.get_state(
            self._state_key,
            {"cycle_seq": 0, "safe_mode": False, "normal_streak": 0, "throttled": False},
        )
        self._runtime_state = self._validate_runtime_state(persisted)

    @staticmethod
    def _validate_runtime_state(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("invalid physiology runtime state")
        cycle_seq = int(value.get("cycle_seq", 0))
        normal_streak = int(value.get("normal_streak", 0))
        if cycle_seq < 0 or normal_streak < 0:
            raise ValueError("invalid physiology counters")
        return {
            "cycle_seq": cycle_seq,
            "safe_mode": bool(value.get("safe_mode", False)),
            "normal_streak": normal_streak,
            "throttled": bool(value.get("throttled", False)),
        }

    def _persist_runtime_state(self) -> None:
        self.journal.set_state(self._state_key, self._runtime_state)

    @property
    def safe_mode(self) -> bool:
        return bool(self._runtime_state["safe_mode"])

    @property
    def throttled(self) -> bool:
        return bool(self._runtime_state["throttled"])

    def _plan_recovery(self, assessment: HealthAssessment) -> RecoveryAction:
        if assessment.state is HealthState.CRITICAL:
            self._runtime_state["safe_mode"] = True
            self._runtime_state["throttled"] = True
            self._runtime_state["normal_streak"] = 0
            return RecoveryAction.ENTER_SAFE_MODE
        if assessment.state is HealthState.DEGRADED:
            self._runtime_state["throttled"] = True
            self._runtime_state["normal_streak"] = 0
            return RecoveryAction.THROTTLE
        self._runtime_state["normal_streak"] += 1
        if self.safe_mode:
            if self._runtime_state["normal_streak"] >= self.controller.policy.normal_streak_to_exit_safe_mode:
                self._runtime_state["safe_mode"] = False
                self._runtime_state["throttled"] = False
                return RecoveryAction.EXIT_SAFE_MODE
            return RecoveryAction.NONE
        self._runtime_state["throttled"] = False
        return RecoveryAction.NONE

    def tick(
        self,
        *,
        proposal: Mapping[str, Any] | None = None,
        human: Mapping[str, Any] | None = None,
        ontology_ok: bool = True,
        signature_valid: bool = True,
        transition_valid: bool = True,
    ) -> CycleResult:
        self._runtime_state["cycle_seq"] += 1
        cycle_seq = int(self._runtime_state["cycle_seq"])
        cycle_id = stable_hash(
            {
                "schema": SCHEMA_VERSION,
                "organism_id": self.organism.organism_id,
                "cycle_seq": cycle_seq,
                "previous_state_root": self.organism.state_root(),
            }
        )
        tick_event = self.journal.append(
            event_id=f"{cycle_id}:tick",
            topic="physiology",
            event_type="TICK",
            payload={"cycle_id": cycle_id, "cycle_seq": cycle_seq},
            correlation_id=cycle_id,
        )

        sample = self.telemetry.sample()
        journal_ok = self.journal.integrity_check()
        assessment = self.controller.assess(sample, journal_ok=journal_ok)
        recovery_action = self._plan_recovery(assessment)
        self._persist_runtime_state()

        observation = self.journal.append(
            event_id=f"{cycle_id}:observation",
            topic="physiology",
            event_type="OBSERVATION",
            payload={
                "cycle_id": cycle_id,
                "telemetry": asdict(sample),
                "journal_integrity": journal_ok,
                "health": assessment.state.value,
                "reasons": list(assessment.reasons),
            },
            causation_id=tick_event.event_id,
            correlation_id=cycle_id,
        )
        plan = self.journal.append(
            event_id=f"{cycle_id}:plan",
            topic="physiology",
            event_type="RECOVERY_PLAN",
            payload={
                "cycle_id": cycle_id,
                "action": recovery_action.value,
                "safe_mode": self.safe_mode,
                "throttled": self.throttled,
            },
            causation_id=observation.event_id,
            correlation_id=cycle_id,
        )

        decision: Decision | None = None
        executed = False
        execution_event_id: str | None = None
        effect_payload: Mapping[str, Any] | None = None

        if proposal is not None:
            proposal_copy = json.loads(canonical_json(dict(proposal)))
            if self.safe_mode:
                decision = Decision.HOLD
                reason = "physiology safe mode blocks external execution"
                decision_receipt = evidence_receipt(
                    "PHYSIOLOGY_SAFE_MODE_DECISION",
                    {"cycle_id": cycle_id, "proposal": proposal_copy},
                    {"decision": decision.value, "reason": reason},
                )
                decision_event = self.journal.append(
                    event_id=f"{cycle_id}:decision",
                    topic="physiology",
                    event_type="DECISION",
                    payload={"decision": decision.value, "reason": reason, "receipt": decision_receipt},
                    causation_id=plan.event_id,
                    correlation_id=cycle_id,
                )
            else:
                loop_result = self.organism.evaluate(
                    event_id=f"{cycle_id}:organism-evaluation",
                    proposal=proposal_copy,
                    human=human,
                    ontology_ok=ontology_ok,
                    signature_valid=signature_valid,
                    transition_valid=transition_valid,
                )
                decision = loop_result.decision
                decision_event = self.journal.append(
                    event_id=f"{cycle_id}:decision",
                    topic="physiology",
                    event_type="DECISION",
                    payload={
                        "decision": loop_result.decision.value,
                        "reason": loop_result.reason,
                        "organism_receipt": dict(loop_result.evidence),
                    },
                    causation_id=plan.event_id,
                    correlation_id=cycle_id,
                )
            if decision is Decision.PASS:
                if self.executor is None:
                    raise RuntimeError("PASS decision requires an explicit executor")
                try:
                    result = self.executor(proposal_copy)
                except Exception as exc:
                    effect_payload = {
                        "status": "ERROR",
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                    }
                else:
                    if not isinstance(result, ExecutionResult):
                        raise TypeError("executor must return ExecutionResult")
                    effect_payload = {"status": result.status, "effect": json.loads(canonical_json(dict(result.effect)))}
                    executed = True
                execution = self.journal.append(
                    event_id=f"{cycle_id}:execution",
                    topic="physiology",
                    event_type="EXECUTION",
                    payload={"attempted": True, "executed": executed},
                    causation_id=decision_event.event_id,
                    correlation_id=cycle_id,
                )
                execution_event_id = execution.event_id
                self.journal.append(
                    event_id=f"{cycle_id}:effect",
                    topic="physiology",
                    event_type="EFFECT",
                    payload=dict(effect_payload or {"status": "UNKNOWN"}),
                    causation_id=execution.event_id,
                    correlation_id=cycle_id,
                )

        cycle_core = {
            "schema": SCHEMA_VERSION,
            "cycle_id": cycle_id,
            "cycle_seq": cycle_seq,
            "health": assessment.state.value,
            "recovery_action": recovery_action.value,
            "safe_mode": self.safe_mode,
            "throttled": self.throttled,
            "decision": None if decision is None else decision.value,
            "executed": executed,
            "execution_event_id": execution_event_id,
            "state_root": self.organism.state_root(),
        }
        receipt = evidence_receipt("PHYSIOLOGY_CYCLE", cycle_core, {"closed": True})
        self.journal.append(
            event_id=f"{cycle_id}:memory",
            topic="physiology",
            event_type="MEMORY_COMMIT",
            payload={**cycle_core, "cycle_receipt": receipt},
            causation_id=execution_event_id or plan.event_id,
            correlation_id=cycle_id,
        )
        self._persist_runtime_state()
        return CycleResult(
            cycle_id=cycle_id,
            cycle_seq=cycle_seq,
            health=assessment.state,
            recovery_action=recovery_action,
            decision=decision,
            executed=executed,
            state_root=self.organism.state_root(),
            receipt_hash=receipt["receipt_hash"],
        )
