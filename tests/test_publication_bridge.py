from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.publication_bridge import (
    ArxivManifest,
    PublicationBridgeError,
    authorize_login,
    build_values,
    prepare,
    verify,
)


def _manifest(manuscript_name: str = "paper.pdf") -> dict:
    return {
        "schema": "matverse.publication-bridge.v1",
        "publication_id": "matverse-paper-001",
        "manuscript_file": manuscript_name,
        "primary_archive": "cs",
        "primary_category": "cs.AI",
        "crosslist_archives": ["cs"],
        "crosslist_categories": ["cs.SE"],
        "license": "CC BY 4.0",
        "title": "Governed Informational Transformation",
        "authors": ["Mateus Alves Areas"],
        "abstract": "A reproducible test manuscript used to validate the publication bridge.",
        "comments": "Test fixture only",
    }


def _fake_paperpush(tmp_path: Path) -> Path:
    executable = tmp_path / "paperpush-fake"
    executable.write_text(
        """#!/usr/bin/env python3
import os
import pathlib
import sys

args = sys.argv[1:]
if args[:2] == ['subfile', 'arxiv']:
    pathlib.Path('arxiv.sub').write_text('@venue: arxiv\\n', encoding='utf-8')
    raise SystemExit(0)
if args[:3] == ['login', 'arxiv', '--status']:
    print('Logged in to: arxiv: fixture-user')
    raise SystemExit(0)
if args[:2] == ['login', 'arxiv']:
    if not os.getenv('PAPERPUSH_USERNAME') or not os.getenv('PAPERPUSH_PASSWORD'):
        raise SystemExit(8)
    raise SystemExit(0)
if args and args[0] in {'autofill', 'validate', 'submit'}:
    raise SystemExit(0)
raise SystemExit(9)
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def test_manifest_forbids_secret_fields() -> None:
    raw = _manifest()
    raw["password"] = "must-never-enter-manifest"
    with pytest.raises(ValidationError):
        ArxivManifest.model_validate(raw)


def test_crosslist_lengths_must_match() -> None:
    raw = _manifest()
    raw["crosslist_categories"] = []
    with pytest.raises(ValidationError):
        ArxivManifest.model_validate(raw)


def test_build_values_preserves_explicit_license_and_author(tmp_path: Path) -> None:
    manuscript = tmp_path / "paper.pdf"
    manuscript.write_bytes(b"pdf-fixture")
    manifest = ArxivManifest.model_validate(_manifest())
    values = build_values(manifest, manuscript)
    by_id = {item["id"]: item for item in values["fields"]}
    assert by_id["license"]["value"] == "CC BY 4.0"
    assert by_id["authors"]["value"] == "Mateus Alves Areas"
    assert values["unfilled"][0]["id"] == "final_submission_confirmation"


def test_prepare_and_verify_are_deterministic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _fake_paperpush(tmp_path)
    monkeypatch.setenv("PAPERPUSH_BIN", str(fake))

    manuscript = tmp_path / "paper.pdf"
    manuscript.write_bytes(b"pdf-fixture")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_manifest()), encoding="utf-8")

    state = prepare(manifest_path, tmp_path / "work")
    state_path = tmp_path / "work" / "matverse-paper-001" / "publication-state.json"

    assert state.status == "VALIDATED"
    assert state.receipt["schema"] == "matverse.evidence-receipt.v1"
    result = verify(state_path, manifest_path)
    assert result["ok"] is True

    manuscript.write_bytes(b"tampered")
    result_after_tamper = verify(state_path, manifest_path)
    assert result_after_tamper["ok"] is False
    assert result_after_tamper["checks"]["manuscript_hash_match"] is False


def test_login_credentials_are_environment_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _fake_paperpush(tmp_path)
    monkeypatch.setenv("PAPERPUSH_BIN", str(fake))
    monkeypatch.setenv("ARXIV_EMAIL", "author@example.org")
    monkeypatch.setenv("ARXIV_PASSWORD", "super-secret-fixture")

    authorize_login(tmp_path)

    for path in tmp_path.rglob("*"):
        if path.is_file():
            content = path.read_text(encoding="utf-8", errors="ignore")
            assert "super-secret-fixture" not in content
            assert "author@example.org" not in content


def test_partial_credentials_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _fake_paperpush(tmp_path)
    monkeypatch.setenv("PAPERPUSH_BIN", str(fake))
    monkeypatch.setenv("ARXIV_EMAIL", "author@example.org")
    monkeypatch.delenv("ARXIV_PASSWORD", raising=False)
    with pytest.raises(PublicationBridgeError):
        authorize_login(tmp_path)
