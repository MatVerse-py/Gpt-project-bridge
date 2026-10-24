"""Regression tests for opt-in TSC/QTN overlay; all fixtures are synthetic/documentary."""
from __future__ import annotations

from dataclasses import dataclass
import pytest

from app.qtn_semantic_review import SourceRecord, document_kind, pqc_expansion, review_findings
from app.qtn_watch import SourceItem, evaluate


@dataclass(frozen=True)
class Source:
    source: str
    title: str
    url: str
    summary: str = ""


def finding(item, *, ids=("QTN-007", "QTN-011"), impact="STANDARDIZATION"):
    return {
        "source": item.source, "title": item.title, "url": item.url,
        "qtn_ids": list(ids), "impact_type": impact,
        "validation_class": "EXTERNAL_RELEVANCE_NOT_MATVERSE_VALIDATION",
        "digest": "legacy-digest",
    }


def test_real_qtn_watch_source_item_finding_integration():
    src = SourceItem(
        source="arXiv",
        title="Energy Consumption of Post-Quantum Cryptography on Constrained and General-Purpose Architectures",
        url="https://arxiv.org/abs/2610.27111v1",
        summary="This paper benchmarks a standards framework for post-quantum cryptography."
    )
    actual = evaluate([src], threshold=0.0)
    assert len(actual) == 1
    old = actual[0].to_dict()
    assert old["impact_type"] == "STANDARDIZATION"
    out = review_findings([old], [src])[0]
    assert out["impact_type"] == old["impact_type"]  # not rewritten
    assert out["digest"] == old["digest"]
    assert out["semantic_review"]["document_kind"] == "PREPRINT"
    assert out["semantic_review"]["semantic_review_verdict"] == "HOLD_MANUAL_ADJUDICATION"
    assert "semantic_review" not in old


def test_fvqkd_preprint_not_standards_document():
    src = Source("arXiv", "FVQKD: A Formal Verification of Quantum Key Distribution Security in Rocq",
                 "https://arxiv.org/abs/2610.30645v1", "Research referencing technical standards")
    out = review_findings([finding(src, ids=("QTN-006", "QTN-024"))], [src])[0]
    assert out["semantic_review"]["document_kind"] == "PREPRINT"
    assert out["semantic_review"]["pointer_status"] == "VERSION_PINNED_URL"
    assert out["semantic_review"]["relation_status"] == "REVIEW_REQUIRED"
    assert out["semantic_review"]["evidence_status"] == "EXTERNAL_CONTENT_NOT_AUTHENTICATED"


def test_pqc_circuit_not_security_migration():
    src = Source("arXiv", "Quantum surrogate for Bayesian inversion",
                 "https://arxiv.org/abs/2610.18942v1",
                 "A PQC (parameterized quantum circuit) with classical optimization")
    out = review_findings([finding(src, ids=("QTN-017",), impact="SECURITY_MIGRATION")], [src])[0]
    a = out["semantic_review"]
    assert a["pqc_expansion"] == "PARAMETERIZED_QUANTUM_CIRCUIT"
    assert a["scientific_subject"] == "QUANTUM_CLASSICAL_COMPUTING"
    assert out["impact_type"] == "SECURITY_MIGRATION"


def test_explicit_post_quantum_cryptography_is_cryptography():
    r = SourceRecord("arXiv", "Post-Quantum Cryptography key encapsulation",
                     "https://arxiv.org/abs/2610.27111v1")
    assert pqc_expansion(r) == "POST_QUANTUM_CRYPTOGRAPHY"


def test_two_pqc_meanings_are_not_force_resolved():
    r = SourceRecord("arXiv", "PQC", "https://arxiv.org/abs/2610.27111v1",
                     "Parameterized quantum circuit and post-quantum cryptography")
    assert pqc_expansion(r) == "CONFLICTING_EXPANSIONS_REVIEW"


def test_ietf_draft_not_an_rfc():
    r = SourceRecord("IETF", "draft-ietf-pquip-pqc-hsm-constrained-08",
                     "https://datatracker.ietf.org/doc/draft-ietf-pquip-pqc-hsm-constrained/08/")
    assert document_kind(r) == ("IETF_DRAFT", "VERSION_PINNED_URL")


def test_an_rfc_is_not_necessarily_standards_track():
    r = SourceRecord("IETF", "JSON Canonicalization Scheme",
                     "https://www.rfc-editor.org/rfc/rfc8785")
    assert document_kind(r) == ("PUBLISHED_RFC", "VERSION_PINNED_URL")


def test_latest_arxiv_link_is_mutable():
    r = SourceRecord("arXiv", "Paper", "https://arxiv.org/abs/2610.18942")
    assert document_kind(r) == ("PREPRINT", "MUTABLE_LATEST_URL")


def test_spoofed_domain_and_source_conflict():
    for link in ("http://arxiv.org/abs/2610.30645v1",
                 "https://arxiv.org.attacker.org/abs/2610.30645v1",
                 "https://other.invalid/abs/2610.30645v1"):
        assert document_kind(SourceRecord("arXiv", "Paper", link))[0] == "UNKNOWN_SOURCE"
    assert document_kind(SourceRecord("IETF", "Paper",
                         "https://arxiv.org/abs/2610.30645v1"))[0] == "SOURCE_CONFLICT"


def test_fail_closed_on_missing_duplicate_and_mismatched_sources():
    src = Source("arXiv", "Paper", "https://arxiv.org/abs/2610.30645v1")
    old = finding(src)
    with pytest.raises(ValueError):
        review_findings([old], [])
    with pytest.raises(ValueError):
        review_findings([old], [src, src])
    with pytest.raises(ValueError):
        review_findings([{**old, "title": "wrong"}], [src])
    with pytest.raises(ValueError):
        review_findings([{**old, "source": "IETF"}], [src])


def test_fail_closed_on_invalid_relations_and_overwrite():
    src = Source("arXiv", "Paper", "https://arxiv.org/abs/2610.30645v1")
    old = finding(src)
    for invalid in ("QTN-007", ["WRONG"], [], None):
        with pytest.raises(ValueError):
            review_findings([{**old, "qtn_ids": invalid}], [src])
    with pytest.raises(ValueError):
        review_findings([{**old, "semantic_review": {"decision": "PASS"}}], [src])


def test_overlay_cannot_be_used_as_runtime_approval():
    src = Source("arXiv", "Paper", "https://arxiv.org/abs/2610.30645v1")
    out = review_findings([finding(src)], [src])[0]
    assert not any(k in out["semantic_review"] for k in ("authorization", "gate_decision", "external_pass"))
    assert out["semantic_review"]["semantic_review_verdict"].startswith("HOLD")
