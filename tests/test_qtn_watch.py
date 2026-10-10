from __future__ import annotations

import subprocess
import sys

import httpx
from datetime import datetime, timezone
from pathlib import Path

from app.qtn_watch import (
    SourceItem,
    _canonical_url,
    _digest,
    _infer_published_at,
    _within_age,
    classify_qtn,
    evaluate,
    impact_type,
    recommendation_for,
    score_item,
)
from scripts.run_qtn_watch import _reported_digests


def test_qtn_routing_and_resource_allocation_mapping() -> None:
    item = SourceItem(
        source="arXiv",
        title="Coherence-aware routing and resource allocation in quantum networks",
        url="https://example.org/paper",
        summary="Adaptive path selection with entanglement fidelity constraints.",
    )
    mapped = set(classify_qtn(item))
    assert {"QTN-001", "QTN-002", "QTN-012", "QTN-013", "QTN-027"}.issubset(mapped)


def test_rfc_publication_not_misreported_as_final_standard_or_validation() -> None:
    item = SourceItem(
        source="IETF",
        title="RFC 10024 Post-Quantum Traditional Hybrid Key Agreement Mechanisms for TLS 1.3",
        url="https://datatracker.ietf.org/doc/rfc10024/",
    )
    findings = evaluate([item], threshold=0.5)
    assert len(findings) == 1
    finding = findings[0]
    assert "QTN-011" in finding.qtn_ids
    assert finding.impact_type == "RFC_PUBLICATION"
    assert finding.recommendation == "VERIFY_RFC_STATUS_BEFORE_REBASE"
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


def test_arxiv_pqc_research_cannot_be_promoted_to_standard() -> None:
    item = SourceItem(
        "arXiv",
        "A Zero-Knowledge Signature Framework for Efficient Post-Quantum Message Authentication in Cooperative Automated Driving",
        "https://arxiv.org/abs/2610.11490v1",
        summary="A proposed standards framework for post-quantum signatures.",
    )
    assert impact_type(item) == "RESEARCH_ADVANCE"
    assert recommendation_for(item) == "REVIEW_FOR_DELTA_AND_BENCHMARK"
    finding = evaluate([item])[0]
    assert finding.score >= 0.68
    assert finding.validation_class == "EXTERNAL_RELEVANCE_NOT_MATVERSE_VALIDATION"


def test_qkd_topology_preprint_does_not_trigger_crypto_migration() -> None:
    item = SourceItem(
        "arXiv",
        "Certifying Hidden Paths: Scalable Topology Assurance for QKD Networks",
        "https://arxiv.org/abs/2610.11513v1",
    )
    assert impact_type(item) == "RESEARCH_ADVANCE"
    assert recommendation_for(item) == "REVIEW_FOR_DELTA_AND_BENCHMARK"


def test_draft_and_final_standard_are_distinct() -> None:
    draft = SourceItem(
        "IETF", "Post-quantum key agreement for TLS",
        "https://datatracker.ietf.org/doc/draft-ietf-tls-hybrid-design/",
    )
    assert impact_type(draft) == "STANDARDIZATION_DRAFT"
    assert recommendation_for(draft) == "TRACK_DRAFT_AND_TEST_COMPATIBILITY"
    final_rfc = SourceItem(
        "IETF", "RFC 10024 post-quantum key agreement",
        "https://datatracker.ietf.org/doc/rfc10024/",
    )
    assert impact_type(final_rfc) == "RFC_PUBLICATION"
    nist_final = SourceItem(
        "NIST", "FIPS 203 ML-KEM post-quantum cryptography",
        "https://csrc.nist.gov/pubs/fips/203/final",
    )
    assert impact_type(nist_final) == "STANDARDIZATION"
    nist_news = SourceItem(
        "NIST", "NIST post-quantum standards framework news",
        "https://www.nist.gov/news-events/news-updates/post-quantum-standards-framework",
    )
    assert impact_type(nist_news) != "STANDARDIZATION"


