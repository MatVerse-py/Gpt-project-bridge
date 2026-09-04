from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.marxiv_runtime_publisher as publisher
from app.marxiv_runtime_publisher import MarxivPublisherError


def _object(manuscript_name: str = "paper.pdf") -> dict:
    return {
        "schema": "marxiv.scientific-object.v1",
        "object_id": "matverse-paper-001",
        "version": "v1",
        "title": "Governed Informational Transformation",
        "authors": [
            {
                "name": "Test Author",
                "orcid": "0000-0000-0000-0000",
                "affiliation": "Independent Research Fixture",
            }
        ],
        "abstract": "A reproducible fixture used to validate the MARXIV runtime publisher lifecycle.",
        "manuscript_file": manuscript_name,
        "keywords": ["governance", "reproducibility"],
        "claims": ["C1: publication authorization is package-bound"],
        "evidence_refs": ["evidence://fixture/001"],
        "parent_object_id": None,
        "publication": {
            "venue": "arxiv",
            "primary_archive": "cs",
            "primary_category": "cs.AI",
            "crosslist_archives": ["cs"],
            "crosslist_categories": ["cs.SE"],
            "license": "CC BY 4.0",
            "keep_all_files": False,
            "comments": "Runtime publisher fixture",
        },
    }


def _fake_prepare(manifest_path: Path, work_root: Path):
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    publication_id = manifest["publication_id"]
    workdir = work_root / publication_id
    workdir.mkdir(parents=True, exist_ok=True)
    subfile = workdir / "arxiv.sub"
    subfile.write_text("@venue: arxiv\ntitle: Governed Informational Transformation\n", encoding="utf-8")
    state_path = workdir / "publication-state.json"
    state_path.write_text(
        json.dumps(
            {
                "schema": "matverse.publication-state.v1",
                "publication_id": publication_id,
                "venue": "arxiv",
                "status": "VALIDATED",
                "manifest_hash": "fixture",
                "manuscript_sha256": "fixture",
                "subfile_path": str(subfile),
                "values_path": str(workdir / "values.json"),
                "receipt": {"receipt_hash": "fixture"},
            }
        ),
        encoding="utf-8",
    )
    return SimpleNamespace(publication_id=publication_id, subfile_path=str(subfile))


def _prepared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    monkeypatch.setattr(publisher, "prepare_arxiv", _fake_prepare)
    manuscript = tmp_path / "paper.pdf"
    manuscript.write_bytes(b"pdf-fixture")
    object_path = tmp_path / "scientific-object.json"
    object_path.write_text(json.dumps(_object()), encoding="utf-8")
    state = publisher.prepare_sandbox(object_path, tmp_path / ".marxiv")
    sandbox = tmp_path / ".marxiv" / state.object_id / state.version
    return sandbox, manuscript


