from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

from app.evidence import evidence_receipt
from app.source_evidence import (
    EvidenceState,
    RepresentationType,
    SourceEvidence,
    SourceRepresentation,
    build_source_evidence,
)


Loader = Callable[[str], SourceRepresentation | None]

DEFAULT_FALLBACK_ORDER: tuple[RepresentationType, ...] = (
    RepresentationType.LIVE_HTML,
    RepresentationType.API_METADATA,
    RepresentationType.SAVED_HTML,
    RepresentationType.ARXIV_EPRINT_SOURCE,
    RepresentationType.LATEX_SOURCE,
    RepresentationType.SAVED_PDF,
    RepresentationType.SAVED_IMAGE,
    RepresentationType.SCREENSHOT,
    RepresentationType.DOCUMENT_PAGE_RENDER,
    RepresentationType.DOI_METADATA,
    RepresentationType.ORCID_SNAPSHOT,
    RepresentationType.REPOSITORY_FILE,
    RepresentationType.GIT_COMMIT,
    RepresentationType.HF_SNAPSHOT,
    RepresentationType.CORPUS_COPY,
    RepresentationType.GENERATED_IMAGE,
    RepresentationType.MODEL_REPORT,
)


@dataclass(frozen=True)
class ResolutionAttempt:
    kind: RepresentationType
    status: str
    reason: str = ""


@dataclass(frozen=True)
class SourceResolution:
    evidence: SourceEvidence
    attempts: tuple[ResolutionAttempt, ...]
    receipt: dict


def resolve_source(
    *,
    original_url: str,
    loaders: Mapping[RepresentationType, Loader],
    order: tuple[RepresentationType, ...] = DEFAULT_FALLBACK_ORDER,
    crosscheck_all: bool = True,
) -> SourceResolution:
    representations: list[SourceRepresentation] = []
    attempts: list[ResolutionAttempt] = []

    for kind in order:
        loader = loaders.get(kind)
        if loader is None:
            attempts.append(ResolutionAttempt(kind, "SKIP", "no loader configured"))
            continue
        try:
            representation = loader(original_url)
        except Exception as exc:
            attempts.append(ResolutionAttempt(kind, "ERROR", f"{type(exc).__name__}: {exc}"))
            continue

        if representation is None:
            attempts.append(ResolutionAttempt(kind, "MISS", "no representation"))
            continue

        if representation.kind is not kind:
            attempts.append(
                ResolutionAttempt(
                    kind,
                    "ERROR",
                    f"loader returned {representation.kind.value}, expected {kind.value}",
                )
            )
            continue

        representations.append(representation)
        attempts.append(ResolutionAttempt(kind, "HIT", representation.locator))

        if not crosscheck_all and kind == RepresentationType.LIVE_HTML:
            break

    evidence = build_source_evidence(original_url=original_url, representations=representations)
    receipt = evidence_receipt(
        "SOURCE_RESOLUTION",
        inputs={
            "original_url": original_url,
            "order": [kind.value for kind in order],
            "crosscheck_all": crosscheck_all,
        },
        outputs={
            "state": evidence.state.value,
            "resolved_url": evidence.resolved_url,
            "evidence_hash": evidence.evidence_hash,
            "evidence_tier": evidence.evidence_tier,
            "authority": dict(evidence.authority),
            "independent_evidence": evidence.independent_evidence,
            "official_version_evidence": evidence.official_version_evidence,
            "admissible": _evidence_is_admissible(evidence),
            "identifiers": dict(evidence.identifiers),
            "claimed_identifiers": {
                key: list(values) for key, values in evidence.claimed_identifiers.items()
            },
            "conflicts": [
                {
                    "code": conflict.code,
                    "field": conflict.field,
                    "blocking": conflict.blocking,
                    "values": conflict.values,
                }
                for conflict in evidence.conflicts
            ],
            "representations": [
                {
                    "kind": rep.kind.value,
                    "locator": rep.locator,
                    "content_hash": rep.content_hash,
                    "closure_complete": rep.metadata.get("closure_complete"),
                }
                for rep in evidence.representations
            ],
        },
    )
    return SourceResolution(evidence=evidence, attempts=tuple(attempts), receipt=receipt)


def _partial_root_is_admissible(rep: SourceRepresentation) -> bool:
    if rep.kind in {RepresentationType.GENERATED_IMAGE, RepresentationType.DOCUMENT_PAGE_RENDER}:
        return False
    if rep.metadata.get("generated") is True or rep.metadata.get("model_generated") is True:
        return False

    if rep.kind in {RepresentationType.LATEX_SOURCE, RepresentationType.ARXIV_EPRINT_SOURCE}:
        return rep.metadata.get("closure_complete") is True

    path = str(rep.metadata.get("path") or rep.locator).split("?", 1)[0].lower()
    if rep.kind is RepresentationType.REPOSITORY_FILE and path.endswith(".tex"):
        return rep.metadata.get("closure_complete") is True

    return True


def _evidence_is_admissible(evidence: SourceEvidence) -> bool:
    if evidence.state in {EvidenceState.VERIFIED, EvidenceState.VERIFIED_SNAPSHOT}:
        return evidence.independent_evidence
    if evidence.state is EvidenceState.PARTIAL:
        # Partial evidence may support explicitly bounded claims only when an
        # admissible independent root exists. An incomplete TeX closure is a
        # fragment: independent bytes, but not an admissible official source.
        return any(_partial_root_is_admissible(rep) for rep in evidence.representations)
    return False


def source_is_admissible(resolution: SourceResolution) -> bool:
    return _evidence_is_admissible(resolution.evidence)