def test_paper_versions_mirrors_protocol_and_title_do_not_duplicate() -> None:
    first = SourceItem("arXiv", "Quantum memory error correction", "http://export.arxiv.org/abs/2610.11658v1?utm_source=feed")
    second = SourceItem("arXiv", "Updated quantum memory error correction", "https://arxiv.org/pdf/2610.11658v2.pdf#abstract")
    assert _canonical_url(first.url) == "https://arxiv.org/abs/2610.11658"
    assert _canonical_url(first.url) == _canonical_url(second.url)
    assert _digest(first) == _digest(second)
    assert len(evaluate([first, second], threshold=0.0)) == 1


def test_nontracking_query_sort_does_not_erase_distinct_work() -> None:
    first = SourceItem("NIST", "Quantum network research", "http://example.org/report/?b=2&a=1&utm_source=watch")
    same = SourceItem("NIST", "Changed title", "https://example.org/report?a=1&b=2#section")
    different = SourceItem("NIST", "Different quantum network research", "https://example.org/report?a=2&b=2")
    assert _digest(first) == _digest(same)
    assert _digest(first) != _digest(different)
    assert len(evaluate([first, same, different], threshold=0.0)) == 2


def test_legacy_issue_urls_are_backfilled_as_canonical_digests() -> None:
    previous_digest = "a" * 64
    body = (
        "# QTN Watch\n"
        "## A Zero-Knowledge Signature Framework\n"
        "- Source: **arXiv**\n"
        "- URL: http://arxiv.org/abs/2610.11490v1\n"
        f"<!-- qtn-watch:{previous_digest} -->\n"
    )
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/issues")
        return httpx.Response(200, json=[{"body": body}])
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        reported = _reported_digests(client, "MatVerse-py/Gpt-project-bridge", "test-token")
    current = SourceItem("arXiv", "Updated zero-knowledge signature", "https://arxiv.org/abs/2610.11490v2")
    assert previous_digest in reported
    assert _digest(current) in reported


def test_issue94_eight_arxiv_signals_preserve_external_status() -> None:
    papers = [
        ("Error-Corrected Memory and Logic on a Heavy-Hex Superconducting-Qubit Processor", "2610.11658", "RESEARCH_ADVANCE"),
        ("Geometry-optimized hyperbolic codes for modular fault-tolerant quantum architectures", "2610.10948", "RESEARCH_ADVANCE"),
        ("A Zero-Knowledge Signature Framework for Efficient Post-Quantum Message Authentication in Cooperative Automated Driving", "2610.11490", "RESEARCH_ADVANCE"),
        ("Secure Quantum Handshakes: Quantum Network Verification Via Simon's Algorithm", "2610.12209", "RESEARCH_ADVANCE"),
        ("Certifying Hidden Paths: Scalable Topology Assurance for QKD Networks", "2610.11513", "RESEARCH_ADVANCE"),
        ("Experimental Measurement-Device-Independent Quantum Key distribution with Flawed State-Preparation over 300 km", "2610.11428", "EXTERNAL_DEMONSTRATION"),
        ("Quantum Networking at the Speed of Quantum Computation: Kilohertz Entanglement in a Heterogeneous Quantum System", "2610.10705", "RESEARCH_ADVANCE"),
        ("Quantum non-Markovian response spectra", "2610.10684", "RESEARCH_ADVANCE"),
    ]
    for title, arxiv_id, expected in papers:
        item = SourceItem("arXiv", title, f"https://arxiv.org/abs/{arxiv_id}v1")
        assert impact_type(item) == expected, (arxiv_id, impact_type(item))
        assert evaluate([item], threshold=0.0)[0].validation_class == "EXTERNAL_RELEVANCE_NOT_MATVERSE_VALIDATION"


def test_sparse_mirror_cannot_suppress_richer_candidate() -> None:
    sparse = SourceItem("arXiv", "Quantum", "http://export.arxiv.org/abs/2610.11658v1")
    rich = SourceItem(
        "arXiv",
        "Error-Corrected Quantum Memory on a fault-tolerant logical qubit",
        "https://arxiv.org/abs/2610.11658v2",
        summary="A logical qubit demonstration with quantum memory and quantum error correction.",
    )
    forward = evaluate([sparse, rich], threshold=0.5)
    backward = evaluate([rich, sparse], threshold=0.5)
    assert len(forward) == len(backward) == 1
    assert forward[0].digest == backward[0].digest
    assert forward[0].title == backward[0].title == rich.title
    assert forward[0].score == backward[0].score
