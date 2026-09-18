import pytest

from app.bridge_core import (
    AnalyticStatus,
    EpistemicNature,
    build_envelope,
    normalize_codex_task,
)


def test_build_envelope_preserves_governance_and_causal_lineage():
    envelope = build_envelope(
        source="codex-cloud",
        destination="atlas",
        actor="engineering-agent",
        objective_id="objective-7",
        capability="record-task-result",
        payload={"task_id": "task_e_1", "execution_status": "FAILED"},
        schema="matverse.codex-task.v1",
        authority_ref="gate:decision-1",
        policy_ref="policy:bridge-1",
        correlation_id="trajectory-4",
        causation_id="task_e_0",
        evidence_refs=("commit:abc",),
        timestamp="2026-09-18T01:00:00+00:00",
        epistemic_nature=EpistemicNature.RUNTIME,
        analytic_status=AnalyticStatus.FACT,
    )

    assert envelope.envelope_id == build_envelope(
        source="codex-cloud",
        destination="atlas",
        actor="engineering-agent",
        objective_id="objective-7",
        capability="record-task-result",
        payload={"task_id": "task_e_1", "execution_status": "FAILED"},
        schema="matverse.codex-task.v1",
        authority_ref="gate:decision-1",
        policy_ref="policy:bridge-1",
        correlation_id="trajectory-4",
        causation_id="task_e_0",
        evidence_refs=("commit:abc",),
        timestamp="2026-09-18T01:00:00+00:00",
        epistemic_nature=EpistemicNature.RUNTIME,
        analytic_status=AnalyticStatus.FACT,
    ).envelope_id
    assert envelope.as_dict()["epistemic_nature"] == "RUNTIME"
    assert envelope.authority_ref == "gate:decision-1"


def test_envelope_fails_closed_without_authority_or_positive_ttl():
    common = dict(
        source="a", destination="b", actor="c", objective_id="o", capability="x",
        payload={}, schema="s", policy_ref="p", correlation_id="r",
        epistemic_nature=EpistemicNature.MODEL, analytic_status=AnalyticStatus.HOLD,
    )
    with pytest.raises(ValueError, match="authority_ref"):
        build_envelope(authority_ref="", **common)
    with pytest.raises(ValueError, match="positive"):
        build_envelope(authority_ref="gate:1", ttl_seconds=0, **common)


def test_codex_adapter_normalizes_operational_state_without_ui_dependency():
    task = normalize_codex_task(
        {
            "task_id": "task_e_1",
            "repository": "matverse/core",
            "status": "Falhou",
            "changed_files": ["app/core.py"],
            "diff_summary": {"additions": 91, "deletions": 4},
            "failure_reason": "tests failed",
        }
    )
    assert task["execution_status"] == "FAILED"
    assert task["diff_summary"] == {"additions": 91, "deletions": 4}


def test_codex_adapter_rejects_unknown_states():
    with pytest.raises(ValueError, match="unsupported"):
        normalize_codex_task({"task_id": "1", "repository": "r", "status": "mystery"})
