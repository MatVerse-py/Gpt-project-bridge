from __future__ import annotations

import json

import pytest

from app.executor_substitution import (
    ExecutorArm,
    OrganismCloneConfig,
    capture_snapshot,
    run_executor_substitution,
)
from app.physiology import ExecutionResult


def _config() -> OrganismCloneConfig:
    return OrganismCloneConfig(
        organism_id="substitution-test-organism",
        frozen_contract_hash="a" * 64,
        runtime_id="substitution-test-runtime",
        state_secret="state-secret-material",
        authority_secrets={"independent-authorizer": "authority-secret-material"},
    )


def _executor(model: str, *, validated: bool = True):
    def execute(proposal):
        return ExecutionResult(
            status="OK" if validated else "MISMATCH",
            effect={
                "validated": validated,
                "provider": "test-provider",
                "model": model,
                "output_hash": ("1" if model == "model-a" else "2") * 64,
                "usage": {"input_tokens": 10, "output_tokens": 1, "total_tokens": 11},
                "proposal_action": proposal.get("action"),
            },
        )

    return execute


def test_paired_substitution_preserves_organism_invariants(tmp_path):
    config = _config()
    snapshot = capture_snapshot(config)
    report = run_executor_substitution(
        config=config,
        snapshot=snapshot,
        proposal={"action": "READ", "task": "return a bounded result"},
        arms=(
            ExecutorArm("a", "test-provider", "model-a", _executor("model-a")),
            ExecutorArm("b", "test-provider", "model-b", _executor("model-b")),
        ),
        workdir=tmp_path,
    )

    assert report.substitution_pass is True
    assert all(report.invariants.values())
    assert report.arms[0].state_root_before == report.arms[1].state_root_before
    assert report.arms[0].state_root_after == report.arms[1].state_root_after
    assert report.arms[0].cycle_id == report.arms[1].cycle_id
    assert report.arms[0].effect_hash != report.arms[1].effect_hash
    assert all(item.success for item in report.arms)
    assert all(item.journal_integrity for item in report.arms)
    assert report.arms[0].journal_event_types == (
        "TICK",
        "OBSERVATION",
        "RECOVERY_PLAN",
        "DECISION",
        "EXECUTION",
        "EFFECT",
        "MEMORY_COMMIT",
    )


def test_task_failure_does_not_fake_substitution_pass(tmp_path):
    config = _config()
    snapshot = capture_snapshot(config)
    report = run_executor_substitution(
        config=config,
        snapshot=snapshot,
        proposal={"action": "READ", "task": "paired task"},
        arms=(
            ExecutorArm("a", "test-provider", "model-a", _executor("model-a")),
            ExecutorArm("b", "test-provider", "model-b", _executor("model-b", validated=False)),
        ),
        workdir=tmp_path,
    )

    assert all(report.invariants.values())
    assert report.arms[0].success is True
    assert report.arms[1].success is False
    assert report.substitution_pass is False


def test_secret_material_is_not_emitted_in_public_report(tmp_path):
    config = _config()
    snapshot = capture_snapshot(config)
    report = run_executor_substitution(
        config=config,
        snapshot=snapshot,
        proposal={"action": "READ", "task": "secret boundary check"},
        arms=(
            ExecutorArm("a", "test-provider", "model-a", _executor("model-a")),
            ExecutorArm("b", "test-provider", "model-b", _executor("model-b")),
        ),
        workdir=tmp_path,
    )

    rendered = json.dumps(report.public_dict(), sort_keys=True)
    assert "state-secret-material" not in rendered
    assert "authority-secret-material" not in rendered
    assert "state-secret-material" not in repr(config)
    assert "authority-secret-material" not in repr(config)


def test_rejects_non_substitution_same_executor_identity(tmp_path):
    config = _config()
    snapshot = capture_snapshot(config)
    with pytest.raises(ValueError, match="distinct executor identities"):
        run_executor_substitution(
            config=config,
            snapshot=snapshot,
            proposal={"action": "READ"},
            arms=(
                ExecutorArm("a", "provider", "same", _executor("model-a")),
                ExecutorArm("b", "provider", "same", _executor("model-b")),
            ),
            workdir=tmp_path,
        )
