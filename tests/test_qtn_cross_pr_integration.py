"""Integration regression for PR #95 source triage + PR #105 TSC version overlay."""
from app.qtn_watch import SourceItem, _digest, evaluate, impact_type
from app.qtn_semantic_review import review_findings


def test_work_level_alert_dedup_keeps_two_observed_revision_identifiers():
    v1 = SourceItem("arXiv", "Quantum network fidelity benchmark",
                    "https://arxiv.org/abs/2610.11658v1",
                    summary="Hypothetical experiment supports fidelity improvement")
    v2 = SourceItem("arXiv", "Quantum network fidelity benchmark",
                    "https://arxiv.org/abs/2610.11658v2",
                    summary="Hypothetical revision does not reproduce improvement")
    assert _digest(v1) == _digest(v2)
    alerts = evaluate([v1, v2], threshold=0.0)
    assert len(alerts) == 1  # intentional work-level alert dedup in #95
    enriched = review_findings([a.to_dict() for a in alerts], [v1, v2])
    assert len(enriched) == 1
    sem = enriched[0]["semantic_review"]
    assert sem["source_work_id"] == "arxiv:2610.11658"
    assert {r["version_id"] for r in sem["observed_version_inventory"]} == {
        "arxiv:2610.11658v1", "arxiv:2610.11658v2"}
    assert sem["version_lineage_status"] == "HOLD_MULTIVERSION_ADJUDICATION"
    assert sem["version_body_sha256"] is None
    assert sem["semantic_review_verdict"] == "HOLD_MANUAL_ADJUDICATION"


def test_pinned_ietf_draft_08_and_09_remain_drafts_not_final_standards():
    rows = [
        SourceItem("IETF", "PQC draft for constrained cryptography",
                   "https://datatracker.ietf.org/doc/draft-ietf-pquip-pqc-hsm-constrained/08/"),
        SourceItem("IETF", "PQC draft for constrained cryptography",
                   "https://datatracker.ietf.org/doc/draft-ietf-pquip-pqc-hsm-constrained/09/"),
    ]
    for row in rows:
        assert impact_type(row) == "STANDARDIZATION_DRAFT"
    result = evaluate(rows, threshold=0.0)
    assert len(result) == 2   # pinned versions are distinct URLs
    reviewed = review_findings([a.to_dict() for a in result], rows)
    for row in reviewed:
        sem = row["semantic_review"]
        assert sem["document_kind"] == "IETF_DRAFT"
        assert sem["source_work_id"] == "ietf-draft:draft-ietf-pquip-pqc-hsm-constrained"
        assert len(sem["observed_version_inventory"]) == 2
        assert sem["semantic_review_verdict"] == "HOLD_MANUAL_ADJUDICATION"


def test_pqc_in_quantum_circuit_paper_is_not_cryptographic_migration():
    item=SourceItem("arXiv", "Quantum-classical Bayesian inversion with PQC",
                    "https://arxiv.org/abs/2610.18942v1",
                    summary="PQC means parameterized quantum circuit for materials inference")
    assert impact_type(item) == "RESEARCH_ADVANCE"
    a=evaluate([item], threshold=0.0)[0]
    sem=review_findings([a.to_dict()], [item])[0]["semantic_review"]
    assert sem["pqc_expansion"]=="PARAMETERIZED_QUANTUM_CIRCUIT"
    assert sem["evidence_status"]=="EXTERNAL_CONTENT_NOT_AUTHENTICATED"
