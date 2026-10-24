"""QTN semantic review overlay: non-authoritative, no automatic validation/authorization.

Keeps legacy QTN finding unchanged and adds separate document-kind,
acronym and source-to-QTN relation statuses. A pointer is not source evidence.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from app.qtn_watch import QTN_RULES
from app.qtn_context import resolve_pqc
from app.qtn_source_lineage import pointer_identity, version_inventory
from urllib.parse import urlsplit
import hashlib
import json
import re


@dataclass(frozen=True)
class SourceRecord:
    source: str
    title: str
    url: str
    summary: str = ""
    qtn_ids: tuple[str, ...] = ()


def document_kind(record: SourceRecord) -> tuple[str, str]:
    try:
        u = urlsplit(record.url)
        if u.scheme != "https" or u.username or u.password or u.port is not None:
            return "UNKNOWN_SOURCE", "UNVERIFIED_POINTER"
    except ValueError:
        return "UNKNOWN_SOURCE", "UNVERIFIED_POINTER"
    host, path = (u.hostname or "").lower(), u.path.lower()
    declared = record.source.lower()
    if host in ("arxiv.org", "www.arxiv.org") and re.fullmatch(r"/abs/\d{4}\.\d{4,5}(v[1-9]\d*)?/?", path):
        return ("PREPRINT" if declared == "arxiv" else "SOURCE_CONFLICT",
                "VERSION_PINNED_URL" if re.search(r"v[1-9]\d*/?$", path) else "MUTABLE_LATEST_URL")
    if host == "datatracker.ietf.org" and path.startswith(("/doc/draft-", "/doc/html/draft-")):
        return ("IETF_DRAFT" if declared == "ietf" else "SOURCE_CONFLICT",
                "VERSION_PINNED_URL" if re.search(r"(?:-\d{2}|/\d{2})/?$", path) else "MUTABLE_LATEST_URL")
    if host in ("rfc-editor.org", "www.rfc-editor.org") and re.fullmatch(r"/rfc/rfc\d+(?:\.html)?/?", path):
        return ("PUBLISHED_RFC" if declared in ("ietf", "rfc") else "SOURCE_CONFLICT", "VERSION_PINNED_URL")
    return "UNKNOWN_SOURCE", "UNVERIFIED_POINTER"


def pqc_expansion(record: SourceRecord) -> str:
    return resolve_pqc(record.title + " " + record.summary)

def review_findings(findings: list[dict], source_items: list[object]) -> list[dict]:
    """Exact source join, fail closed on ambiguous/missing joins or QTN types."""
    historical_versions = version_inventory(source_items)
    index = defaultdict(list)
    for source in source_items:
        index[(source.source, source.title, source.url)].append(source)
    reviewed = []
    for finding in findings:
        if not isinstance(finding, dict):
            raise ValueError("finding must be a dictionary")
        key = (finding.get("source"), finding.get("title"), finding.get("url"))
        matches = index.get(key, [])
        if len(matches) != 1:
            raise ValueError("missing or ambiguous exact source match")
        if "semantic_review" in finding:
            raise ValueError("refusing to overwrite existing review")
        ids = finding.get("qtn_ids")
        if not isinstance(ids, (tuple, list)) or not ids or not all(
            isinstance(value, str) and value in QTN_RULES for value in ids
        ):
            raise ValueError("invalid QTN relation identifiers")
        source = matches[0]
        record = SourceRecord(source.source, source.title, source.url, source.summary, tuple(ids))
        kind, pointer = document_kind(record)
        scientific_pointer = pointer_identity(record.url)
        work_id = scientific_pointer["work_id"]
        observed_versions = historical_versions.get(work_id, [])
        collision = any(v["metadata_conflict"] for v in observed_versions)
        lineage_status = (
            "HOLD_VERSION_METADATA_COLLISION" if collision
            else "HOLD_UNPINNED_OR_UNKNOWN_VERSION"
            if scientific_pointer["version_id"] is None
            else "HOLD_MULTIVERSION_ADJUDICATION" if len(observed_versions) > 1
            else "HOLD_BODY_UNVERIFIED"
        )
        expansion = pqc_expansion(record)
        description = (record.title + " " + record.summary).lower()
        subject = ("QUANTUM_CLASSICAL_COMPUTING"
                   if expansion == "PARAMETERIZED_QUANTUM_CIRCUIT" or any(
                       v in description for v in ("bayesian inversion", "latent surrogate", "quantum compiler"))
                   else "POST_QUANTUM_SECURITY" if expansion == "POST_QUANTUM_CRYPTOGRAPHY"
                   else "UNCERTAIN_RESEARCH_TOPIC")
        metadata_hash = hashlib.sha256(json.dumps(
            {"title": source.title, "url": source.url}, ensure_ascii=False,
            sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        overlay = {
            "schema": "matverse.qtn.semantic-review/0.3.1",
            "document_kind": kind,
            "pointer_status": pointer,
            "pqc_expansion": expansion,
            "scientific_subject": subject,
            "relation_status": "REVIEW_REQUIRED",
            "evidence_status": "EXTERNAL_CONTENT_NOT_AUTHENTICATED",
            "source_metadata_fingerprint": metadata_hash,
            "semantic_review_verdict": "HOLD_MANUAL_ADJUDICATION",
            "original_impact_unchanged": finding.get("impact_type"),
            "source_work_id": work_id,
            "source_version_id": scientific_pointer["version_id"],
            "source_version_tag": scientific_pointer["version_tag"],
            "source_version_pointer": scientific_pointer["version_pointer"],
            "source_pointer_kind": scientific_pointer["pointer_kind"],
            "observed_version_inventory": observed_versions,
            "version_lineage_status": lineage_status,
            "version_body_sha256": None,  # no verified body bytes fetched
            "bibliographic_identity_is_not_version_evidence": True,
        }
        # A metadata classification is never a science PASS, RFC status or Gate approval.
        reviewed.append({**finding, "semantic_review": overlay})
    return reviewed
