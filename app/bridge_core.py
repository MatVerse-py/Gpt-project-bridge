from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
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
    signature: str | None
    evidence_refs: tuple[str, ...]
    epistemic_nature: EpistemicNature
    analytic_status: AnalyticStatus

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["epistemic_nature"] = self.epistemic_nature.value
        value["analytic_status"] = self.analytic_status.value
        value["protocol_version"] = PROTOCOL_VERSION
        return value


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
        "source": source,
        "destination": destination,
        "actor": actor,
        "objective_id": objective_id,
        "capability": capability,
        "schema": schema,
        "authority_ref": authority_ref,
        "policy_ref": policy_ref,
        "correlation_id": correlation_id,
    }
    missing = sorted(name for name, value in required.items() if not value.strip())
    if missing:
        raise ValueError(f"empty required envelope fields: {', '.join(missing)}")
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")

    observed_at = timestamp or datetime.now(timezone.utc).isoformat()
    identity = {
        "protocol_version": PROTOCOL_VERSION,
        **required,
        "payload": dict(payload),
        "context_refs": context_refs,
        "causation_id": causation_id,
        "timestamp": observed_at,
        "evidence_refs": evidence_refs,
        "epistemic_nature": epistemic_nature.value,
        "analytic_status": analytic_status.value,
    }
    return CausalEnvelope(
        envelope_id=stable_hash(identity),
        source=source,
        destination=destination,
        actor=actor,
        objective_id=objective_id,
        capability=capability,
        payload=dict(payload),
        schema=schema,
        context_refs=context_refs,
        authority_ref=authority_ref,
        policy_ref=policy_ref,
        correlation_id=correlation_id,
        causation_id=causation_id,
        timestamp=observed_at,
        ttl_seconds=ttl_seconds,
        signature=signature,
        evidence_refs=evidence_refs,
        epistemic_nature=epistemic_nature,
        analytic_status=analytic_status,
    )


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
    if isinstance(value, Mapping):
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

    required = ("task_id", "repository", "status")
    missing = [key for key in required if not str(task.get(key, "")).strip()]
    if missing:
        raise ValueError(f"missing Codex task fields: {', '.join(missing)}")
    raw_status = str(task["status"]).strip().lower()
    if raw_status not in _CODEX_STATUS:
        raise ValueError(f"unsupported Codex task status: {task['status']}")

    diff = dict(task.get("diff_summary", {}))
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
            "task_id": str(task["task_id"]),
            "repository": str(task["repository"]),
            "branch": task.get("branch"),
            "worktree": task.get("worktree"),
        },
        "state": {
            "task": _CODEX_STATUS[raw_status].value,
            "additions": diff["additions"],
            "deletions": diff["deletions"],
            "objective": task.get("objective"),
            "changed_files": list(task.get("changed_files", ())),
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
