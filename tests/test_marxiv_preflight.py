from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.marxiv_preflight import assess, promote


def _preflight() -> dict:
    return {
        "schema": "marxiv.paper-preflight.v1",
        "status": "HOLD_PREPARE",
        "object_id": "matverse-2.0",
        "version": "v1",
        "title": "MATVERSE 2.0: A Constitutional Architecture for Governed Informational Transformation in Federated Research Systems",
        "abstract": "A sufficiently long abstract for validating the governed preflight promotion path.",
        "keywords": ["governance", "replay"],
        "contributions": [
            {"id": "C1", "name": "Constitutional Separation", "status": "SUPPORTED_BY_CORPUS"}
        ],
        "blocked_result_claims": ["MATVERSE demonstrates digital life"],
        "evidence_refs": ["evidence://matverse/reference-vertical-slice"],
        "publication_intent": {
            "venue": "arxiv",
            "authors": [
                {
                    "name": "Mateus Alves Arêas",
                    "orcid": "0009-0008-2973-4047",
                    "affiliation": None,
                    "verified": True,
                }
            ],
            "manuscript_file": None,
            "manuscript_confirmed": False,
            "primary_archive": None,
            "primary_category": None,
            "crosslist_archives": [],
            "crosslist_categories": [],
            "license": None,
            "category_confirmed": False,
            "crosslist_confirmed": False,
            "license_confirmed": False,
            "final_abstract_confirmed": False,
        },
    }


def test_incomplete_real_object_stays_hold(tmp_path: Path) -> None:
    path = tmp_path / "preflight.json"
    path.write_text(json.dumps(_preflight()), encoding="utf-8")

    result = assess(path)

    assert result.status == "HOLD_PREPARE"
    assert "manuscript file is missing" in result.blockers
    assert "arXiv primary category is missing" in result.blockers
    assert "publication license is missing" in result.blockers


def test_existing_but_unfrozen_manuscript_stays_hold(tmp_path: Path) -> None:
    payload = _preflight()
    manuscript = tmp_path / "paper.tex"
    manuscript.write_text("candidate", encoding="utf-8")
    payload["publication_intent"]["manuscript_file"] = "paper.tex"
    path = tmp_path / "preflight.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = assess(path)

    assert result.status == "HOLD_PREPARE"
    assert "manuscript has not been explicitly frozen/confirmed for publication" in result.blockers
    assert result.manuscript_resolved_path == str(manuscript.resolve())


def test_complete_preflight_promotes_without_external_effect(tmp_path: Path) -> None:
    payload = _preflight()
    manuscript = tmp_path / "paper.pdf"
    manuscript.write_bytes(b"final-manuscript-fixture")
    intent = payload["publication_intent"]
    intent.update(
        {
            "manuscript_file": "paper.pdf",
            "manuscript_confirmed": True,
            "primary_archive": "cs",
            "primary_category": "cs.SE",
            "crosslist_archives": ["cs"],
            "crosslist_categories": ["cs.AI"],
            "license": "CC BY 4.0",
            "category_confirmed": True,
            "crosslist_confirmed": True,
            "license_confirmed": True,
            "final_abstract_confirmed": True,
        }
    )
    path = tmp_path / "preflight.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    assessment = assess(path)
    output = tmp_path / "scientific-object.json"
    scientific_object = promote(path, output)

    assert assessment.status == "READY_FOR_PROMOTION"
    assert output.is_file()
    assert scientific_object.authors[0].name == "Mateus Alves Arêas"
    assert scientific_object.authors[0].orcid == "0009-0008-2973-4047"
    assert scientific_object.manuscript_file == "paper.pdf"
    assert scientific_object.publication.primary_category == "cs.SE"
    assert scientific_object.publication.crosslist_categories == ["cs.AI"]
    assert scientific_object.publication.license == "CC BY 4.0"


def test_unverified_author_blocks_promotion(tmp_path: Path) -> None:
    payload = _preflight()
    payload["publication_intent"]["authors"][0]["verified"] = False
    path = tmp_path / "preflight.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = assess(path)

    assert result.status == "HOLD_PREPARE"
    assert "author 1 is not marked verified" in result.blockers
    with pytest.raises(RuntimeError):
        promote(path, tmp_path / "scientific-object.json")
