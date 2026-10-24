"""Version-specific local text excerpt receipts.

Matching a caller-supplied digest is content integrity *relative to that digest*,
not authentication of a publisher, peer review or scientific truth.
"""
from __future__ import annotations

from hashlib import sha256
from hmac import compare_digest
import json
import re

from app.qtn_source_lineage import pointer_identity

_MAX_BYTES = 10 * 1024 * 1024


def bind_claim_excerpt(*, source_url: str, document_bytes: bytes,
                       expected_sha256: str, exact_excerpt: str) -> dict:
    """Bind an exact UTF-8 excerpt to supplied bytes and a pinned source version.

    Only textual byte streams (e.g., saved UTF-8 plain text) are supported.
    This is NOT a PDF reader, webpage signature verifier, publisher attestation,
    or decision about the truth of the claim.
    """
    identity = pointer_identity(source_url)
    if not identity["version_id"]:
        raise ValueError("pinned recognized source version URL required")
    if not isinstance(document_bytes, bytes) or not (0 < len(document_bytes) <= _MAX_BYTES):
        raise ValueError("nonempty bounded document_bytes required")
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ValueError("lowercase SHA-256 expectation required")
    if not isinstance(exact_excerpt, str) or not exact_excerpt.strip():
        raise ValueError("nonempty claim excerpt required")
    actual_sha = sha256(document_bytes).hexdigest()
    if not compare_digest(actual_sha, expected_sha256):
        raise ValueError("digest mismatch; do not bind excerpt")
    excerpt_bytes = exact_excerpt.encode("utf-8")
    # Verify text bytes are valid UTF-8; never silently parse a PDF binary as text.
    document_bytes.decode("utf-8", errors="strict")
    start = document_bytes.find(excerpt_bytes)
    if start < 0:
        raise ValueError("excerpt not present in verified-by-hash input bytes")
    if document_bytes.find(excerpt_bytes, start + 1) >= 0:
        raise ValueError("ambiguous repeated excerpt; use a longer distinct anchor")
    binding = {
        "schema": "matverse.qtn.claim-excerpt/0.1",
        "source_work_id": identity["work_id"],
        "source_version_id": identity["version_id"],
        "source_version_pointer": identity["version_pointer"],
        "content_sha256": actual_sha,
        "content_format": "UTF8_TEXT_BYTES",
        "excerpt_sha256": sha256(excerpt_bytes).hexdigest(),
        "byte_start": start,
        "byte_end": start + len(excerpt_bytes),
        "integrity_result": "MATCHES_CALLER_SUPPLIED_DIGEST",
        "origin_authentication": "HOLD_NO_TRUSTED_EXTERNAL_ROOT",
        "claim_veracity": "HOLD_MANUAL_SCIENTIFIC_ADJUDICATION",
        "gate_authorization": "NOT_GRANTED",
    }
    binding["binding_digest"] = sha256(json.dumps(
        binding, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")).hexdigest()
    return binding
