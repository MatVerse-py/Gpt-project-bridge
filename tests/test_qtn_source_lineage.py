"""Synthetic counterexamples for scientific revision loss.

These fixtures do NOT assert that any named real preprint changed its claims.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.qtn_source_lineage import pointer_identity, version_inventory, metadata_fingerprint
from app.qtn_semantic_review import review_findings
from app.qtn_watch import SourceItem, evaluate


def mk(src, ids=("QTN-012", "QTN-018")):
    return {"source": src.source, "title": src.title, "url": src.url,
            "qtn_ids": list(ids), "impact_type": "RESEARCH_ADVANCE",
            "digest": "legacy-alert-id"}


def test_arxiv_versions_share_work_but_not_version_id():
    one = pointer_identity("https://arxiv.org/abs/2610.11658v1")
    two = pointer_identity("https://arxiv.org/pdf/2610.11658v2.pdf")
    assert one["work_id"] == two["work_id"] == "arxiv:2610.11658"
    assert one["version_id"] == "arxiv:2610.11658v1"
    assert two["version_id"] == "arxiv:2610.11658v2"
    assert one["version_id"] != two["version_id"]


def test_arxiv_latest_is_not_pinned_to_version():
    item = pointer_identity("https://arxiv.org/abs/2610.11658")
    assert item["work_id"] == "arxiv:2610.11658"
    assert item["version_id"] is None


def test_adversarial_revisions_disagree_without_becoming_one_claim():
    src1 = SourceItem("arXiv", "Quantum benchmark v1", "https://arxiv.org/abs/2610.11658v1",
                      summary="Hypothetical experiment: strong improvement observed.")
    src2 = SourceItem("arXiv", "Quantum benchmark v2", "https://arxiv.org/abs/2610.11658v2",
                      summary="Hypothetical revision: previous improvement not reproduced.")
    out = review_findings([mk(src2)], [src1, src2])[0]
    sem = out["semantic_review"]
    assert sem["source_work_id"] == "arxiv:2610.11658"
    assert sem["source_version_id"] == "arxiv:2610.11658v2"
    assert {r["version_id"] for r in sem["observed_version_inventory"]} == {
        "arxiv:2610.11658v1", "arxiv:2610.11658v2"}
    assert sem["version_lineage_status"] == "HOLD_MULTIVERSION_ADJUDICATION"
    assert sem["version_body_sha256"] is None
    assert out["digest"] == "legacy-alert-id"


def test_multiple_source_versions_do_not_auto_validate_science():
    src1 = SourceItem("arXiv", "Quantum network result", "https://arxiv.org/abs/2610.11658v1",
                      summary="Positive benchmark outcome")
    src2 = SourceItem("arXiv", "Quantum network result", "https://arxiv.org/abs/2610.11658v2",
                      summary="Result retracted in later revision")
    row = evaluate([src1, src2], threshold=0)[0]
    # Both main's current evaluator and PR #95's work-level dedup output at
    # least one source; project all observed versions on whichever survived.
    out = review_findings([row.to_dict()], [src1, src2])[0]["semantic_review"]
    assert len(out["observed_version_inventory"]) == 2
    assert out["semantic_review_verdict"] == "HOLD_MANUAL_ADJUDICATION"
    assert out["version_lineage_status"] == "HOLD_MULTIVERSION_ADJUDICATION"


def test_ietf_draft_revision_changes_work_never_becomes_rfc():
    url8 = "https://datatracker.ietf.org/doc/draft-ietf-pquip-pqc-hsm-constrained/08/"
    url9 = "https://datatracker.ietf.org/doc/draft-ietf-pquip-pqc-hsm-constrained/09/"
    v8, v9 = pointer_identity(url8), pointer_identity(url9)
    assert v8["work_id"] == v9["work_id"]
    assert v8["version_id"] != v9["version_id"]
    assert v8["pointer_kind"] == v9["pointer_kind"] == "IETF_INTERNET_DRAFT"
    assert "rfc" not in v8["work_id"]


def test_ietf_dash_version_form_also_supported():
    u1="https://datatracker.ietf.org/doc/draft-ietf-pquip-pqc-hsm-constrained-08/"
    u2="https://datatracker.ietf.org/doc/draft-ietf-pquip-pqc-hsm-constrained/08/"
    assert pointer_identity(u1)["version_id"] == pointer_identity(u2)["version_id"]


def test_unknown_and_malformed_urls_cannot_claim_verified_version():
    urls=["http://arxiv.org/abs/2610.11658v1",
          "https://arxiv.org.attacker.invalid/abs/2610.11658v1",
          "https://arxiv.org/abs/2610.11658v1?utm_source=watch",
          "https://arxiv.org:8443/abs/2610.11658v1",
          "https://someone@arxiv.org/abs/2610.11658v1"]
    for u in urls:
        assert pointer_identity(u)["version_id"] is None


def test_revision_metadata_hash_does_not_pretend_to_hash_article_body():
    source = SourceItem("arXiv", "Article", "https://arxiv.org/abs/2610.11658v1",
                        summary="Unverified abstract")
    fp=metadata_fingerprint(source)
    assert len(fp)==64
    out=review_findings([mk(source)], [source])[0]["semantic_review"]
    assert out["version_body_sha256"] is None
    assert out["observed_version_inventory"][0]["metadata_fingerprints"]==[fp]


def test_same_version_ambiguous_metadata_fails_to_hold():
    one=SourceItem("arXiv", "Article", "https://arxiv.org/abs/2610.11658v1", summary="claim A")
    alt=SourceItem("arXiv", "Altered Article", "https://arxiv.org/abs/2610.11658v1", summary="claim B")
    out=review_findings([mk(one)], [one, alt])[0]["semantic_review"]
    assert out["observed_version_inventory"][0]["metadata_conflict"] is True
    assert out["version_lineage_status"]=="HOLD_VERSION_METADATA_COLLISION"


def test_unpinned_latest_must_remain_hold():
    src=SourceItem("arXiv", "Quantum result", "https://arxiv.org/abs/2610.11658")
    out=review_findings([mk(src)], [src])[0]["semantic_review"]
    assert out["source_version_id"] is None
    assert out["version_lineage_status"]=="HOLD_UNPINNED_OR_UNKNOWN_VERSION"


def test_unrecognized_host_is_not_work_identity():
    x=pointer_identity("https://example.org/abs/2610.11658v1")
    assert x["work_id"] is None and x["pointer_kind"]=="UNRECOGNIZED"