def _approve(sandbox: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MARXIV_APPROVAL_SECRET", "fixture-secret-material-0123456789-abcdef")
    challenge = publisher.request_approval(sandbox)
    approval = publisher.approve(
        sandbox,
        approver_id="human-fixture",
        confirmation=publisher.required_confirmation(challenge),
    )
    return challenge, approval


def test_sandbox_organizes_scientific_object_and_publication_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox, _ = _prepared(tmp_path, monkeypatch)
    state = json.loads((sandbox / "publisher-state.json").read_text(encoding="utf-8"))
    review = json.loads((sandbox / "review-packet.json").read_text(encoding="utf-8"))
    manifest = json.loads((sandbox / "arxiv-manifest.json").read_text(encoding="utf-8"))

    assert state["status"] == "HUMAN_REVIEW_REQUIRED"
    assert state["package_hash"]
    assert review["claims"] == ["C1: publication authorization is package-bound"]
    assert review["authors"][0]["orcid"] == "0000-0000-0000-0000"
    assert manifest["primary_category"] == "cs.AI"
    assert manifest["crosslist_categories"] == ["cs.SE"]
    assert manifest["license"] == "CC BY 4.0"
    assert manifest["manuscript_file"] == "manuscript/paper.pdf"
    assert (sandbox / "manuscript" / "paper.pdf").read_bytes() == b"pdf-fixture"


def test_package_identity_is_stable_across_sandbox_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(publisher, "prepare_arxiv", _fake_prepare)
    manuscript = tmp_path / "paper.pdf"
    manuscript.write_bytes(b"pdf-fixture")
    object_path = tmp_path / "scientific-object.json"
    object_path.write_text(json.dumps(_object()), encoding="utf-8")

    first = publisher.prepare_sandbox(object_path, tmp_path / ".marxiv-a")
    second = publisher.prepare_sandbox(object_path, tmp_path / ".marxiv-b")

    assert first.object_hash == second.object_hash
    assert first.manifest_hash == second.manifest_hash
    assert first.manuscript_sha256 == second.manuscript_sha256
    assert first.arxiv_subfile_sha256 == second.arxiv_subfile_sha256
    assert first.review_packet_hash == second.review_packet_hash
    assert first.package_hash == second.package_hash


def test_human_approval_is_bound_to_exact_prepared_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox, _ = _prepared(tmp_path, monkeypatch)
    challenge = publisher.request_approval(sandbox)

    monkeypatch.setenv("MARXIV_APPROVAL_SECRET", "fixture-secret-material-0123456789-abcdef")
    with pytest.raises(MarxivPublisherError):
        publisher.approve(sandbox, "human-fixture", "OK")

    approval = publisher.approve(
        sandbox,
        "human-fixture",
        publisher.required_confirmation(challenge),
    )
    verification = publisher.verify_approval(sandbox)

    assert approval.package_hash == challenge.package_hash
    assert verification["ok"] is True


def test_manuscript_change_invalidates_existing_human_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox, _ = _prepared(tmp_path, monkeypatch)
    _approve(sandbox, monkeypatch)
    (sandbox / "manuscript" / "paper.pdf").write_bytes(b"changed-after-human-approval")

    verification = publisher.verify_approval(sandbox)

    assert verification["ok"] is False
    assert verification["checks"]["package_manuscript_hash_match"] is False


def test_publish_is_blocked_without_human_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox, _ = _prepared(tmp_path, monkeypatch)
    monkeypatch.setenv("MARXIV_APPROVAL_SECRET", "fixture-secret-material-0123456789-abcdef")
    monkeypatch.setattr(publisher, "authorize_login", lambda _: None)

    with pytest.raises(MarxivPublisherError):
        publisher.publish(
            sandbox,
            transport=lambda _: {
                "final_click_performed": True,
                "portal_confirmation_observed": True,
            },
        )


def test_approved_agent_can_publish_then_reconcile_external_identifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox, _ = _prepared(tmp_path, monkeypatch)
    _approve(sandbox, monkeypatch)
    monkeypatch.setattr(publisher, "authorize_login", lambda _: None)

    submitted = publisher.publish(
        sandbox,
        transport=lambda manifest: {
            "final_click_performed": True,
            "portal_confirmation_observed": True,
            "external_identifier": None,
            "manuscript_is_absolute_for_transport": Path(manifest.manuscript_file).is_absolute(),
        },
    )
    reconciled = publisher.reconcile(sandbox, "2609.12345")

    assert submitted.status == "SUBMITTED_TO_ARXIV"
    assert reconciled.status == "RECONCILED"
    assert reconciled.external_identifier == "2609.12345"
    assert reconciled.receipt["schema"] == "matverse.evidence-receipt.v1"


def test_unknown_result_after_final_click_holds_and_is_not_auto_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox, _ = _prepared(tmp_path, monkeypatch)
    _approve(sandbox, monkeypatch)
    monkeypatch.setattr(publisher, "authorize_login", lambda _: None)

    result = publisher.publish(
        sandbox,
        transport=lambda _: {
            "final_click_performed": True,
            "portal_confirmation_observed": False,
        },
    )

    assert result.status == "HOLD_RECONCILIATION_REQUIRED"
    with pytest.raises(MarxivPublisherError):
        publisher.publish(
            sandbox,
            transport=lambda _: {
                "final_click_performed": True,
                "portal_confirmation_observed": True,
            },
        )
