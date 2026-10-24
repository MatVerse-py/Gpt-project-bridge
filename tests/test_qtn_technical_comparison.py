"""Audit the benchmark *method and labels*, not just its favourable outcome."""
from __future__ import annotations

import json
from benchmarks.qtn_technical_comparison import (
    BENCHMARK_KIND, SEMANTIC_CASES, calculate, semantic_benchmark,
    version_benchmark, receipt_benchmark, confusion,
)


def test_benchmark_exposes_authored_labels_and_comparator_code():
    out = calculate(timing=False)
    assert out["kind"] == "AUTHOR_CREATED_CONTROLLED_FIXTURES_NOT_INDEPENDENT_REPLICATION"
    assert out["definitions"]["gold"].startswith("Author-assigned")
    assert "code-level reproduction" in out["semantic"]["baseline"]
    assert "NOT actual SHACL/OpenLineage/Pandera" in out["definitions"]["scope"]
    assert out["case_count"] >= 50
    assert len({c.label for c in SEMANTIC_CASES}) == len(SEMANTIC_CASES)


def test_semantic_comparison_reports_all_confusion_cells():
    result = semantic_benchmark()
    first, second = result["baseline_metrics"], result["integrated_metrics"]
    assert first["n"] == second["n"] == len(SEMANTIC_CASES)
    assert second["fp"] < first["fp"]
    assert second["recall"] >= first["recall"]
    assert second["fp"] > 0  # harder negations remain unsolved; show them
    assert all("gold" in case and "baseline" in case and "integrated" in case
               for case in result["cases"])


def test_version_comparison_reports_both_simple_baselines_and_hold():
    result = version_benchmark()
    assert result["integrated_new_version_recall"] == 1.0
    assert result["baseline_work_new_version_recall"] < 1.0
    assert result["baseline_version_new_version_recall"] == 1.0
    assert result["integrated_event_accuracy"] >= result["baseline_version_event_accuracy"]
    assert any(row["gold"] == "VERSION_METADATA_VARIATION" for row in result["cases"])


def test_receipt_control_demonstrates_hash_alone_not_enough():
    data = receipt_benchmark()
    assert data["integrated_metrics"]["accuracy"] >= data["sha_only_metrics"]["accuracy"]
    assert data["sha_only_metrics"]["accuracy"] >= data["metadata_metrics"]["accuracy"]
    assert data["integrated_metrics"]["fn"] == 0
    assert any(x["id"] == "spoofed-origin" and x["sha_only"] and not x["integrated"]
               for x in data["cases"])
    assert "not prove the truth" in data["task"]


def test_zero_positive_precision_is_null_not_misleading_zero():
    data = confusion([False, False], [False, False])
    assert data["precision"] is None
    assert data["accuracy"] == 1.0
    assert data["false_positive_rate"] == 0.0


def test_comparison_json_can_be_serialized_reproducibly():
    first = calculate(timing=False)
    second = calculate(timing=False)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
