from benchmarks.organism_scaling_diagnostics_v2 import (
    _profile_target_calls,
    run_executor_mode,
    run_lineage_profile,
)


def test_scaling_diagnostic_sequential_preserves_decisions():
    result = run_executor_mode(mode="sequential", workers=1, iterations=12)
    assert result["invariant_pass"] is True
    assert result["decision_failures"] == 0
    assert result["total_evaluations"] == 12
    assert result["aggregate_ops_s"] > 0


def test_scaling_diagnostic_thread_preserves_decisions():
    result = run_executor_mode(mode="thread", workers=2, iterations=8)
    assert result["invariant_pass"] is True
    assert result["decision_failures"] == 0
    assert result["total_evaluations"] == 16


def test_lineage_profile_records_growth_without_changing_correctness():
    result = run_lineage_profile(iterations=18, bucket_size=6)
    assert result["invariant_pass"] is True
    assert result["decision_failures"] == 0
    assert len(result["buckets"]) == 3

    lineage_sizes = [row["lineage_entries"] for row in result["buckets"]]
    state_sizes = [row["serialized_state_bytes"] for row in result["buckets"]]
    assert lineage_sizes == sorted(lineage_sizes)
    assert len(set(lineage_sizes)) == len(lineage_sizes)
    assert state_sizes == sorted(state_sizes)
    assert all(row["state_root_us"] >= 0 for row in result["buckets"])
    assert all(row["canonical_json_us"] >= 0 for row in result["buckets"])
    assert all(row["evidence_receipt_us"] >= 0 for row in result["buckets"])


def test_function_profile_observes_target_runtime_functions():
    profile = _profile_target_calls(iterations=8)
    names = {row["function"] for row in profile["functions"]}
    assert "evaluate" in names
    assert "state_root" in names
    assert "canonical_json" in names
    assert "evidence_receipt" in names
