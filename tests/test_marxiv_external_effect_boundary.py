from __future__ import annotations

import json
from pathlib import Path

import pytest

import app.marxiv_runtime_publisher as publisher
from app.publication_bridge import PublicationBridgeError


def test_runtime_publish_cannot_cross_external_effect_gate_after_legacy_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    manuscript = sandbox / "paper.pdf"
    manuscript.write_bytes(b"fixture")
    manifest_path = sandbox / "arxiv-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema": "matverse.publication-bridge.v1",
                "publication_id": "matverse-2.0-v1-arxiv",
                "manuscript_file": "paper.pdf",
                "primary_archive": "cs",
                "primary_category": "cs.SE",
                "crosslist_archives": [],
                "crosslist_categories": [],
                "license": "CC BY 4.0",
                "title": "MATVERSE 2.0 fixture",
                "authors": ["Test Author"],
                "abstract": "A sufficiently long fixture abstract for the external effect boundary test.",
            }
        ),
        encoding="utf-8",
    )

    state = publisher.PublisherState(
        object_id="matverse-2.0",
        version="v1",
        publication_id="matverse-2.0-v1-arxiv",
        status="APPROVED",
        object_hash="a" * 64,
        manifest_hash="b" * 64,
        manuscript_sha256="c" * 64,
        arxiv_subfile_sha256="d" * 64,
        review_packet_hash="e" * 64,
        package_hash="f" * 64,
        object_snapshot_path=str(sandbox / "scientific-object.snapshot.json"),
        arxiv_manifest_path=str(manifest_path),
        arxiv_state_path=str(sandbox / "transport" / "publication-state.json"),
        review_packet_path=str(sandbox / "review-packet.json"),
        approval_path=str(sandbox / "human-approval.json"),
        external_identifier=None,
        receipt={"receipt_hash": "fixture"},
    )

    monkeypatch.setattr(publisher, "_load_state", lambda _: state)
    monkeypatch.setattr(publisher, "verify_approval", lambda _: {"ok": True})

    transport_called = False

    def transport(_):
        nonlocal transport_called
        transport_called = True
        return {"final_click_performed": True, "portal_confirmation_observed": True}

    with pytest.raises(PublicationBridgeError, match="external effect blocked"):
        publisher.publish(sandbox, transport=transport)

    assert transport_called is False
    assert not (sandbox / "submission-result.json").exists()
