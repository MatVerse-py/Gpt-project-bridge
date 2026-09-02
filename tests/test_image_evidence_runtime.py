from app.image_evidence import (
    ImageEvidence,
    ImageEvidenceKind,
    ImageEvidenceState,
    dedupe_by_hash,
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


def test_external_claim_in_screenshot_remains_partial_without_crosscheck():
    ev = ImageEvidence(
        source_id="deploy-shot",
        kind=ImageEvidenceKind.SAVED_IMAGE,
        sha256="c" * 64,
        external_claims=("doi_exists",),
    )
    assert ev.state is ImageEvidenceState.PARTIAL


def test_external_claim_can_upgrade_after_independent_verification():
    ev = ImageEvidence(
        source_id="deploy-shot",
        kind=ImageEvidenceKind.SAVED_IMAGE,
        sha256="d" * 64,
        external_claims=("doi_exists",),
        independently_verified_claims=("doi_exists",),
    )
    assert ev.state is ImageEvidenceState.VERIFIED_SNAPSHOT


def test_exact_duplicate_images_count_as_one_evidence_root():
    a = ImageEvidence(source_id="a", kind=ImageEvidenceKind.SAVED_IMAGE, sha256="e" * 64)
    b = ImageEvidence(source_id="b", kind=ImageEvidenceKind.SAVED_IMAGE, sha256="e" * 64)
    assert dedupe_by_hash((a, b)) == (a,)
