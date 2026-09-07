from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .corpus_fractions import FRACTION_PROTOCOL, FractionManifest, FractionPlan
from .evidence import canonical_json, evidence_receipt

SCHEMA = "matverse.fraction-publication.v1"


class FractionPublicationError(RuntimeError):
    pass


class DisclosureMode(str, Enum):
    METADATA_ONLY = "METADATA_ONLY"
    REDACTED_BUNDLE = "REDACTED_BUNDLE"
    PUBLIC_BUNDLE = "PUBLIC_BUNDLE"


def _require_sha256(value: str, *, field: str) -> str:
    normalized = str(value).strip().lower()
    if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return normalized


@dataclass(frozen=True)
class FractionPublicationEnvelope:
    schema: str
    corpus_id: str
    source_protocol: str
    fraction_index: int
    start_ordinal: int
    end_ordinal: int
    member_count: int
    fraction_size: int
    total_items: int
    fraction_count: int
    manifest_hash: str
    merge_root: str
    disclosure_mode: DisclosureMode
    bundle_sha256: str | None
    receipt: dict[str, Any]

    def public_manifest(self) -> dict[str, Any]:
        """Return a publication-safe structural manifest with no source member IDs."""

        return {
            "schema": self.schema,
            "corpus_id": self.corpus_id,
            "source_protocol": self.source_protocol,
            "fraction_index": self.fraction_index,
            "start_ordinal": self.start_ordinal,
            "end_ordinal": self.end_ordinal,
            "member_count": self.member_count,
            "fraction_size": self.fraction_size,
            "total_items": self.total_items,
            "fraction_count": self.fraction_count,
            "manifest_hash": self.manifest_hash,
            "merge_root": self.merge_root,
            "disclosure_mode": self.disclosure_mode.value,
            "bundle_sha256": self.bundle_sha256,
        }


@dataclass(frozen=True)
class ZenodoDraftPlan:
    schema: str
    action: str
    target: str
    requires_authorization: bool
    metadata: dict[str, Any]
    generated_files: dict[str, str]
    external_files: tuple[dict[str, str], ...]
    receipt: dict[str, Any]


