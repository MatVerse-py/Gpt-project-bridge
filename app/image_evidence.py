from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
from pathlib import Path
from typing import Mapping, Sequence


class ImageEvidenceKind(str, Enum):
    SCREENSHOT = "SCREENSHOT"
    SAVED_IMAGE = "SAVED_IMAGE"
    GENERATED_IMAGE = "GENERATED_IMAGE"
    DOCUMENT_PAGE_RENDER = "DOCUMENT_PAGE_RENDER"


class ImageEvidenceState(str, Enum):
    VISUAL_OBSERVATION = "VISUAL_OBSERVATION"
    VERIFIED_SNAPSHOT = "VERIFIED_SNAPSHOT"
    PARTIAL = "PARTIAL"
    CONFLICT = "CONFLICT"
    BLOCK_TAMPERED = "BLOCK_TAMPERED"


@dataclass(frozen=True)
class ImageEvidence:
    source_id: str
    kind: ImageEvidenceKind
    sha256: str
    visible_text: tuple[str, ...] = ()
    source_url: str | None = None
    generated: bool = False
    derived_from_hash: str | None = None
    external_claims: tuple[str, ...] = ()
    independently_verified_claims: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)

    @property
    def independent_evidence(self) -> bool:
        # Provenance dominates appearance: a generated image remains generated
        # even if it visually resembles a report, browser capture, or dashboard.
        if self.generated or self.kind is ImageEvidenceKind.GENERATED_IMAGE:
            return False
        # A page render is derivative of the underlying PDF/document root.
        if self.kind is ImageEvidenceKind.DOCUMENT_PAGE_RENDER:
            return False
        return True

    @property
    def state(self) -> ImageEvidenceState:
        if bool(self.metadata.get("tampered")):
            return ImageEvidenceState.BLOCK_TAMPERED
        if self.conflicts:
            return ImageEvidenceState.CONFLICT
        if not self.independent_evidence:
            return ImageEvidenceState.PARTIAL
        if self.external_claims:
            unresolved = set(self.external_claims) - set(self.independently_verified_claims)
            if unresolved:
                return ImageEvidenceState.PARTIAL
        if self.kind is ImageEvidenceKind.SCREENSHOT:
            return ImageEvidenceState.VISUAL_OBSERVATION
        return ImageEvidenceState.VERIFIED_SNAPSHOT


def sha256_bytes(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def image_evidence_from_file(
    path: str | Path,
    *,
    source_id: str,
    kind: ImageEvidenceKind,
    visible_text: Sequence[str] = (),
    source_url: str | None = None,
    generated: bool = False,
    derived_from_hash: str | None = None,
    external_claims: Sequence[str] = (),
    independently_verified_claims: Sequence[str] = (),
    expected_sha256: str | None = None,
) -> ImageEvidence:
    raw = Path(path).read_bytes()
    digest = sha256_bytes(raw)
    tampered = expected_sha256 is not None and digest != expected_sha256
    return ImageEvidence(
        source_id=source_id,
        kind=kind,
        sha256=digest,
        visible_text=tuple(visible_text),
        source_url=source_url,
        generated=generated,
        derived_from_hash=derived_from_hash,
        external_claims=tuple(external_claims),
        independently_verified_claims=tuple(independently_verified_claims),
        metadata={"tampered": tampered, "size_bytes": len(raw)},
    )


def group_by_hash(items: Sequence[ImageEvidence]) -> dict[str, tuple[ImageEvidence, ...]]:
    """Group exact byte-identical images into one probative root per SHA-256."""
    groups: dict[str, list[ImageEvidence]] = {}
    for item in items:
        groups.setdefault(item.sha256, []).append(item)
    return {digest: tuple(group) for digest, group in groups.items()}


def dedupe_by_hash(items: Sequence[ImageEvidence]) -> tuple[ImageEvidence, ...]:
    """Deduplicate exact byte-identical images; duplicates are not independent roots."""
    return tuple(group[0] for group in group_by_hash(items).values())


def visual_near_duplicate_is_probative_match(*, exact_hash_equal: bool) -> bool:
    """Visual similarity alone never establishes evidentiary identity.

    Perceptual hashes, OCR similarity, same dimensions, or human visual similarity
    are review signals only. Only exact byte identity (or a separately verified
    derivation relation) may collapse two files into one evidence root.
    """
    return exact_hash_equal
