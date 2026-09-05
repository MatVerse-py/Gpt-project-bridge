from app.source_evidence import (
    EvidenceState,
    RepresentationType,
    SourceRepresentation,
    build_source_evidence,
)
from app.source_resolver import resolve_source, source_is_admissible


def test_screenshot_is_partial_visual_evidence_not_structured_identity():
    rep = SourceRepresentation.from_bytes(
        kind=RepresentationType.SCREENSHOT,
        locator="library://urano.png",
        content=b"png-bytes",
        metadata={"visible_text": ["URANO OSX", "Q-Gate", "Ledger"]},
    )
    ev = build_source_evidence(original_url="https://example.invalid/urano", representations=(rep,))
    assert ev.state is EvidenceState.PARTIAL
    assert ev.independent_evidence is True


def test_generated_image_is_not_independent_external_evidence():
    rep = SourceRepresentation.from_bytes(
        kind=RepresentationType.GENERATED_IMAGE,
        locator="library://omega-seed.png",
        content=b"generated-image",
        metadata={"generated": True, "external_claims": ["sepolia_deployed"]},
    )
    ev = build_source_evidence(original_url="https://example.invalid/genesis", representations=(rep,))
    assert ev.independent_evidence is False
    assert ev.state is EvidenceState.PARTIAL


def test_model_generated_flag_downgrades_even_saved_image_kind():
    rep = SourceRepresentation.from_bytes(
        kind=RepresentationType.SAVED_IMAGE,
        locator="library://generated-report.png",
        content=b"generated-report",
        metadata={"model_generated": True, "doi": "10.9999/not-proof"},
    )
    ev = build_source_evidence(original_url="https://example.invalid/report", representations=(rep,))
    assert ev.independent_evidence is False
    assert ev.evidence_tier == "P0"
    assert "doi" not in ev.identifiers
    assert ev.state is EvidenceState.PARTIAL


def test_generated_only_partial_resolution_is_not_admissible():
    def generated(_: str):
        return SourceRepresentation.from_bytes(
            kind=RepresentationType.GENERATED_IMAGE,
            locator="library://generated.png",
            content=b"generated",
            metadata={"generated": True},
        )

    resolution = resolve_source(
        original_url="https://example.invalid/generated",
        loaders={RepresentationType.GENERATED_IMAGE: generated},
    )
    assert resolution.evidence.state is EvidenceState.PARTIAL
    assert resolution.evidence.independent_evidence is False
    assert source_is_admissible(resolution) is False
    assert resolution.receipt["schema"] == "matverse.evidence-receipt.v1"


def test_document_page_render_is_derivative_not_independent():
    rep = SourceRepresentation.from_bytes(
        kind=RepresentationType.DOCUMENT_PAGE_RENDER,
        locator="render://orcid/page-1.png",
        content=b"rendered-page",
        metadata={"derived_from_hash": "a" * 64},
    )
    ev = build_source_evidence(original_url="library://orcid.pdf", representations=(rep,))
    assert ev.independent_evidence is False
    assert ev.state is EvidenceState.PARTIAL


def test_image_identifier_disagreement_yields_explicit_nonblocking_conflict_and_structured_wins():
    screenshot = SourceRepresentation.from_bytes(
        kind=RepresentationType.SCREENSHOT,
        locator="library://shot.png",
        content=b"shot",
        metadata={"doi": "10.1/image"},
    )
    structured = SourceRepresentation.from_text(
        kind=RepresentationType.SAVED_HTML,
        locator="library://record.html",
        content="<html></html>",
        metadata={"doi": "10.1/structured", "canonical_url": "https://example.org/record"},
    )
    ev = build_source_evidence(
        original_url="https://example.org/record",
        representations=(screenshot, structured),
    )
    assert ev.identifiers["doi"] == "10.1/structured"
    assert any(c.code == "IMAGE_METADATA_CONFLICT" for c in ev.conflicts)
    assert ev.state is EvidenceState.VERIFIED_SNAPSHOT