class FractionPublicationBridge:
    """Governed structural bridge from corpus fractions to publication staging.

    This module produces plans only. It never performs a Zenodo network write.
    Any executor must still cross the existing independent publication
    authorization and provider-secret boundaries.
    """

    def __init__(self, plan: FractionPlan, *, corpus_id: str) -> None:
        if plan.protocol != FRACTION_PROTOCOL:
            raise FractionPublicationError(f"unsupported fraction protocol: {plan.protocol}")
        if not corpus_id.strip():
            raise ValueError("corpus_id is required")
        _require_sha256(plan.merge_root, field="merge_root")
        self.plan = plan
        self.corpus_id = corpus_id.strip()

    def _fraction(self, fraction_index: int) -> FractionManifest:
        for fraction in self.plan.fractions:
            if fraction.fraction_index == fraction_index:
                return fraction
        raise FractionPublicationError(f"unknown fraction_index: {fraction_index}")

    def build_envelope(
        self,
        fraction_index: int,
        *,
        disclosure_mode: DisclosureMode = DisclosureMode.METADATA_ONLY,
        bundle_sha256: str | None = None,
    ) -> FractionPublicationEnvelope:
        fraction = self._fraction(fraction_index)
        _require_sha256(fraction.manifest_hash, field="manifest_hash")
        if disclosure_mode is DisclosureMode.METADATA_ONLY and bundle_sha256 is not None:
            raise FractionPublicationError("METADATA_ONLY must not attach a content bundle")
        if disclosure_mode in {DisclosureMode.REDACTED_BUNDLE, DisclosureMode.PUBLIC_BUNDLE}:
            if bundle_sha256 is None:
                raise FractionPublicationError(f"{disclosure_mode.value} requires bundle_sha256")
            bundle_sha256 = _require_sha256(bundle_sha256, field="bundle_sha256")

        core = {
            "schema": SCHEMA,
            "corpus_id": self.corpus_id,
            "source_protocol": self.plan.protocol,
            "fraction_index": fraction.fraction_index,
            "start_ordinal": fraction.start_ordinal,
            "end_ordinal": fraction.end_ordinal,
            "member_count": fraction.member_count,
            "fraction_size": self.plan.fraction_size,
            "total_items": self.plan.total_items,
            "fraction_count": self.plan.fraction_count,
            "manifest_hash": fraction.manifest_hash,
            "merge_root": self.plan.merge_root,
            "disclosure_mode": disclosure_mode.value,
            "bundle_sha256": bundle_sha256,
        }
        receipt = evidence_receipt(
            "fraction_publication.envelope",
            {"fraction_manifest_hash": fraction.manifest_hash, "merge_root": self.plan.merge_root},
            core,
        )
        return FractionPublicationEnvelope(
            schema=SCHEMA,
            corpus_id=self.corpus_id,
            source_protocol=self.plan.protocol,
            fraction_index=fraction.fraction_index,
            start_ordinal=fraction.start_ordinal,
            end_ordinal=fraction.end_ordinal,
            member_count=fraction.member_count,
            fraction_size=self.plan.fraction_size,
            total_items=self.plan.total_items,
            fraction_count=self.plan.fraction_count,
            manifest_hash=fraction.manifest_hash,
            merge_root=self.plan.merge_root,
            disclosure_mode=disclosure_mode,
            bundle_sha256=bundle_sha256,
            receipt=receipt,
        )

    def _validate_envelope(self, envelope: FractionPublicationEnvelope) -> None:
        if envelope.schema != SCHEMA:
            raise FractionPublicationError(f"unsupported publication envelope schema: {envelope.schema}")
        if envelope.corpus_id != self.corpus_id:
            raise FractionPublicationError("envelope corpus_id does not belong to this bridge")
        if envelope.source_protocol != self.plan.protocol:
            raise FractionPublicationError("envelope source protocol does not match this fraction plan")
        if envelope.merge_root != self.plan.merge_root:
            raise FractionPublicationError("envelope merge_root does not match this fraction plan")
        fraction = self._fraction(envelope.fraction_index)
        expected = (
            fraction.start_ordinal,
            fraction.end_ordinal,
            fraction.member_count,
            self.plan.fraction_size,
            self.plan.total_items,
            self.plan.fraction_count,
            fraction.manifest_hash,
        )
        actual = (
            envelope.start_ordinal,
            envelope.end_ordinal,
            envelope.member_count,
            envelope.fraction_size,
            envelope.total_items,
            envelope.fraction_count,
            envelope.manifest_hash,
        )
        if actual != expected:
            raise FractionPublicationError("envelope structural fields do not match the canonical fraction plan")
        if envelope.disclosure_mode is DisclosureMode.METADATA_ONLY and envelope.bundle_sha256 is not None:
            raise FractionPublicationError("METADATA_ONLY envelope must not contain bundle_sha256")
        if envelope.disclosure_mode in {DisclosureMode.REDACTED_BUNDLE, DisclosureMode.PUBLIC_BUNDLE}:
            if envelope.bundle_sha256 is None:
                raise FractionPublicationError("bundle disclosure envelope is missing bundle_sha256")
            _require_sha256(envelope.bundle_sha256, field="bundle_sha256")

    def prepare_zenodo_draft(
        self,
        envelope: FractionPublicationEnvelope,
        *,
        title_prefix: str = "MatVerse Corpus Fraction",
        creators: tuple[str, ...] = (),
    ) -> ZenodoDraftPlan:
        self._validate_envelope(envelope)
        cleaned_creators = tuple(item.strip() for item in creators if item.strip())
        if not cleaned_creators:
            raise FractionPublicationError("Zenodo staging requires at least one explicit creator")
        if not title_prefix.strip():
            raise FractionPublicationError("title_prefix is required")

        public_manifest = envelope.public_manifest()
        manifest_text = canonical_json(public_manifest)
        manifest_name = f"{self.corpus_id}-F{envelope.fraction_index:03d}-manifest.json"
        external_files: list[dict[str, str]] = []
        if envelope.bundle_sha256 is not None:
            suffix = "redacted" if envelope.disclosure_mode is DisclosureMode.REDACTED_BUNDLE else "public"
            external_files.append(
                {
                    "filename": f"{self.corpus_id}-F{envelope.fraction_index:03d}-{suffix}-bundle",
                    "sha256": envelope.bundle_sha256,
                    "disclosure_mode": envelope.disclosure_mode.value,
                }
            )

        metadata = {
            "title": f"{title_prefix.strip()}: {self.corpus_id} F{envelope.fraction_index}",
            "resource_type": "dataset",
            "creators": list(cleaned_creators),
            "description": (
                "Governed structural publication envelope for one corpus fraction. "
                "The fraction manifest hash and corpus merge root commit to structural "
                "integrity; they do not by themselves authorize disclosure of source content."
            ),
            "keywords": [
                "matverse",
                "corpus-fractions",
                envelope.source_protocol,
                "governed-publication",
            ],
            "notes": {
                "fraction_index": envelope.fraction_index,
                "manifest_hash": envelope.manifest_hash,
                "merge_root": envelope.merge_root,
                "disclosure_mode": envelope.disclosure_mode.value,
            },
        }
        output = {
            "schema": SCHEMA,
            "action": "zenodo.create_draft",
            "target": f"zenodo:{self.corpus_id}:F{envelope.fraction_index}",
            "requires_authorization": True,
            "metadata": metadata,
            "generated_files": {manifest_name: manifest_text},
            "external_files": external_files,
        }
        receipt = evidence_receipt(
            "fraction_publication.zenodo_draft_plan",
            {"envelope": public_manifest},
            output,
        )
        return ZenodoDraftPlan(
            schema=SCHEMA,
            action="zenodo.create_draft",
            target=output["target"],
            requires_authorization=True,
            metadata=metadata,
            generated_files={manifest_name: manifest_text},
            external_files=tuple(external_files),
            receipt=receipt,
        )
