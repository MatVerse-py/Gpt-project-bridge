from experiments.tesseu_cross_host_v1 import (
    compare_host_results,
    restore_and_continue,
    seed_capsule,
)


def _fake_host_result(*, arm_id: str, runtime_id: str, os_name: str, post_root: str) -> dict:
    return {
        "schema": "matverse.tesseu-cross-host.v1",
        "arm_id": arm_id,
        "runtime_id": runtime_id,
        "snapshot_hash": "snapshot",
        "pre_state_root": "pre-root",
        "post_state_root": post_root,
        "organism_id": "organism",
        "constitutional_contract_hash": "contract",
        "gate_fingerprint": "gate",
        "decision": "PASS",
        "reason": "admissible transition",
        "semantic_lineage_hash": "semantic-lineage",
        "invariants": {"all": True},
        "local_pass": True,
        "environment": {
            "runner_os": os_name,
            "runner_arch": "X64",
        },
        "host_binding": f"host-{arm_id}",
        "result_hash": f"result-{arm_id}",
    }


def test_seed_capsule_restores_and_continues_on_new_runtime() -> None:
    capsule = seed_capsule()
    result = restore_and_continue(capsule, runtime_id="runtime-b", arm_id="arm-b")
    assert result["local_pass"] is True
    assert result["runtime_id"] == "runtime-b"
    assert result["pre_state_root"] == capsule["pre_continuation_state_root"]
    assert result["organism_id"] == capsule["organism_id"]
    assert result["constitutional_contract_hash"] == capsule["constitutional_contract_hash"]
    assert result["decision"] == "PASS"


def test_cross_os_compare_accepts_semantic_continuity_even_when_raw_roots_differ() -> None:
    linux = _fake_host_result(arm_id="linux", runtime_id="runtime-linux", os_name="Linux", post_root="raw-linux")
    windows = _fake_host_result(arm_id="windows", runtime_id="runtime-windows", os_name="Windows", post_root="raw-windows")
    report = compare_host_results([linux, windows])
    assert report["cross_host_os_status"] == "PASS"
    assert report["raw_post_state_roots_equal"] is False
    assert report["external_provider_status"] == "HOLD_SECOND_INDEPENDENT_COMPUTE_PROVIDER_REQUIRED"
    assert report["hardware_independence_status"] == "HOLD_ATTESTED_HETEROGENEOUS_HARDWARE_REQUIRED"


def test_cross_host_compare_fails_without_os_heterogeneity() -> None:
    a = _fake_host_result(arm_id="a", runtime_id="runtime-a", os_name="Linux", post_root="a")
    b = _fake_host_result(arm_id="b", runtime_id="runtime-b", os_name="Linux", post_root="b")
    report = compare_host_results([a, b])
    assert report["cross_host_os_status"] == "FAIL"
    assert report["invariants"]["distinct_operating_systems"] is False


def test_cross_host_compare_fails_on_semantic_lineage_drift() -> None:
    a = _fake_host_result(arm_id="a", runtime_id="runtime-a", os_name="Linux", post_root="a")
    b = _fake_host_result(arm_id="b", runtime_id="runtime-b", os_name="Windows", post_root="b")
    b["semantic_lineage_hash"] = "drifted"
    report = compare_host_results([a, b])
    assert report["cross_host_os_status"] == "FAIL"
    assert report["invariants"]["same_semantic_lineage"] is False
