from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
from typing import Any, Iterable, Mapping
import json
import re


class RepresentationType(str, Enum):
    LIVE_HTML = "LIVE_HTML"
    API_METADATA = "API_METADATA"
    SAVED_HTML = "SAVED_HTML"
    SAVED_PDF = "SAVED_PDF"
    SAVED_IMAGE = "SAVED_IMAGE"
    SCREENSHOT = "SCREENSHOT"
    GENERATED_IMAGE = "GENERATED_IMAGE"
    DOI_METADATA = "DOI_METADATA"
    ORCID_SNAPSHOT = "ORCID_SNAPSHOT"
    REPOSITORY_FILE = "REPOSITORY_FILE"
    GIT_COMMIT = "GIT_COMMIT"
    HF_SNAPSHOT = "HF_SNAPSHOT"
    CORPUS_COPY = "CORPUS_COPY"
    MODEL_REPORT = "MODEL_REPORT"


class EvidenceState(str, Enum):
    VERIFIED = "VERIFIED"
    VERIFIED_SNAPSHOT = "VERIFIED_SNAPSHOT"
    PARTIAL = "PARTIAL"
    CONFLICT = "CONFLICT"
    HOLD_AUTHORITY = "HOLD_AUTHORITY"
    HOLD_SEMANTICS = "HOLD_SEMANTICS"
    UNAVAILABLE_AFTER_FALLBACK = "UNAVAILABLE_AFTER_FALLBACK"
    BLOCK_TAMPERED = "BLOCK_TAMPERED"


STRUCTURED_REPRESENTATIONS = {
    RepresentationType.LIVE_HTML,
    RepresentationType.API_METADATA,
    RepresentationType.SAVED_HTML,
    RepresentationType.DOI_METADATA,
    RepresentationType.ORCID_SNAPSHOT,
    RepresentationType.REPOSITORY_FILE,
    RepresentationType.GIT_COMMIT,
    RepresentationType.HF_SNAPSHOT,
}

IMAGE_REPRESENTATIONS = {
    RepresentationType.SAVED_IMAGE,
    RepresentationType.SCREENSHOT,
    RepresentationType.GENERATED_IMAGE,
}

REPRESENTATION_PRIORITY: dict[RepresentationType, int] = {
    RepresentationType.API_METADATA: 100,
    RepresentationType.DOI_METADATA: 95,
    RepresentationType.GIT_COMMIT: 95,
    RepresentationType.LIVE_HTML: 90,
    RepresentationType.SAVED_HTML: 85,
    RepresentationType.ORCID_SNAPSHOT: 85,
    RepresentationType.REPOSITORY_FILE: 80,
    RepresentationType.HF_SNAPSHOT: 75,
    RepresentationType.SAVED_PDF: 70,
    RepresentationType.SAVED_IMAGE: 60,
    RepresentationType.SCREENSHOT: 55,
    RepresentationType.CORPUS_COPY: 50,
    RepresentationType.MODEL_REPORT: 10,
    RepresentationType.GENERATED_IMAGE: 0,
}

TIER_BY_PRIORITY = (
    (95, "P5"),
    (85, "P4"),
    (75, "P3"),
    (60, "P2"),
    (40, "P1"),
    (0, "P0"),
)

IDENTIFIER_KEYS = ("doi", "orcid", "repo", "commit_sha", "canonical_url", "title", "author")

_DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b")


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256_text(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def normalize_identifier(key: str, value: str) -> str:
    value = value.strip()
    if key == "doi":
        value = value.removeprefix("https://doi.org/").removeprefix("http://doi.org/")
        return value.lower()
    if key == "orcid":
        value = value.removeprefix("https://orcid.org/").removeprefix("http://orcid.org/")
        return value.upper()
    if key in {"canonical_url", "repo"}:
        return value.rstrip("/")
    if key in {"title", "author"}:
        return " ".join(value.split()).casefold()
    return value


@dataclass(frozen=True)
class SourceRepresentation:
    kind: RepresentationType
    locator: str
    content_hash: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
    captured_at: str | None = None

    @classmethod
    def from_text(
        cls,
        *,
        kind: RepresentationType,
        locator: str,
        content: str,
        metadata: Mapping[str, Any] | None = None,
        captured_at: str | None = None,
    ) -> "SourceRepresentation":
        return cls(
            kind=kind,
            locator=locator,
            content_hash=sha256_text(content),
            metadata=dict(metadata or {}),
            captured_at=captured_at,
        )

    @classmethod
    def from_bytes(
        cls,
        *,
        kind: RepresentationType,
        locator: str,
        content: bytes,
        metadata: Mapping[str, Any] | None = None,
        captured_at: str | None = None,
        expected_sha256: str | None = None,
    ) -> "SourceRepresentation":
        digest = sha256_bytes(content)
        merged_metadata = dict(metadata or {})
        if expected_sha256 is not None:
            merged_metadata["tampered"] = digest != expected_sha256
            merged_metadata["expected_sha256"] = expected_sha256
        merged_metadata.setdefault("size_bytes", len(content))
        return cls(
            kind=kind,
            locator=locator,
            content_hash=digest,
            metadata=merged_metadata,
            captured_at=captured_at,
        )


@dataclass(frozen=True)
class EvidenceConflict:
    code: str
    field: str
    values: tuple[str, ...]
    blocking: bool
    detail: str


@dataclass(frozen=True)
class SourceEvidence:
    original_url: str
    resolved_url: str | None
    representations: tuple[SourceRepresentation, ...]
    identifiers: Mapping[str, str]
    conflicts: tuple[EvidenceConflict, ...]
    state: EvidenceState
    evidence_tier: str
    evidence_hash: str

    @property
    def independent_evidence(self) -> bool:
        if not self.representations:
            return False
        return any(
            rep.kind is not RepresentationType.GENERATED_IMAGE
            and rep.metadata.get("generated") is not True
            for rep in self.representations
        )


def _tier(representations: Iterable[SourceRepresentation]) -> str:
    highest = max((REPRESENTATION_PRIORITY[r.kind] for r in representations), default=0)
    for threshold, tier in TIER_BY_PRIORITY:
        if highest >= threshold:
            return tier
    return "P0"


def _collect_values(
    representations: Iterable[SourceRepresentation],
    key: str,
) -> list[tuple[int, RepresentationType, str]]:
    values: list[tuple[int, RepresentationType, str]] = []
    for rep in representations:
        raw = rep.metadata.get(key)
        if raw is None:
            continue
        raw_values = raw if isinstance(raw, (list, tuple, set)) else [raw]
        for item in raw_values:
            if not isinstance(item, str) or not item.strip():
                continue
            values.append((REPRESENTATION_PRIORITY[rep.kind], rep.kind, item.strip()))
    return values


def _resolve_identifier(
    representations: tuple[SourceRepresentation, ...],
    key: str,
) -> tuple[str | None, list[EvidenceConflict]]:
    observed = _collect_values(representations, key)
    if not observed:
        return None, []

    normalized_to_raw: dict[str, list[tuple[int, RepresentationType, str]]] = {}
    for priority, kind, raw in observed:
        normalized_to_raw.setdefault(normalize_identifier(key, raw), []).append((priority, kind, raw))

    ranked = sorted(
        normalized_to_raw.items(),
        key=lambda item: max(v[0] for v in item[1]),
        reverse=True,
    )
    winner_norm, winner_entries = ranked[0]
    winner_raw = max(winner_entries, key=lambda item: item[0])[2]
    winner_priority = max(item[0] for item in winner_entries)

    conflicts: list[EvidenceConflict] = []
    for other_norm, other_entries in ranked[1:]:
        other_priority = max(item[0] for item in other_entries)
        if other_norm == winner_norm:
            continue

        winner_structured = any(kind in STRUCTURED_REPRESENTATIONS for _, kind, _ in winner_entries)
        other_structured = any(kind in STRUCTURED_REPRESENTATIONS for _, kind, _ in other_entries)
        winner_image = any(kind in IMAGE_REPRESENTATIONS for _, kind, _ in winner_entries)
        other_image = any(kind in IMAGE_REPRESENTATIONS for _, kind, _ in other_entries)

        structured_conflict = winner_structured and other_structured
        image_structured_conflict = (winner_structured and other_image) or (winner_image and other_structured)
        blocking = structured_conflict and other_priority >= 75 and winner_priority >= 75

        if image_structured_conflict:
            code = "IMAGE_METADATA_CONFLICT"
            detail = f"Visual {key} disagrees with structured metadata; structured metadata retains identity precedence"
        else:
            code = "IDENTIFIER_CONFLICT" if blocking else "LOWER_PRIORITY_DISAGREEMENT"
            detail = f"Conflicting {key} values across source representations"

        conflicts.append(
            EvidenceConflict(
                code=code,
                field=key,
                values=(winner_raw, max(other_entries, key=lambda item: item[0])[2]),
                blocking=blocking,
                detail=detail,
            )
        )

    return winner_raw, conflicts


def _detect_stale_prose(representations: tuple[SourceRepresentation, ...], identifiers: Mapping[str, str]) -> list[EvidenceConflict]:
    conflicts: list[EvidenceConflict] = []
    resolved_doi = identifiers.get("doi")
    if not resolved_doi:
        return conflicts

    for rep in representations:
        description = rep.metadata.get("description")
        if not isinstance(description, str):
            continue
        lower = description.casefold()
        if "doi" in lower and ("será atribuído" in lower or "will be assigned" in lower or "doi pending" in lower):
            conflicts.append(
                EvidenceConflict(
                    code="STALE_PROSE",
                    field="doi",
                    values=(description, resolved_doi),
                    blocking=False,
                    detail="Human-readable description is stale; structured DOI metadata takes precedence",
                )
            )
            continue
        found = _DOI_RE.search(description)
        if found and normalize_identifier("doi", found.group(0)) != normalize_identifier("doi", resolved_doi):
            conflicts.append(
                EvidenceConflict(
                    code="STALE_PROSE",
                    field="doi",
                    values=(found.group(0), resolved_doi),
                    blocking=False,
                    detail="Description DOI disagrees with higher-priority structured DOI metadata",
                )
            )
    return conflicts


def _has_unverified_external_image_claim(representations: tuple[SourceRepresentation, ...]) -> bool:
    for rep in representations:
        if rep.kind not in IMAGE_REPRESENTATIONS:
            continue
        claims = rep.metadata.get("external_claims")
        verified = rep.metadata.get("independently_verified_claims")
        if not claims:
            continue
        claim_set = set(claims if isinstance(claims, (list, tuple, set)) else [claims])
        verified_set = set(verified if isinstance(verified, (list, tuple, set)) else ([verified] if verified else []))
        if claim_set - verified_set:
            return True
    return False


def build_source_evidence(
    *,
    original_url: str,
    representations: Iterable[SourceRepresentation],
) -> SourceEvidence:
    reps = tuple(representations)
    if not reps:
        payload = {"original_url": original_url, "state": EvidenceState.UNAVAILABLE_AFTER_FALLBACK.value}
        return SourceEvidence(
            original_url=original_url,
            resolved_url=None,
            representations=(),
            identifiers={},
            conflicts=(),
            state=EvidenceState.UNAVAILABLE_AFTER_FALLBACK,
            evidence_tier="P0",
            evidence_hash=sha256_text(canonical_json(payload)),
        )

    if any(rep.metadata.get("tampered") is True for rep in reps):
        payload = {
            "original_url": original_url,
            "representations": [rep.content_hash for rep in reps],
            "state": EvidenceState.BLOCK_TAMPERED.value,
        }
        return SourceEvidence(
            original_url=original_url,
            resolved_url=None,
            representations=reps,
            identifiers={},
            conflicts=(
                EvidenceConflict(
                    code="TAMPER_SIGNAL",
                    field="content",
                    values=tuple(rep.locator for rep in reps if rep.metadata.get("tampered") is True),
                    blocking=True,
                    detail="At least one representation is marked as tampered",
                ),
            ),
            state=EvidenceState.BLOCK_TAMPERED,
            evidence_tier=_tier(reps),
            evidence_hash=sha256_text(canonical_json(payload)),
        )

    identifiers: dict[str, str] = {}
    conflicts: list[EvidenceConflict] = []
    for key in IDENTIFIER_KEYS:
        value, field_conflicts = _resolve_identifier(reps, key)
        if value is not None:
            identifiers[key] = value
        conflicts.extend(field_conflicts)

    conflicts.extend(_detect_stale_prose(reps, identifiers))
    blocking = any(c.blocking for c in conflicts)

    only_generated_images = all(
        rep.kind is RepresentationType.GENERATED_IMAGE or rep.metadata.get("generated") is True
        for rep in reps
    )

    if blocking:
        state = EvidenceState.CONFLICT
    elif only_generated_images:
        state = EvidenceState.PARTIAL
    elif _has_unverified_external_image_claim(reps) and not any(rep.kind in STRUCTURED_REPRESENTATIONS for rep in reps):
        state = EvidenceState.PARTIAL
    elif any(rep.kind == RepresentationType.LIVE_HTML for rep in reps):
        state = EvidenceState.VERIFIED
    elif any(rep.kind in STRUCTURED_REPRESENTATIONS for rep in reps):
        state = EvidenceState.VERIFIED_SNAPSHOT
    else:
        state = EvidenceState.PARTIAL

    resolved_url = identifiers.get("canonical_url")
    if not resolved_url:
        best = max(reps, key=lambda rep: REPRESENTATION_PRIORITY[rep.kind])
        resolved_url = best.locator

    evidence_payload = {
        "original_url": original_url,
        "resolved_url": resolved_url,
        "representations": [
            {
                "kind": rep.kind.value,
                "locator": rep.locator,
                "content_hash": rep.content_hash,
                "captured_at": rep.captured_at,
                "metadata": dict(rep.metadata),
            }
            for rep in reps
        ],
        "identifiers": identifiers,
        "conflicts": [
            {
                "code": c.code,
                "field": c.field,
                "values": c.values,
                "blocking": c.blocking,
                "detail": c.detail,
            }
            for c in conflicts
        ],
        "state": state.value,
        "tier": _tier(reps),
    }
    return SourceEvidence(
        original_url=original_url,
        resolved_url=resolved_url,
        representations=reps,
        identifiers=identifiers,
        conflicts=tuple(conflicts),
        state=state,
        evidence_tier=_tier(reps),
        evidence_hash=sha256_text(canonical_json(evidence_payload)),
    )
