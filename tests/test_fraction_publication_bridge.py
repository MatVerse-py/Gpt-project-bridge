from __future__ import annotations

from dataclasses import replace

import pytest

from app.corpus_fractions import build_fraction_plan
from app.evidence import sha256_text
from app.fraction_publication_bridge import (
    DisclosureMode,
    FractionPublicationBridge,
    FractionPublicationError,
)


def make_plan():
    members = [
        {
            "ordinal": index,
            "document_id": f"doc-{index}",
            "conversation_id": f"conv-private-{index}",
            "source_hash": sha256_text(f"source-{index}"),
        }
        for index in range(1, 18)
    ]
    return build_fraction_plan(members, fraction_size=14)


def test_metadata_only_envelope_exposes_hiding_commitments_not_structural_hashes():
    plan = make_plan()
    bridge = FractionPublicationBridge(plan, corpus_id="patente")
    envelope = bridge.build_envelope(1)
    public_manifest = envelope.public_manifest()

    serialized = str(public_manifest)
    assert envelope.disclosure_mode is DisclosureMode.METADATA_ONLY
    assert "conv-private" not in serialized
    assert "document_id" not in serialized
    assert "conversation_id" not in serialized
    assert "source_hash" not in serialized
    assert "manifest_hash" not in public_manifest
    assert "merge_root" not in public_manifest
    assert len(public_manifest["fraction_commitment_root"]) == 64
    assert len(public_manifest["corpus_commitment_root"]) == 64
    assert public_manifest["corpus_commitment_root"] != plan.merge_root


def test_same_private_plan_yields_different_public_roots_by_default():
    plan = make_plan()
    a = FractionPublicationBridge(plan, corpus_id="patente")
    b = FractionPublicationBridge(plan, corpus_id="patente")

    assert a.commitment_work.public.root != b.commitment_work.public.root


def test_content_bundle_requires_explicit_disclosure_mode_and_hash():
    bridge = FractionPublicationBridge(make_plan(), corpus_id="patente")

    with pytest.raises(FractionPublicationError, match="requires bundle_sha256"):
        bridge.build_envelope(1, disclosure_mode=DisclosureMode.REDACTED_BUNDLE)

    with pytest.raises(FractionPublicationError, match="must not attach"):
        bridge.build_envelope(1, bundle_sha256=sha256_text("private"))

    envelope = bridge.build_envelope(
        1,
        disclosure_mode=DisclosureMode.REDACTED_BUNDLE,
        bundle_sha256=sha256_text("redacted-bundle"),
    )
    assert envelope.bundle_sha256 == sha256_text("redacted-bundle")


def test_zenodo_plan_is_prepared_staging_only_and_requires_authorization():
    bridge = FractionPublicationBridge(make_plan(), corpus_id="patente")
    envelope = bridge.build_envelope(2)
    draft = bridge.prepare_zenodo_draft(envelope, creators=("MatVerse",))

    assert draft.action == "zenodo.create_draft"
    assert draft.requires_authorization is True
    assert draft.marxiv_stage == "Prepared"
    assert draft.external_files == ()
    assert len(draft.generated_files) == 1
    manifest = next(iter(draft.generated_files.values()))
    assert "corpus_commitment_root" in manifest
    assert "merge_root" not in manifest
    assert "conversation_id" not in manifest
    assert draft.receipt["schema"] == "matverse.evidence-receipt.v1"


def test_forged_commitment_envelope_is_rejected_before_staging():
    bridge = FractionPublicationBridge(make_plan(), corpus_id="patente")
    envelope = bridge.build_envelope(1)
    forged = replace(
        envelope,
        fraction_commitment_root=sha256_text("forged-commitment"),
    )

    with pytest.raises(FractionPublicationError, match="commitment fields"):
        bridge.prepare_zenodo_draft(forged, creators=("MatVerse",))


def test_zenodo_staging_requires_explicit_creator():
    bridge = FractionPublicationBridge(make_plan(), corpus_id="patente")
    envelope = bridge.build_envelope(1)

    with pytest.raises(FractionPublicationError, match="explicit creator"):
        bridge.prepare_zenodo_draft(envelope)
