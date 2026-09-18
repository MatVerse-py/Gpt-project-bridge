from datetime import datetime, timedelta, timezone

import pytest

import app.bridge_core as bridge_core
from app.bridge_core import (
    AnalyticStatus,
    EpistemicNature,
    assert_envelope_fresh,
    assess_engineering_transition,
    build_envelope,
    normalize_codex_task,
)


def _envelope_kwargs(**overrides):
    values = {
        "source": "codex-cloud",
        "destination": "atlas",
        "actor": "engineering-agent",
        "objective_id": "objective-7",
        "capability": "record-task-result",
        "payload": {"task_id": "task_e_1", "execution_status": "FAILED"},
        "schema": "matverse.codex-task.v1",
        "authority_ref": "gate:decision-1",
        "policy_ref": "policy:bridge-1",
        "correlation_id": "trajectory-4",
        "causation_id": "task_e_0",
        "evidence_refs": ("commit:abc",),
        "timestamp": "2026-09-18T01:00:00+00:00",
        "epistemic_nature": EpistemicNature.RUNTIME,
        "analytic_status": AnalyticStatus.FACT,
    }
    values.update(overrides)
    return values


def test_build_envelope_preserves_governance_and_causal_lineage():
    envelope = build_envelope(**_envelope_kwargs())

    assert envelope.envelope_id == build_envelope(**_envelope_kwargs()).envelope_id
    assert envelope.as_dict()["epistemic_nature"] == "RUNTIME"
    assert envelope.authority_ref == "gate:decision-1"
    assert envelope.expires_at == "2026-09-18T02:00:00+00:00"


def test_envelope_identifier_separates_causal_inputs_protocol_and_ttl(monkeypatch):
    baseline = build_envelope(**_envelope_kwargs())
    different_cause = build_envelope(**_envelope_kwargs(causation_id="task_e_2"))
    different_ttl = build_envelope(**_envelope_kwargs(ttl_seconds=7200))

    assert different_cause.envelope_id != baseline.envelope_id
    assert different_ttl.envelope_id != baseline.envelope_id

    monkeypatch.setattr(bridge_core, "PROTOCOL_VERSION", "matverse.bridge.v1.2-test")
    different_protocol = build_envelope(**_envelope_kwargs())
    assert different_protocol.envelope_id != baseline.envelope_id


def test_equivalent_timestamps_are_canonicalized_before_hashing():
    zulu = build_envelope(**_envelope_kwargs(timestamp="2026-09-18T01:00:00Z"))
    offset = build_envelope(**_envelope_kwargs(timestamp="2026-09-18T01:00:00+00:00"))

    assert zulu.timestamp == "2026-09-18T01:00:00+00:00"
    assert zulu.envelope_id == offset.envelope_id


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


def test_envelope_rejects_naive_timestamp_and_enforces_expiry_at_traversal():
    with pytest.raises(ValueError, match="timezone"):
        build_envelope(**_envelope_kwargs(timestamp="2026-09-18T01:00:00"))

    envelope = build_envelope(**_envelope_kwargs(ttl_seconds=60))
    assert assert_envelope_fresh(
        envelope,
        now=datetime(2026, 9, 18, 1, 0, 59, tzinfo=timezone.utc),
    )
    with pytest.raises(ValueError, match="expired"):
        assert_envelope_fresh(
            envelope,
            now=datetime(2026, 9, 18, 1, 1, 0, tzinfo=timezone.utc),
        )


def test_payload_is_detached_and_recursively_immutable():
    original = {"nested": {"items": [1, 2]}}
    envelope = build_envelope(**_envelope_kwargs(payload=original))

    original["nested"]["items"].append(3)
    assert envelope.as_dict()["payload"] == {"nested": {"items": [1, 2]}}

    with pytest.raises(TypeError):
        envelope.payload["new"] = "mutation"
    assert envelope.payload["nested"]["items"] == (1, 2)


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
    assert task["state"]["task"] == "FAILED"
    assert task["state"]["additions"] == 91
    assert task["state"]["deletions"] == 4
    assert task["promotion"]["highest_demonstrated_state"] == "WORKSPACE"
    assert task["promotion"]["canonical_integration"] == "HOLD"
    assert task["promotion"]["runtime_traversal"] == "HOLD"


def test_codex_adapter_treats_null_optional_collections_as_empty():
    task = normalize_codex_task(
        {
            "task_id": "task_e_1",
            "repository": "matverse/core",
            "status": "open",
            "changed_files": None,
            "diff_summary": None,
        }
    )

    assert task["state"]["changed_files"] == []
    assert task["state"]["additions"] == 0
    assert task["state"]["deletions"] == 0


def test_codex_adapter_rejects_null_required_identifiers():
    with pytest.raises(ValueError, match="task_id"):
        normalize_codex_task({"task_id": None, "repository": "r", "status": "open"})
    with pytest.raises(ValueError, match="repository"):
        normalize_codex_task({"task_id": "1", "repository": None, "status": "open"})


def test_codex_adapter_rejects_unknown_states():
    with pytest.raises(ValueError, match="unsupported"):
        normalize_codex_task({"task_id": "1", "repository": "r", "status": "mystery"})


def test_open_codex_task_does_not_imply_commit_pr_ci_merge_or_runtime():
    task = normalize_codex_task(
        {
            "task_id": "task_e_6aac90fdeed8832699998eb648a87ff7",
            "repository": "Gpt-project-bridge",
            "branch": "codex/conectar-o-codex-cloud-ao-bridge",
            "status": "Aberto",
            "diff_summary": {"additions": 338, "deletions": 0},
        }
    )
    assert task["bridge_event"] == "engineering_transition"
    assert task["state"]["task"] == "OPEN"
    assert task["promotion"]["evidence"]["commit"]["status"] == "UNKNOWN"
    assert task["promotion"]["evidence"]["pull_request"]["status"] == "UNKNOWN"
    assert task["promotion"]["evidence"]["ci"]["status"] == "UNKNOWN"
    assert task["promotion"]["evidence"]["merge"]["status"] == "UNKNOWN"
    assert task["promotion"]["evidence"]["runtime"]["status"] == "UNKNOWN"


def test_promotion_is_sequential_and_requires_runtime_evidence_separately():
    assessed = assess_engineering_transition(
        {
            "commit": "commit:abc",
            "push": "push:origin/topic",
            "pull_request": "pr:42",
            "ci": "ci:pass:42",
            "merge": "merge:def",
        }
    )
    assert assessed["highest_demonstrated_state"] == "MERGE"
    assert assessed["canonical_integration"] == "DEMONSTRATED"
    assert assessed["runtime_traversal"] == "HOLD"

    gap = assess_engineering_transition({"commit": "commit:abc", "merge": "merge:def"})
    assert gap["highest_demonstrated_state"] == "COMMIT"
    assert gap["canonical_integration"] == "HOLD"


def test_codex_adapter_rejects_invalid_diff_counts():
    with pytest.raises(ValueError, match="additions"):
        normalize_codex_task(
            {
                "task_id": "1",
                "repository": "r",
                "status": "open",
                "diff_summary": {"additions": -1, "deletions": 0},
            }
        )
