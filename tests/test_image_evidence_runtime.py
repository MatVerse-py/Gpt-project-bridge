from app.image_evidence import (
    ImageEvidence,
    ImageEvidenceKind,
    ImageEvidenceState,
    dedupe_by_hash,
    group_by_hash,
    visual_near_duplicate_is_probative_match,
)


def test_screenshot_only_proves_visible_state_until_crosschecked():
    ev = ImageEvidence(
        source_id="urano-console",
        kind=ImageEvidenceKind.SCREENSHOT,
        sha256="a" * 64,
        visible_text=("URANO OSX", "Q-Gate", "Ledger"),
    )
    assert ev.independent_evidence is True
    assert ev.state is ImageEvidenceState.VISUAL_OBSERVATION


def test_generated_image_never_counts_as_independent_external_evidence():
    ev = ImageEvidence(
        source_id="omega-seed-generated",
        kind=ImageEvidenceKind.GENERATED_IMAGE,
        sha256="b" * 64,
        generated=True,
        external_claims=("sepolia_deployed", "zenodo_prepared"),
    )
    assert ev.independent_evidence is False
    assert ev.state is ImageEvidenceState.PARTIAL


def test_model_generated_flag_overrides_saved_image_appearance():
    ev = ImageEvidence(
        source_id="generated-report-looking-image",
        kind=ImageEvidenceKind.SAVED_IMAGE,
        sha256="c" * 64,
        generated=True,
    )
    assert ev.independent_evidence is False
    assert ev.state is ImageEvidenceState.PARTIAL


def test_document_page_render_is_derivative_not_independent_root():
    ev = ImageEvidence(
        source_id="pdf-page-render",
        kind=ImageEvidenceKind.DOCUMENT_PAGE_RENDER,
        sha256="d" * 64,
        derived_from_hash="e" * 64,
    )
    assert ev.independent_evidence is False
    assert ev.state is ImageEvidenceState.PARTIAL


def test_external_claim_in_screenshot_remains_partial_without_crosscheck():
    ev = ImageEvidence(
        source_id="deploy-shot",
        kind=ImageEvidenceKind.SAVED_IMAGE,
        sha256="f" * 64,
        external_claims=("doi_exists",),
    )
    assert ev.state is ImageEvidenceState.PARTIAL


def test_external_claim_can_upgrade_after_independent_verification():
    ev = ImageEvidence(
        source_id="deploy-shot",
        kind=ImageEvidenceKind.SAVED_IMAGE,
        sha256="1" * 64,
        external_claims=("doi_exists",),
        independently_verified_claims=("doi_exists",),
    )
    assert ev.state is ImageEvidenceState.VERIFIED_SNAPSHOT


def test_exact_duplicate_images_count_as_one_evidence_root():
    a = ImageEvidence(source_id="a", kind=ImageEvidenceKind.SAVED_IMAGE, sha256="2" * 64)
    b = ImageEvidence(source_id="b", kind=ImageEvidenceKind.SAVED_IMAGE, sha256="2" * 64)
    c = ImageEvidence(source_id="c", kind=ImageEvidenceKind.SAVED_IMAGE, sha256="3" * 64)
    groups = group_by_hash((a, b, c))
    assert len(groups) == 2
    assert groups["2" * 64] == (a, b)
    assert dedupe_by_hash((a, b, c)) == (a, c)


def test_visual_near_duplicate_never_collapses_without_exact_hash_or_verified_derivation():
    assert visual_near_duplicate_is_probative_match(exact_hash_equal=False) is False
    assert visual_near_duplicate_is_probative_match(exact_hash_equal=True) is True
