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
    external_claims: tuple[str, ...] = ()
    independently_verified_claims: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)

    @property
    def independent_evidence(self) -> bool:
        return self.kind is not ImageEvidenceKind.GENERATED_IMAGE and not self.generated

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
        external_claims=tuple(external_claims),
        independently_verified_claims=tuple(independently_verified_claims),
        metadata={"tampered": tampered, "size_bytes": len(raw)},
    )


def dedupe_by_hash(items: Sequence[ImageEvidence]) -> tuple[ImageEvidence, ...]:
    """Deduplicate exact byte-identical images; duplicates are not independent roots."""
    seen: set[str] = set()
    unique: list[ImageEvidence] = []
    for item in items:
        if item.sha256 in seen:
            continue
        seen.add(item.sha256)
        unique.append(item)
    return tuple(unique)
