from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.qtn_watch import (
    SourceItem,
    _infer_published_at,
    _within_age,
    classify_qtn,
    evaluate,
    impact_type,
    score_item,
)


def test_qtn_routing_and_resource_allocation_mapping() -> None:
    item = SourceItem(
        source="arXiv",
        title="Coherence-aware routing and resource allocation in quantum networks",
        url="https://example.org/paper",
        summary="Adaptive path selection with entanglement fidelity constraints.",
    )
    mapped = set(classify_qtn(item))
    assert {"QTN-001", "QTN-002", "QTN-012", "QTN-013", "QTN-027"}.issubset(mapped)


def test_pqc_standardization_is_not_matverse_validation() -> None:
    item = SourceItem(
        source="IETF",
        title="RFC 10024 Post-Quantum Traditional Hybrid Key Agreement Mechanisms for TLS 1.3",
        url="https://datatracker.ietf.org/doc/rfc10024/",
    )
    findings = evaluate([item], threshold=0.5)
    assert len(findings) == 1
    finding = findings[0]
    assert "QTN-011" in finding.qtn_ids
    assert finding.impact_type == "STANDARDIZATION"
    assert finding.recommendation == "REBASE_AGAINST_EXTERNAL_STANDARD"
    assert finding.validation_class == "EXTERNAL_RELEVANCE_NOT_MATVERSE_VALIDATION"


def test_generic_standard_word_does_not_mean_standardization() -> None:
    item = SourceItem(
        source="arXiv",
        title="Standard model analysis for quantum memory dynamics",
        url="https://arxiv.org/abs/2609.00001",
        summary="A theoretical quantum memory analysis without standards activity.",
    )
    assert impact_type(item) == "RESEARCH_ADVANCE"


def test_rhetorical_demonstrate_in_abstract_is_not_external_demonstration() -> None:
    item = SourceItem(
        source="arXiv",
        title="Analytical bounds for noisy quantum memories",
        url="https://arxiv.org/abs/2609.00002",
        summary="We demonstrate mathematically that the bound is tight for the studied model.",
    )
    assert impact_type(item) == "RESEARCH_ADVANCE"


def test_low_relevance_item_is_filtered() -> None:
    item = SourceItem(
        source="NIST",
        title="Ordinary materials measurement update",
        url="https://example.org/materials",
    )
    assert classify_qtn(item) == ()
    assert score_item(item, ()) == 0.0
    assert evaluate([item], threshold=0.1) == []


def test_demonstration_classification() -> None:
    item = SourceItem(
        source="NIST",
        title="Quantum network demonstrated over commercial fiber",
        url="https://example.org/quantum-network",
        summary="Entanglement distribution was demonstrated in a field trial.",
    )
    assert impact_type(item) == "EXTERNAL_DEMONSTRATION"
    finding = evaluate([item], threshold=0.5)[0]
    assert "QTN-001" in finding.qtn_ids
    assert "QTN-013" in finding.qtn_ids
    assert finding.recommendation == "ADD_EXTERNAL_BASELINE_AND_RETEST"


def test_date_inference_and_recency_window() -> None:
    published = _infer_published_at(
        "Quantum network field trial",
        "https://example.org/2026/09/09/quantum-network-field-trial/",
    )
    assert published == "2026-09-09T00:00:00+00:00"
    assert _within_age(published, now=datetime(2026, 9, 12, tzinfo=timezone.utc))
    assert not _within_age("2025-01-01T00:00:00+00:00", now=datetime(2026, 9, 12, tzinfo=timezone.utc))


def test_duplicate_url_is_collapsed() -> None:
    a = SourceItem("IETF", "Quantum internet protocol stack standard", "https://example.org/1")
    b = SourceItem("IETF", "Quantum internet protocol stack standard duplicate", "https://example.org/1")
    findings = evaluate([a, b], threshold=0.5)
    assert len(findings) == 1


def test_runner_bootstraps_repository_import_path() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "scripts/run_qtn_watch.py", "--help"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "QTN-001..QTN-028" in result.stdout
