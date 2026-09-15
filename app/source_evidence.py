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
    LATEX_SOURCE = "LATEX_SOURCE"
    ARXIV_EPRINT_SOURCE = "ARXIV_EPRINT_SOURCE"
    SAVED_PDF = "SAVED_PDF"
    SAVED_IMAGE = "SAVED_IMAGE"
    SCREENSHOT = "SCREENSHOT"
    DOCUMENT_PAGE_RENDER = "DOCUMENT_PAGE_RENDER"
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
    RepresentationType.LATEX_SOURCE,
    RepresentationType.ARXIV_EPRINT_SOURCE,
    RepresentationType.DOI_METADATA,
    RepresentationType.ORCID_SNAPSHOT,
    RepresentationType.REPOSITORY_FILE,
    RepresentationType.GIT_COMMIT,
    RepresentationType.HF_SNAPSHOT,
}

IMAGE_REPRESENTATIONS = {
    RepresentationType.SAVED_IMAGE,
    RepresentationType.SCREENSHOT,
    RepresentationType.DOCUMENT_PAGE_RENDER,
    RepresentationType.GENERATED_IMAGE,
}

# Compatibility ranking used for fallback/tier summaries. Claim authority is
# modeled separately below as a vector. A scalar tier must never be read as
# universal truth strength.
REPRESENTATION_PRIORITY: dict[RepresentationType, int] = {
    RepresentationType.API_METADATA: 100,
    RepresentationType.DOI_METADATA: 95,
    RepresentationType.GIT_COMMIT: 95,
    RepresentationType.ARXIV_EPRINT_SOURCE: 90,
    RepresentationType.LIVE_HTML: 90,
    RepresentationType.SAVED_HTML: 85,
    RepresentationType.ORCID_SNAPSHOT: 85,
    RepresentationType.LATEX_SOURCE: 80,
    RepresentationType.REPOSITORY_FILE: 80,
    RepresentationType.HF_SNAPSHOT: 75,
    RepresentationType.SAVED_PDF: 70,
    RepresentationType.SAVED_IMAGE: 60,
    RepresentationType.SCREENSHOT: 55,
    RepresentationType.CORPUS_COPY: 50,
    RepresentationType.DOCUMENT_PAGE_RENDER: 45,
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

IDENTIFIER_KEYS = (
    "doi",
    "orcid",
    "repo",
    "commit_sha",
    "canonical_url",
    "title",
    "author",
    "version",
)

AUTHORITY_DOMAINS = (
    "content",
    "version",
    "authorship",
    "publication",
    "timestamp",
    "execution",
)

# Policy weights, NOT probabilities and NOT scientific confidence scores.
# They exist to prevent one scalar evidence tier from leaking authority across
# unrelated predicates (e.g. TeX content authority becoming publication proof).
AUTHORITY_BY_REPRESENTATION: dict[RepresentationType, dict[str, float]] = {
    RepresentationType.LATEX_SOURCE: {
        "content": 1.00,
        "version": 1.00,
        "authorship": 0.30,
        "publication": 0.00,
        "timestamp": 0.00,
        "execution": 0.00,
    },
    RepresentationType.ARXIV_EPRINT_SOURCE: {
        "content": 1.00,
        "version": 1.00,
        "authorship": 0.60,
        "publication": 0.70,
        "timestamp": 0.90,
        "execution": 0.00,
    },
    RepresentationType.SAVED_PDF: {
        "content": 0.80,
        "version": 0.60,
        "authorship": 0.30,
        "publication": 0.00,
        "timestamp": 0.00,
        "execution": 0.00,
    },
    RepresentationType.DOI_METADATA: {
        "content": 0.20,
        "version": 0.70,
        "authorship": 0.60,
        "publication": 1.00,
        "timestamp": 0.90,
        "execution": 0.00,
    },
    RepresentationType.GIT_COMMIT: {
        "content": 0.90,
        "version": 1.00,
        "authorship": 0.60,
        "publication": 0.00,
        "timestamp": 0.85,
        "execution": 0.00,
    },
    RepresentationType.API_METADATA: {
        "content": 0.25,
        "version": 0.75,
        "authorship": 0.65,
        "publication": 0.90,
        "timestamp": 0.80,
        "execution": 0.00,
    },
    RepresentationType.LIVE_HTML: {
        "content": 0.80,
        "version": 0.55,
        "authorship": 0.40,
        "publication": 0.70,
        "timestamp": 0.50,
        "execution": 0.00,
    },
    RepresentationType.SAVED_HTML: {
        "content": 0.75,
        "version": 0.55,
        "authorship": 0.40,
        "publication": 0.55,
        "timestamp": 0.45,
        "execution": 0.00,
    },
    RepresentationType.ORCID_SNAPSHOT: {
        "content": 0.10,
        "version": 0.20,
        "authorship": 0.90,
        "publication": 0.40,
        "timestamp": 0.80,
        "execution": 0.00,
    },
    RepresentationType.REPOSITORY_FILE: {
        "content": 0.80,
        "version": 0.80,
        "authorship": 0.35,
        "publication": 0.00,
        "timestamp": 0.30,
        "execution": 0.00,
    },
    RepresentationType.HF_SNAPSHOT: {
        "content": 0.70,
        "version": 0.70,
        "authorship": 0.35,
        "publication": 0.45,
        "timestamp": 0.60,
        "execution": 0.00,
    },
    RepresentationType.CORPUS_COPY: {
        "content": 0.50,
        "version": 0.30,
        "authorship": 0.20,
        "publication": 0.00,
        "timestamp": 0.10,
        "execution": 0.00,
    },
    RepresentationType.SAVED_IMAGE: {
        "content": 0.35,
        "version": 0.10,
        "authorship": 0.05,
        "publication": 0.00,
        "timestamp": 0.05,
        "execution": 0.00,
    },
    RepresentationType.SCREENSHOT: {
        "content": 0.30,
        "version": 0.05,
        "authorship": 0.05,
        "publication": 0.00,
        "timestamp": 0.05,
        "execution": 0.00,
    },
    RepresentationType.DOCUMENT_PAGE_RENDER: {
        "content": 0.00,
        "version": 0.00,
        "authorship": 0.00,
        "publication": 0.00,
        "timestamp": 0.00,
        "execution": 0.00,
    },
    RepresentationType.GENERATED_IMAGE: {
        "content": 0.00,
        "version": 0.00,
        "authorship": 0.00,
        "publication": 0.00,
        "timestamp": 0.00,
        "execution": 0.00,
    },
    RepresentationType.MODEL_REPORT: {
        "content": 0.05,
        "version": 0.00,
        "authorship": 0.00,
        "publication": 0.00,
        "timestamp": 0.00,
        "execution": 0.00,
    },
}

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
    if key in {"title", "author", "version"}:
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
    claimed_identifiers: Mapping[str, tuple[str, ...]]
    authority: Mapping[str, float]
    conflicts: tuple[EvidenceConflict, ...]
    state: EvidenceState
    evidence_tier: str
    evidence_hash: str

    @property
    def independent_evidence(self) -> bool:
        if not self.representations:
            return False
        return any(_representation_is_independent(rep) for rep in self.representations)

    @property
    def official_version_evidence(self) -> bool:
        """True when an official TeX source has complete closure + verified anchor.

        Scope is intentionally narrow: source/version identity only. This does
        not prove external publication, peer review, reproduction or scientific
        validity.
        """
        return any(_representation_is_official_version_source(rep) for rep in self.representations)


def _representation_is_generated(rep: SourceRepresentation) -> bool:
    return (
        rep.kind is RepresentationType.GENERATED_IMAGE
        or rep.metadata.get("generated") is True
        or rep.metadata.get("model_generated") is True
    )


def _representation_is_derivative(rep: SourceRepresentation) -> bool:
    return rep.kind is RepresentationType.DOCUMENT_PAGE_RENDER


def _representation_is_latex(rep: SourceRepresentation) -> bool:
    if rep.kind in {RepresentationType.LATEX_SOURCE, RepresentationType.ARXIV_EPRINT_SOURCE}:
        return True
    path = str(rep.metadata.get("path") or rep.locator).split("?", 1)[0].lower()
    return rep.kind is RepresentationType.REPOSITORY_FILE and path.endswith(".tex")


def _representation_has_complete_latex_closure(rep: SourceRepresentation) -> bool:
    if not _representation_is_latex(rep):
        return True
    return rep.metadata.get("closure_complete") is True


def _representation_declares_official_version(rep: SourceRepresentation) -> bool:
    return _representation_is_latex(rep) and rep.metadata.get("official_version") is True


def _representation_has_verified_version_anchor(rep: SourceRepresentation) -> bool:
    """Require verification of the anchor, not merely an anchor-shaped string."""
    if rep.metadata.get("signature_verified") is True:
        return True

    anchor_checks = (
        ("commit_sha", "commit_verified"),
        ("source_commit", "commit_verified"),
        ("release_tag", "tag_verified"),
        ("manifest_sha256", "manifest_verified"),
        ("canonical_url", "canonical_verified"),
    )
    for anchor_key, verified_key in anchor_checks:
        value = rep.metadata.get(anchor_key)
        if isinstance(value, str) and value.strip() and rep.metadata.get(verified_key) is True:
            return True
    return False


def _representation_has_verified_external_custody(rep: SourceRepresentation) -> bool:
    if rep.kind is not RepresentationType.ARXIV_EPRINT_SOURCE:
        return False
    return (
        rep.metadata.get("external_timestamp_verified") is True
        and (
            rep.metadata.get("canonical_verified") is True
            or rep.metadata.get("signature_verified") is True
        )
    )


def _representation_is_official_version_source(rep: SourceRepresentation) -> bool:
    return (
        _representation_declares_official_version(rep)
        and _representation_has_complete_latex_closure(rep)
        and _representation_has_verified_version_anchor(rep)
    )


def _representation_is_independent(rep: SourceRepresentation) -> bool:
    return not _representation_is_generated(rep) and not _representation_is_derivative(rep)


def _effective_priority(rep: SourceRepresentation) -> int:
    # Provenance dominates appearance. Generated/derivative material cannot gain
    # authority from presentation. Incomplete TeX is a fragment and is capped at
    # P1 until the transitive source closure is resolved.
    if _representation_is_generated(rep):
        return 0
    if _representation_is_derivative(rep):
        return min(REPRESENTATION_PRIORITY[rep.kind], 45)
    if _representation_is_latex(rep) and not _representation_has_complete_latex_closure(rep):
        return 50
    if _representation_is_official_version_source(rep):
        return 95
    return REPRESENTATION_PRIORITY[rep.kind]


def _tier(representations: Iterable[SourceRepresentation]) -> str:
    highest = max((_effective_priority(r) for r in representations), default=0)
    for threshold, tier in TIER_BY_PRIORITY:
        if highest >= threshold:
            return tier
    return "P0"


def _representation_authority(rep: SourceRepresentation) -> dict[str, float]:
    zero = {domain: 0.0 for domain in AUTHORITY_DOMAINS}
    if not _representation_is_independent(rep):
        return zero
    if _representation_is_latex(rep) and not _representation_has_complete_latex_closure(rep):
        return zero

    base = AUTHORITY_BY_REPRESENTATION.get(rep.kind, zero)
    if rep.kind is RepresentationType.ARXIV_EPRINT_SOURCE and not _representation_has_verified_external_custody(rep):
        # Without verified third-party custody, an arXiv-shaped source is only a
        # TeX source. Naming/URL shape cannot manufacture publication authority.
        base = AUTHORITY_BY_REPRESENTATION[RepresentationType.LATEX_SOURCE]
    return {domain: float(base.get(domain, 0.0)) for domain in AUTHORITY_DOMAINS}


def _aggregate_authority(representations: Iterable[SourceRepresentation]) -> dict[str, float]:
    # Use max, not probabilistic noisy-OR. Derivational independence is not the
    # same as statistical independence, so multiplying evidence weights would
    # create false precision. Multiple corroborators may be recorded separately,
    # but the policy authority ceiling for a domain is the strongest independent
    # admissible representation.
    out = {domain: 0.0 for domain in AUTHORITY_DOMAINS}
    for rep in representations:
        vector = _representation_authority(rep)
        for domain in AUTHORITY_DOMAINS:
            out[domain] = max(out[domain], vector[domain])
    return out


def _collect_values(
    representations: Iterable[SourceRepresentation],
    key: str,
) -> list[tuple[int, RepresentationType, str]]:
    values: list[tuple[int, RepresentationType, str]] = []
    for rep in representations:
        if _representation_is_generated(rep):
            continue

        # DOI/ORCID text inside TeX remains a CLAIMED_IDENTIFIER. Resolution must
        # come from an independent metadata/identity source.
        if _representation_is_latex(rep) and key in {"doi", "orcid"}:
            continue

        raw = rep.metadata.get(key)
        if raw is None:
            continue
        raw_values = raw if isinstance(raw, (list, tuple, set)) else [raw]
        for item in raw_values:
            if not isinstance(item, str) or not item.strip():
                continue
            values.append((_effective_priority(rep), rep.kind, item.strip()))
    return values


def _collect_claimed_identifiers(representations: Iterable[SourceRepresentation]) -> dict[str, tuple[str, ...]]:
    collected: dict[str, set[str]] = {}
    for rep in representations:
        claims = rep.metadata.get("claimed_identifiers")
        if not isinstance(claims, Mapping):
            continue
        for key, raw_values in claims.items():
            values = raw_values if isinstance(raw_values, (list, tuple, set)) else [raw_values]
            for raw in values:
                if isinstance(raw, str) and raw.strip():
                    collected.setdefault(str(key), set()).add(raw.strip())
    return {key: tuple(sorted(values)) for key, values in sorted(collected.items())}


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


def _official_version_conflicts(representations: tuple[SourceRepresentation, ...]) -> list[EvidenceConflict]:
    conflicts: list[EvidenceConflict] = []
    for rep in representations:
        if not _representation_declares_official_version(rep):
            continue
        if not _representation_has_complete_latex_closure(rep):
            unresolved = rep.metadata.get("unresolved_references") or ()
            conflicts.append(
                EvidenceConflict(
                    code="LATEX_CLOSURE_INCOMPLETE",
                    field="version_authority",
                    values=(rep.locator, *tuple(str(v) for v in unresolved)),
                    blocking=False,
                    detail=(
                        "LaTeX source declares official_version but its transitive source closure "
                        "is incomplete; it is a fragment and cannot establish official version"
                    ),
                )
            )
        if not _representation_has_verified_version_anchor(rep):
            conflicts.append(
                EvidenceConflict(
                    code="OFFICIAL_VERSION_UNANCHORED",
                    field="version_authority",
                    values=(rep.locator,),
                    blocking=False,
                    detail=(
                        "LaTeX source declares official_version but lacks a verified immutable "
                        "commit/tag/manifest/canonical/signature anchor"
                    ),
                )
            )
    return conflicts


def _detect_stale_prose(
    representations: tuple[SourceRepresentation, ...],
    identifiers: Mapping[str, str],
) -> list[EvidenceConflict]:
    conflicts: list[EvidenceConflict] = []
    resolved_doi = identifiers.get("doi")
    if not resolved_doi:
        return conflicts

    for rep in representations:
        if _representation_is_generated(rep):
            continue
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


def _only_incomplete_latex_independent_roots(representations: tuple[SourceRepresentation, ...]) -> bool:
    roots = tuple(rep for rep in representations if _representation_is_independent(rep))
    if not roots:
        return False
    return all(_representation_is_latex(rep) and not _representation_has_complete_latex_closure(rep) for rep in roots)


def build_source_evidence(
    *,
    original_url: str,
    representations: Iterable[SourceRepresentation],
) -> SourceEvidence:
    reps = tuple(representations)
    empty_authority = {domain: 0.0 for domain in AUTHORITY_DOMAINS}

    if not reps:
        payload = {"original_url": original_url, "state": EvidenceState.UNAVAILABLE_AFTER_FALLBACK.value}
        return SourceEvidence(
            original_url=original_url,
            resolved_url=None,
            representations=(),
            identifiers={},
            claimed_identifiers={},
            authority=empty_authority,
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
            claimed_identifiers=_collect_claimed_identifiers(reps),
            authority=empty_authority,
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

    claimed_identifiers = _collect_claimed_identifiers(reps)
    conflicts.extend(_official_version_conflicts(reps))
    conflicts.extend(_detect_stale_prose(reps, identifiers))
    blocking = any(c.blocking for c in conflicts)

    no_independent_root = not any(_representation_is_independent(rep) for rep in reps)

    if blocking:
        state = EvidenceState.CONFLICT
    elif no_independent_root:
        state = EvidenceState.PARTIAL
    elif _only_incomplete_latex_independent_roots(reps):
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
        best = max(reps, key=_effective_priority)
        resolved_url = best.locator

    authority = _aggregate_authority(reps)
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
                "effective_priority": _effective_priority(rep),
                "authority": _representation_authority(rep),
                "official_version_source": _representation_is_official_version_source(rep),
            }
            for rep in reps
        ],
        "identifiers": identifiers,
        "claimed_identifiers": claimed_identifiers,
        "authority": authority,
        "official_version_evidence": any(_representation_is_official_version_source(rep) for rep in reps),
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
        claimed_identifiers=claimed_identifiers,
        authority=authority,
        conflicts=tuple(conflicts),
        state=state,
        evidence_tier=_tier(reps),
        evidence_hash=sha256_text(canonical_json(evidence_payload)),
    )
