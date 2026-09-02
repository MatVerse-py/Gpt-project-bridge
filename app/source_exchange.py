"""Versioned SourceEvidence -> ARGUS exchange contract.

This module is intentionally transport-agnostic. Any HTTP/MCP/catalog endpoint
may expose the returned payload, but the evidence semantics stay stable and
unit-testable. Raw source text is never exported implicitly; callers must pass
`observed_text_by_locator` explicitly when disclosure is intended.
"""

from __future__ import annotations

from hashlib import sha256
from typing import Mapping
import json

from app.source_evidence import RepresentationType, SourceEvidence, SourceRepresentation


ARGUS_BATCH_SCHEMA = "matverse.bridge-evidence-batch.v1"

_SAFE_METADATA_KEYS = {
    "citation_doi",
    "citation_title",
    "citation_author",
    "citation_publication_date",
    "citation_pdf_url",
    "canonical_url",
    "title",
    "author",
    "version",
    "description",
    "og:description",
    "closure_complete",
    "official_version",
    "model_generated",
    "generated",
    "derived_representation",
    "evidence_root_id",
    "derived_from_root",
    "context_status",
    "integrity_status",
    "claim_relation",
    "content_type",
    "captured_at",
    "published_at",
}


def _safe_metadata(rep: SourceRepresentation) -> dict[str, object]:
    return {
        str(key): value
        for key, value in rep.metadata.items()
        if str(key) in _SAFE_METADATA_KEYS
        and isinstance(value, (str, int, float, bool, type(None)))
    }


def _independent(rep: SourceRepresentation) -> bool:
    if rep.kind in {RepresentationType.GENERATED_IMAGE, RepresentationType.DOCUMENT_PAGE_RENDER}:
        return False
    if rep.metadata.get("generated") is True or rep.metadata.get("model_generated") is True:
        return False
    if rep.metadata.get("derived_representation") is True:
        return False
    return True


def source_evidence_to_argus_batch(
    evidence: SourceEvidence,
    *,
    observed_text_by_locator: Mapping[str, str] | None = None,
    claim_relation_by_locator: Mapping[str, str] | None = None,
    max_items: int = 32,
) -> dict[str, object]:
    """Serialize SourceEvidence into the ARGUS wire contract.

    `observed_text_by_locator` is opt-in because SourceEvidence intentionally
    stores hashes/metadata rather than retaining arbitrary source bodies.
    `claim_relation_by_locator`, when supplied by an upstream adjudicator, is
    transported as an explicit relation and is never inferred here.
    """

    observed_text_by_locator = dict(observed_text_by_locator or {})
    claim_relation_by_locator = dict(claim_relation_by_locator or {})
    items: list[dict[str, object]] = []

    for rep in evidence.representations[: max(0, max_items)]:
        metadata = _safe_metadata(rep)
        root_id = str(
            rep.metadata.get("evidence_root_id")
            or rep.metadata.get("derived_from_root")
            or rep.content_hash
        )
        item: dict[str, object] = {
            "locator": rep.locator,
            "representation": rep.kind.value,
            "source_content_hash": rep.content_hash,
            "evidence_root_id": root_id,
            "independent": _independent(rep),
            "metadata": metadata,
        }

        relation = claim_relation_by_locator.get(rep.locator)
        if relation:
            item["claim_relation"] = relation

        observed_text = observed_text_by_locator.get(rep.locator)
        if observed_text is not None:
            item["observed_text"] = observed_text
            item["observed_text_sha256"] = sha256(observed_text.encode("utf-8")).hexdigest()

        if rep.metadata.get("generated") is True:
            item["generated"] = True
        if rep.metadata.get("model_generated") is True:
            item["model_generated"] = True
        if rep.metadata.get("derived_representation") is True:
            item["derived_representation"] = True

        items.append(item)

    return {
        "schema": ARGUS_BATCH_SCHEMA,
        "evidence_hash": evidence.evidence_hash,
        "state": evidence.state.value,
        "evidence_tier": evidence.evidence_tier,
        "authority": dict(evidence.authority),
        "resolved_url": evidence.resolved_url,
        "independent_evidence": evidence.independent_evidence,
        "official_version_evidence": evidence.official_version_evidence,
        "identifiers": dict(evidence.identifiers),
        "claimed_identifiers": {
            key: list(values) for key, values in evidence.claimed_identifiers.items()
        },
        "conflicts": [
            {
                "code": conflict.code,
                "field": conflict.field,
                "blocking": conflict.blocking,
                "values": list(conflict.values),
                "detail": conflict.detail,
            }
            for conflict in evidence.conflicts
        ],
        "items": items,
    }
