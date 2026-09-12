from __future__ import annotations

from copy import deepcopy

from experiments.tesseu_cross_host_v1 import seed_capsule
from experiments import tesseu_substrate_v2 as substrate


def _arm(monkeypatch, *, arm_id: str, runtime_id: str, arch: str):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("RUNNER_OS", "Linux")
    monkeypatch.setenv("RUNNER_ARCH", arch)
    monkeypatch.setenv("RUNNER_NAME", f"runner-{arm_id}")
    machine = "aarch64" if arch == "ARM64" else "x86_64"
    monkeypatch.setattr(substrate.platform, "machine", lambda: machine)
    return substrate.restore_substrate(
        seed_capsule(),
        runtime_id=runtime_id,
        arm_id=arm_id,
        declared_provider="github-actions",
    )


def test_cross_arch_preserves_semantic_continuity(monkeypatch):
    x64 = _arm(monkeypatch, arm_id="x64", runtime_id="runtime-x64", arch="X64")
    arm64 = _arm(monkeypatch, arm_id="arm64", runtime_id="runtime-arm64", arch="ARM64")
    report = substrate.compare_substrates([x64, arm64])
    assert report["architecture_heterogeneity_status"] == "PASS_PROVIDER_REPORTED_ARCH"
    assert report["hardware_independence_status"] == "PARTIAL_PASS_CROSS_ARCH_NOT_CRYPTOGRAPHICALLY_ATTESTED"
    assert report["external_provider_status"] == "HOLD_SECOND_INDEPENDENT_COMPUTE_PROVIDER_REQUIRED"
    assert report["invariants"]["same_semantic_lineage"] is True
    assert report["invariants"]["same_transferred_snapshot"] is True


def test_same_arch_cannot_promote_architecture(monkeypatch):
    a = _arm(monkeypatch, arm_id="a", runtime_id="runtime-a", arch="X64")
    b = _arm(monkeypatch, arm_id="b", runtime_id="runtime-b", arch="X64")
    report = substrate.compare_substrates([a, b])
    assert report["architecture_heterogeneity_status"] == "HOLD"
    assert report["substrate_independence_status"] == "HOLD"


def test_semantic_drift_blocks_promotion(monkeypatch):
    x64 = _arm(monkeypatch, arm_id="x64", runtime_id="runtime-x64", arch="X64")
    arm64 = _arm(monkeypatch, arm_id="arm64", runtime_id="runtime-arm64", arch="ARM64")
    mutated = deepcopy(arm64)
    mutated["semantic_lineage_hash"] = "drift"
    report = substrate.compare_substrates([x64, mutated])
    assert report["architecture_heterogeneity_status"] == "HOLD"
    assert report["invariants"]["same_semantic_lineage"] is False


def test_provider_label_without_environment_evidence_does_not_pass(monkeypatch):
    x64 = _arm(monkeypatch, arm_id="x64", runtime_id="runtime-x64", arch="X64")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("REPL_ID", raising=False)
    monkeypatch.delenv("REPL_SLUG", raising=False)
    monkeypatch.delenv("REPL_OWNER", raising=False)
    monkeypatch.setattr(substrate.platform, "machine", lambda: "x86_64")
    unknown = substrate.restore_substrate(
        seed_capsule(),
        runtime_id="runtime-unknown",
        arm_id="unknown",
        declared_provider="replit",
    )
    report = substrate.compare_substrates([x64, unknown])
    assert report["external_provider_status"] == "HOLD_SECOND_INDEPENDENT_COMPUTE_PROVIDER_REQUIRED"
    assert report["invariants"]["provider_signals_consistent"] is False
