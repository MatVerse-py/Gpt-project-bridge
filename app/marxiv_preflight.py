from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.marxiv_runtime_publisher import MarxivScientificObject


class PreflightAuthor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2)
    orcid: str | None = None
    affiliation: str | None = None
    verified: bool = False


class PreflightPublicationIntent(BaseModel):
    model_config = ConfigDict(extra="allow")

    venue: Literal["arxiv"] = "arxiv"
    authors: list[PreflightAuthor] | None = None
    manuscript_file: str | None = None
    manuscript_confirmed: bool = False
    primary_archive: str | None = None
    primary_category: str | None = None
    crosslist_archives: list[str] = Field(default_factory=list)
    crosslist_categories: list[str] = Field(default_factory=list)
    license: str | None = None
    comments: str | None = None
    category_confirmed: bool = False
    crosslist_confirmed: bool = False
    license_confirmed: bool = False
    final_abstract_confirmed: bool = False


class PreflightContribution(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    status: str


class PaperPreflight(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema: Literal["marxiv.paper-preflight.v1"]
    status: str
    object_id: str
    version: str
    title: str
    abstract: str
    keywords: list[str] = Field(default_factory=list)
    contributions: list[PreflightContribution] = Field(default_factory=list)
    blocked_result_claims: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    publication_intent: PreflightPublicationIntent


class PreflightAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema: Literal["marxiv.preflight-assessment.v1"] = "marxiv.preflight-assessment.v1"
    object_id: str
    version: str
    status: Literal["READY_FOR_PROMOTION", "HOLD_PREPARE"]
    blockers: list[str]
    warnings: list[str]
    manuscript_resolved_path: str | None = None


def load_preflight(path: Path) -> PaperPreflight:
    return PaperPreflight.model_validate_json(path.read_text(encoding="utf-8"))


def assess(path: Path) -> PreflightAssessment:
    path = path.resolve()
    obj = load_preflight(path)
    intent = obj.publication_intent
    blockers: list[str] = []
    warnings: list[str] = []

    if not intent.authors:
        blockers.append("verified author list is missing")
    else:
        for index, author in enumerate(intent.authors, start=1):
            if not author.verified:
                blockers.append(f"author {index} is not marked verified")
            if author.orcid is None:
                warnings.append(f"author {index} has no ORCID")
            if author.affiliation is None:
                warnings.append(f"author {index} affiliation is intentionally unset")

    manuscript_path: Path | None = None
    if not intent.manuscript_file:
        blockers.append("manuscript file is missing")
    else:
        candidate = Path(intent.manuscript_file).expanduser()
        manuscript_path = candidate if candidate.is_absolute() else (path.parent / candidate).resolve()
        if not manuscript_path.is_file():
            blockers.append(f"manuscript file does not exist: {manuscript_path}")
        if not intent.manuscript_confirmed:
            blockers.append("manuscript has not been explicitly frozen/confirmed for publication")

    if not intent.primary_archive:
        blockers.append("arXiv primary archive is missing")
    if not intent.primary_category:
        blockers.append("arXiv primary category is missing")
    if not intent.category_confirmed:
        blockers.append("arXiv category has not been explicitly confirmed")

    if len(intent.crosslist_archives) != len(intent.crosslist_categories):
        blockers.append("cross-list archive/category lengths differ")
    if not intent.crosslist_confirmed:
        blockers.append("cross-list decision has not been explicitly confirmed")

    if not intent.license:
        blockers.append("publication license is missing")
    if not intent.license_confirmed:
        blockers.append("publication license has not been explicitly confirmed")
    if not intent.final_abstract_confirmed:
        blockers.append("final abstract has not been confirmed against the manuscript")

    return PreflightAssessment(
        object_id=obj.object_id,
        version=obj.version,
        status="READY_FOR_PROMOTION" if not blockers else "HOLD_PREPARE",
        blockers=blockers,
        warnings=warnings,
        manuscript_resolved_path=str(manuscript_path) if manuscript_path and manuscript_path.is_file() else None,
    )


def promote(preflight_path: Path, output_path: Path) -> MarxivScientificObject:
    preflight_path = preflight_path.resolve()
    assessment = assess(preflight_path)
    if assessment.status != "READY_FOR_PROMOTION":
        raise RuntimeError(f"preflight is not promotable: {assessment.blockers}")

    obj = load_preflight(preflight_path)
    intent = obj.publication_intent
    manuscript = Path(intent.manuscript_file or "")
    if not manuscript.is_absolute():
        manuscript = (preflight_path.parent / manuscript).resolve()

    scientific_object = MarxivScientificObject.model_validate(
        {
            "schema": "marxiv.scientific-object.v1",
            "object_id": obj.object_id,
            "version": obj.version,
            "title": obj.title,
            "authors": [
                {
                    "name": author.name,
                    "orcid": author.orcid,
                    "affiliation": author.affiliation,
                }
                for author in (intent.authors or [])
            ],
            "abstract": obj.abstract,
            "manuscript_file": str(manuscript),
            "keywords": obj.keywords,
            "claims": [f"{item.id}: {item.name}" for item in obj.contributions],
            "evidence_refs": obj.evidence_refs,
            "parent_object_id": None,
            "publication": {
                "venue": "arxiv",
                "primary_archive": intent.primary_archive,
                "primary_category": intent.primary_category,
                "crosslist_archives": intent.crosslist_archives,
                "crosslist_categories": intent.crosslist_categories,
                "license": intent.license,
                "comments": intent.comments,
            },
        }
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(scientific_object.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return scientific_object


def _main() -> int:
    parser = argparse.ArgumentParser(description="Assess or promote a MARXIV paper preflight object")
    sub = parser.add_subparsers(dest="command", required=True)

    assess_parser = sub.add_parser("assess")
    assess_parser.add_argument("--preflight", required=True)

    promote_parser = sub.add_parser("promote")
    promote_parser.add_argument("--preflight", required=True)
    promote_parser.add_argument("--output", required=True)

    args = parser.parse_args()
    if args.command == "assess":
        result = assess(Path(args.preflight))
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return 0 if result.status == "READY_FOR_PROMOTION" else 2

    result = promote(Path(args.preflight), Path(args.output))
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
