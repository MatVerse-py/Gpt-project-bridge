from hashlib import sha256

import pytest

from app.source_evidence import (
    RepresentationType,
    SourceRepresentation,
    build_source_evidence,
)
from app.source_exchange import ARGUS_BATCH_SCHEMA, source_evidence_to_argus_batch


def test_source_evidence_exports_versioned_argus_batch_without_implicit_raw_text():
    html = SourceRepresentation.from_text(
        kind=RepresentationType.SAVED_HTML,
        locator="saved://record.html",
        content="<html>official record</html>",
        metadata={
            "citation_doi": "10.5281/zenodo.1",
            "description": "official record",
            "private_note": "must-not-cross-contract",
            "claim_relation": "SUPPORTS",
            "context_status": "OUT_OF_CONTEXT",
        },
    )
    generated = SourceRepresentation.from_bytes(
        kind=RepresentationType.GENERATED_IMAGE,
        locator="image://generated.png",
        content=b"generated-pixels",
        metadata={"model_generated": True},
    )
    evidence = build_source_evidence(
        original_url="https://example.invalid/record",
        representations=(html, generated),
    )

    batch = source_evidence_to_argus_batch(evidence)

    assert batch["schema"] == ARGUS_BATCH_SCHEMA
    assert batch["evidence_hash"] == evidence.evidence_hash
    assert len(batch["items"]) == 2
    assert "observed_text" not in batch["items"][0]
    assert "private_note" not in batch["items"][0]["metadata"]
    assert "claim_relation" not in batch["items"][0]["metadata"]
    assert "context_status" not in batch["items"][0]["metadata"]
    assert batch["items"][1]["independent"] is False
    assert batch["items"][1]["model_generated"] is True


def test_source_exchange_opt_in_text_is_hash_anchored_and_relation_is_explicit_and_bound():
    rep = SourceRepresentation.from_text(
        kind=RepresentationType.API_METADATA,
        locator="api://record/1",
        content='{"status":"published"}',
        metadata={"title": "Record 1"},
    )
    evidence = build_source_evidence(
        original_url="https://example.invalid/record/1",
        representations=(rep,),
    )
    observed = "The record status is published."

    batch = source_evidence_to_argus_batch(
        evidence,
        observed_text_by_locator={rep.locator: observed},
        claim_relation_by_locator={rep.locator: "SUPPORTS"},
        claim_ref="claim://1",
    )
    item = batch["items"][0]

    assert item["observed_text"] == observed
    assert item["observed_text_sha256"] == sha256(observed.encode("utf-8")).hexdigest()
    assert item["claim_relation"] == "SUPPORTS"
    assert item["relation_claim_ref"] == "claim://1"
    assert item["source_content_hash"] == rep.content_hash


def test_source_exchange_rejects_unbound_claim_relation():
    rep = SourceRepresentation.from_text(
        kind=RepresentationType.API_METADATA,
        locator="api://record/1",
        content='{"status":"published"}',
    )
    evidence = build_source_evidence(
        original_url="https://example.invalid/record/1",
        representations=(rep,),
    )

    with pytest.raises(ValueError, match="require claim_ref or claim_sha256"):
        source_evidence_to_argus_batch(
            evidence,
            claim_relation_by_locator={rep.locator: "SUPPORTS"},
        )
