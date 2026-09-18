from __future__ import annotations

from collections.abc import Mapping as MappingABC
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from .core import stable_hash

PROTOCOL_VERSION = "matverse.bridge.v1.1"


class EpistemicNature(str, Enum):
    ARTIFACT = "ARTEFATO"
    RUNTIME = "RUNTIME"
    INFERENCE = "INFERENCIA"
    MODEL = "MODELO"


class AnalyticStatus(str, Enum):
    FACT = "FATO"
    CITATION = "CITACAO"
    INFERENCE = "INFERENCIA"
    HOLD = "HOLD"
    CONTRADICTION = "CONTRADICAO"


class ExecutionStatus(str, Enum):
    OPEN = "OPEN"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class EngineeringState(str, Enum):
    """Distinct, non-interchangeable states in engineering promotion."""

    WORKSPACE = "WORKSPACE"
    COMMIT = "COMMIT"
    PUSH = "PUSH"
    PULL_REQUEST = "PULL_REQUEST"
    CI = "CI"
    MERGE = "MERGE"
    RUNTIME = "RUNTIME"


class EvidenceStatus(str, Enum):
    UNKNOWN = "UNKNOWN"
    PRESENT = "PRESENT"
    FAILED = "FAILED"


def _require_nonempty_string(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _freeze_json(value: Any) -> Any:
    """Create an immutable, JSON-compatible snapshot of causal content."""

    if isinstance(value, MappingABC):
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("payload mappings must use string keys")
            frozen[key] = _freeze_json(item)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError(f"payload contains unsupported JSON value: {type(value).__name__}")


def _thaw_json(value: Any) -> Any:
    if isinstance(value, MappingABC):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _canonicalize_timestamp(value: str | None) -> tuple[datetime, str]:
    if value is None:
        parsed = datetime.now(timezone.utc)
    else:
        raw = _require_nonempty_string("timestamp", value)
        if raw.endswith("Z"):
            raw = f"{raw[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError("timestamp must be a timezone-aware ISO 8601 value") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        parsed = parsed.astimezone(timezone.utc)
    return parsed, parsed.isoformat()


def _normalize_optional_sequence(value: Any, name: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{name} must be a list or tuple when provided")
    return list(value)


def _normalize_optional_mapping(value: Any, name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, MappingABC):
        raise ValueError(f"{name} must be a mapping when provided")
    return dict(value)


@dataclass(frozen=True)
class CausalEnvelope:
    """Provider-neutral unit transported by the Bridge.

    Authority is referenced rather than inferred: creating an envelope records
    an authorized traversal, but neither grants authority nor executes work.
    """

    envelope_id: str
    source: str
    destination: str
    actor: str
    objective_id: str
    capability: str
    payload: Mapping[str, Any]
    schema: str
    context_refs: tuple[str, ...]
    authority_ref: str
    policy_ref: str
    correlation_id: str
    causation_id: str | None
    timestamp: str
    ttl_seconds: int
    expires_at: str
    signature: str | None
    evidence_refs: tuple[str, ...]
    epistemic_nature: EpistemicNature
    analytic_status: AnalyticStatus

    def as_dict(self) -> dict[str, Any]:
        return {
            "envelope_id": self.envelope_id,
            "source": self.source,
            "destination": self.destination,
            "actor": self.actor,
            "objective_id": self.objective_id,
            "capability": self.capability,
            "payload": _thaw_json(self.payload),
            "schema": self.schema,
            "context_refs": self.context_refs,
            "authority_ref": self.authority_ref,
            "policy_ref": self.policy_ref,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "timestamp": self.timestamp,
            "ttl_seconds": self.ttl_seconds,
            "expires_at": self.expires_at,
            "signature": self.signature,
            "evidence_refs": self.evidence_refs,
            "epistemic_nature": self.epistemic_nature.value,
            "analytic_status": self.analytic_status.value,
            "protocol_version": PROTOCOL_VERSION,
        }


def build_envelope(
    *,
    source: str,
    destination: str,
    actor: str,
    objective_id: str,
    capability: str,
    payload: Mapping[str, Any],
    schema: str,
    authority_ref: str,
    policy_ref: str,
    correlation_id: str,
    epistemic_nature: EpistemicNature,
    analytic_status: AnalyticStatus,
    context_refs: tuple[str, ...] = (),
    causation_id: str | None = None,
    evidence_refs: tuple[str, ...] = (),
    timestamp: str | None = None,
    ttl_seconds: int = 3600,
    signature: str | None = None,
) -> CausalEnvelope:
    required = {
        "source": _require_nonempty_string("source", source),
        "destination": _require_nonempty_string("destination", destination),
        "actor": _require_nonempty_string("actor", actor),
        "objective_id": _require_nonempty_string("objective_id", objective_id),
        "capability": _require_nonempty_string("capability", capability),
        "schema": _require_nonempty_string("schema", schema),
        "authority_ref": _require_nonempty_string("authority_ref", authority_ref),
        "policy_ref": _require_nonempty_string("policy_ref", policy_ref),
        "correlation_id": _require_nonempty_string("correlation_id", correlation_id),
    }
    if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be a positive integer")

    observed_time, observed_at = _canonicalize_timestamp(timestamp)
    expires_at = (observed_time + timedelta(seconds=ttl_seconds)).isoformat()
    frozen_payload = _freeze_json(payload)
    canonical_payload = _thaw_json(frozen_payload)

    identity = {
        "protocol_version": PROTOCOL_VERSION,
        **required,
        "payload": canonical_payload,
        "context_refs": context_refs,
        "causation_id": causation_id,
        "timestamp": observed_at,
        "ttl_seconds": ttl_seconds,
        "expires_at": expires_at,
        "evidence_refs": evidence_refs,
        "epistemic_nature": epistemic_nature.value,
        "analytic_status": analytic_status.value,
    }
    return CausalEnvelope(
        envelope_id=stable_hash(identity),
        source=required["source"],
        destination=required["destination"],
        actor=required["actor"],
        objective_id=required["objective_id"],
        capability=required["capability"],
        payload=frozen_payload,
        schema=required["schema"],
        context_refs=context_refs,
        authority_ref=required["authority_ref"],
        policy_ref=required["policy_ref"],
        correlation_id=required["correlation_id"],
        causation_id=causation_id,
        timestamp=observed_at,
        ttl_seconds=ttl_seconds,
        expires_at=expires_at,
        signature=signature,
        evidence_refs=evidence_refs,
        epistemic_nature=epistemic_nature,
        analytic_status=analytic_status,
    )


def assert_envelope_fresh(
    envelope: CausalEnvelope,
    *,
    now: datetime | None = None,
) -> bool:
    """Fail closed if an envelope is expired before traversal."""

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must include a timezone")
    current = current.astimezone(timezone.utc)
    expiry, _ = _canonicalize_timestamp(envelope.expires_at)
    if current >= expiry:
        raise ValueError("envelope has expired")
    return True


_CODEX_STATUS = {
    "open": ExecutionStatus.OPEN,
    "aberto": ExecutionStatus.OPEN,
    "running": ExecutionStatus.RUNNING,
    "succeeded": ExecutionStatus.SUCCEEDED,
    "closed": ExecutionStatus.SUCCEEDED,
    "fechado": ExecutionStatus.SUCCEEDED,
    "merged": ExecutionStatus.SUCCEEDED,
    "combinado": ExecutionStatus.SUCCEEDED,
    "failed": ExecutionStatus.FAILED,
    "falhou": ExecutionStatus.FAILED,
    "cancelled": ExecutionStatus.CANCELLED,
    "canceled": ExecutionStatus.CANCELLED,
}

_ENGINEERING_SEQUENCE = tuple(EngineeringState)


def _normalize_evidence(value: Any) -> dict[str, Any]:
    if value is None:
        return {"status": EvidenceStatus.UNKNOWN.value, "ref": None}
    if isinstance(value, MappingABC):
        raw_status = str(value.get("status", "UNKNOWN")).upper()
        try:
            status = EvidenceStatus(raw_status)
        except ValueError as exc:
            raise ValueError(f"unsupported evidence status: {raw_status}") from exc
        return {"status": status.value, "ref": value.get("ref")}
    if isinstance(value, str) and value.strip():
        return {"status": EvidenceStatus.PRESENT.value, "ref": value}
    raise ValueError("evidence must be null, a non-empty reference, or a status mapping")


def assess_engineering_transition(evidence: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Assess promotion without treating workspace output as canonical or live.

    A later state cannot promote the transition past an unknown or failed earlier
    state. All seven states remain visible so missing evidence is never inferred.
    """

    supplied = evidence or {}
    unexpected = sorted(set(supplied) - {stage.value.lower() for stage in _ENGINEERING_SEQUENCE})
    if unexpected:
        raise ValueError(f"unsupported engineering evidence: {', '.join(unexpected)}")

    states: dict[str, dict[str, Any]] = {}
    highest: EngineeringState | None = None
    blocked = False
    for stage in _ENGINEERING_SEQUENCE:
        key = stage.value.lower()
        if stage is EngineeringState.WORKSPACE:
            item = _normalize_evidence(supplied.get(key, {"status": "PRESENT", "ref": None}))
        else:
            item = _normalize_evidence(supplied.get(key))
        states[key] = item
        if stage is EngineeringState.WORKSPACE:
            blocked = item["status"] != EvidenceStatus.PRESENT.value
            if not blocked:
                highest = stage
        elif not blocked and item["status"] == EvidenceStatus.PRESENT.value:
            highest = stage
        else:
            blocked = True

    return {
        "highest_demonstrated_state": highest.value if highest is not None else None,
        "canonical_integration": (
            "DEMONSTRATED"
            if highest in {EngineeringState.MERGE, EngineeringState.RUNTIME}
            else "HOLD"
        ),
        "runtime_traversal": "DEMONSTRATED" if highest is EngineeringState.RUNTIME else "HOLD",
        "evidence": states,
    }


def normalize_codex_task(task: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize exported/API task metadata; this does not scrape Codex UI."""

    task_id = _require_nonempty_string("task_id", task.get("task_id"))
    repository = _require_nonempty_string("repository", task.get("repository"))
    status = _require_nonempty_string("status", task.get("status"))
    raw_status = status.lower()
    if raw_status not in _CODEX_STATUS:
        raise ValueError(f"unsupported Codex task status: {status}")

    changed_files = _normalize_optional_sequence(task.get("changed_files"), "changed_files")
    diff = _normalize_optional_mapping(task.get("diff_summary"), "diff_summary")
    for key in ("additions", "deletions"):
        value = diff.get(key, 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"diff_summary.{key} must be a non-negative integer")
        diff[key] = value

    promotion = assess_engineering_transition(task.get("engineering_evidence"))
    return {
        "bridge_event": "engineering_transition",
        "source": {
            "system": "codex-cloud",
            "task_id": task_id,
            "repository": repository,
            "branch": task.get("branch"),
            "worktree": task.get("worktree"),
        },
        "state": {
            "task": _CODEX_STATUS[raw_status].value,
            "additions": diff["additions"],
            "deletions": diff["deletions"],
            "objective": task.get("objective"),
            "changed_files": changed_files,
            "failure_reason": task.get("failure_reason"),
            "started_at": task.get("started_at"),
            "finished_at": task.get("finished_at"),
        },
        "destination": {
            "system": "github",
            "target": task.get("target", "canonical-repository"),
        },
        "evidence_required": [stage.value.lower() for stage in _ENGINEERING_SEQUENCE[1:]],
        "promotion": promotion,
    }
