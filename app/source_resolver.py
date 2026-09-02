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
            "independent_evidence": evidence.independent_evidence,
            "admissible": _evidence_is_admissible(evidence),
            "identifiers": dict(evidence.identifiers),
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
                }
                for rep in evidence.representations
            ],
        },
    )
    return SourceResolution(evidence=evidence, attempts=tuple(attempts), receipt=receipt)


def _evidence_is_admissible(evidence: SourceEvidence) -> bool:
    if evidence.state in {EvidenceState.VERIFIED, EvidenceState.VERIFIED_SNAPSHOT}:
        return evidence.independent_evidence
    if evidence.state is EvidenceState.PARTIAL:
        # Partial can be admitted for bounded, explicitly partial use only when
        # at least one independent root exists. Generated-only and derivative-
        # only evidence remain non-admissible.
        return evidence.independent_evidence
    return False


def source_is_admissible(resolution: SourceResolution) -> bool:
    return _evidence_is_admissible(resolution.evidence)
