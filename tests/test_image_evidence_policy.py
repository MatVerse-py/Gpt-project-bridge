from app.source_evidence import EvidenceConflict, EvidenceState, RepresentationType, SourceEvidence


def test_screenshot_is_visual_observation_not_structured_identity():
    ev = SourceEvidence(
        source_id="img:urano",
        original_url=None,
        representation=RepresentationType.SCREENSHOT,
        content_hash="a" * 64,
        metadata={"visible_text": ["URANO OSX", "Q-Gate", "Ledger"]},
    )
    assert ev.representation is RepresentationType.SCREENSHOT
    assert ev.state in {EvidenceState.PARTIAL, EvidenceState.VERIFIED_SNAPSHOT}


def test_generated_image_is_not_independent_external_evidence():
    ev = SourceEvidence(
        source_id="img:omega-seed",
        original_url=None,
        representation=RepresentationType.GENERATED_IMAGE,
        content_hash="b" * 64,
        metadata={"claims_external_deploy": True},
    )
    assert ev.independent_evidence is False
    assert ev.state is EvidenceState.PARTIAL


def test_image_conflict_with_structured_metadata_is_blocking_for_identifier():
    conflict = EvidenceConflict(
        code="IMAGE_METADATA_CONFLICT",
        field="doi",
        values=("10.1/image", "10.1/structured"),
        blocking=True,
        reason="screenshot identifier conflicts with structured platform metadata",
    )
    ev = SourceEvidence(
        source_id="img:doi-conflict",
        original_url=None,
        representation=RepresentationType.SCREENSHOT,
        content_hash="c" * 64,
        conflicts=(conflict,),
    )
    assert ev.state is EvidenceState.CONFLICT
