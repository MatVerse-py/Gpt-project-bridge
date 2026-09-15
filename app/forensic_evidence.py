from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
import math
import os

from app.evidence import canonical_json, evidence_receipt


class ForensicArtifactKind(str, Enum):
    ORIGINAL_FILE = "ORIGINAL_FILE"
    LOGICAL_COPY = "LOGICAL_COPY"
    FORENSIC_IMAGE = "FORENSIC_IMAGE"
    MEMORY_SNAPSHOT = "MEMORY_SNAPSHOT"
    CARVED_ARTIFACT = "CARVED_ARTIFACT"
    FILE_SYSTEM_METADATA = "FILE_SYSTEM_METADATA"
    HASH_VERIFICATION = "HASH_VERIFICATION"
    TIMELINE_OBSERVATION = "TIMELINE_OBSERVATION"
    ENTROPY_OBSERVATION = "ENTROPY_OBSERVATION"


class ForensicState(str, Enum):
    FORENSIC_VERIFIED = "FORENSIC_VERIFIED"
    INTEGRITY_VERIFIED = "INTEGRITY_VERIFIED"
    CUSTODY_VERIFIED = "CUSTODY_VERIFIED"
    PARTIAL = "PARTIAL"
    HOLD_ACQUISITION_METHOD = "HOLD_ACQUISITION_METHOD"
    BLOCK_TAMPERED = "BLOCK_TAMPERED"


class CustodyEventType(str, Enum):
    ACQUIRED = "ACQUIRED"
    HASHED = "HASHED"
    COPIED = "COPIED"
    ANALYZED = "ANALYZED"
    EXTRACTED = "EXTRACTED"
    TRANSFERRED = "TRANSFERRED"
    REPORTED = "REPORTED"
    SEALED = "SEALED"


FORENSIC_AUTHORITY_DOMAINS = (
    "content",
    "version",
    "authorship",
    "publication",
    "timestamp",
    "execution",
    "integrity",
    "custody",
)

# Policy weights only. They are not probabilities, legal-admissibility scores,
# or scientific truth scores. Forensic evidence adds two domains that the
# generic source layer cannot express cleanly: byte integrity and custody.
BASE_FORENSIC_AUTHORITY: dict[ForensicArtifactKind, dict[str, float]] = {
    ForensicArtifactKind.ORIGINAL_FILE: {
        "content": 0.90,
        "version": 0.40,
        "authorship": 0.05,
        "publication": 0.00,
        "timestamp": 0.10,
        "execution": 0.00,
        "integrity": 0.25,
        "custody": 0.00,
    },
    ForensicArtifactKind.LOGICAL_COPY: {
        "content": 0.90,
        "version": 0.50,
        "authorship": 0.05,
        "publication": 0.00,
        "timestamp": 0.15,
        "execution": 0.00,
        "integrity": 0.25,
        "custody": 0.00,
    },
    ForensicArtifactKind.FORENSIC_IMAGE: {
        "content": 0.95,
        "version": 0.70,
        "authorship": 0.05,
        "publication": 0.00,
        "timestamp": 0.20,
        "execution": 0.00,
        "integrity": 0.30,
        "custody": 0.00,
    },
    ForensicArtifactKind.MEMORY_SNAPSHOT: {
        "content": 0.85,
        "version": 0.35,
        "authorship": 0.05,
        "publication": 0.00,
        "timestamp": 0.35,
        "execution": 0.35,
        "integrity": 0.25,
        "custody": 0.00,
    },
    ForensicArtifactKind.CARVED_ARTIFACT: {
        "content": 0.55,
        "version": 0.20,
        "authorship": 0.00,
        "publication": 0.00,
        "timestamp": 0.05,
        "execution": 0.00,
        "integrity": 0.20,
        "custody": 0.00,
    },
    ForensicArtifactKind.FILE_SYSTEM_METADATA: {
        "content": 0.25,
        "version": 0.10,
        "authorship": 0.00,
        "publication": 0.00,
        "timestamp": 0.35,
        "execution": 0.00,
        "integrity": 0.10,
        "custody": 0.00,
    },
    ForensicArtifactKind.HASH_VERIFICATION: {
        "content": 0.05,
        "version": 0.20,
        "authorship": 0.00,
        "publication": 0.00,
        "timestamp": 0.00,
        "execution": 0.00,
        "integrity": 0.95,
        "custody": 0.00,
    },
    ForensicArtifactKind.TIMELINE_OBSERVATION: {
        "content": 0.20,
        "version": 0.05,
        "authorship": 0.00,
        "publication": 0.00,
        "timestamp": 0.45,
        "execution": 0.10,
        "integrity": 0.10,
        "custody": 0.00,
    },
    ForensicArtifactKind.ENTROPY_OBSERVATION: {
        "content": 0.10,
        "version": 0.00,
        "authorship": 0.00,
        "publication": 0.00,
        "timestamp": 0.00,
        "execution": 0.00,
        "integrity": 0.00,
        "custody": 0.00,
    },
}


def _sha256_bytes(data: bytes) -> str:
    return sha256(data).hexdigest()


def sha256_file(path: str | Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = sha256()
    with Path(path).open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class CustodyEvent:
    sequence: int
    event_type: CustodyEventType
    evidence_id: str
    observed_at: str
    actor: str
    input_hash: str
    output_hash: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
    previous_event_hash: str = ""
    event_hash: str = ""

    def payload(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "event_type": self.event_type.value,
            "evidence_id": self.evidence_id,
            "observed_at": self.observed_at,
            "actor": self.actor,
            "input_hash": self.input_hash,
            "output_hash": self.output_hash,
            "metadata": dict(self.metadata),
            "previous_event_hash": self.previous_event_hash,
        }


@dataclass(frozen=True)
class CustodyVerification:
    valid: bool
    errors: tuple[str, ...]
    chain_head: str


def create_custody_event(
    *,
    sequence: int,
    event_type: CustodyEventType,
    evidence_id: str,
    actor: str,
    input_hash: str,
    output_hash: str,
    previous_event_hash: str = "",
    observed_at: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> CustodyEvent:
    event = CustodyEvent(
        sequence=sequence,
        event_type=event_type,
        evidence_id=evidence_id,
        observed_at=observed_at or _utc_now(),
        actor=actor,
        input_hash=input_hash,
        output_hash=output_hash,
        metadata=dict(metadata or {}),
        previous_event_hash=previous_event_hash,
    )
    # This is a tamper-evident hash-chain commitment, not a cryptographic
    # signature. Collector identity must be established separately.
    return replace(event, event_hash=_sha256_bytes(canonical_json(event.payload()).encode("utf-8")))


def append_custody_event(
    chain: Sequence[CustodyEvent],
    *,
    event_type: CustodyEventType,
    evidence_id: str,
    actor: str,
    input_hash: str,
    output_hash: str,
    observed_at: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> tuple[CustodyEvent, ...]:
    previous = chain[-1].event_hash if chain else ""
    event = create_custody_event(
        sequence=len(chain),
        event_type=event_type,
        evidence_id=evidence_id,
        actor=actor,
        input_hash=input_hash,
        output_hash=output_hash,
        previous_event_hash=previous,
        observed_at=observed_at,
        metadata=metadata,
    )
    return (*chain, event)


def verify_custody_chain(chain: Sequence[CustodyEvent]) -> CustodyVerification:
    errors: list[str] = []
    previous_hash = ""
    evidence_id: str | None = None

    for index, event in enumerate(chain):
        if event.sequence != index:
            errors.append(f"sequence mismatch at index {index}: {event.sequence}")
        if evidence_id is None:
            evidence_id = event.evidence_id
        elif event.evidence_id != evidence_id:
            errors.append(f"evidence_id changed at index {index}")
        if event.previous_event_hash != previous_hash:
            errors.append(f"previous_event_hash mismatch at index {index}")
        expected = _sha256_bytes(canonical_json(event.payload()).encode("utf-8"))
        if event.event_hash != expected:
            errors.append(f"event_hash mismatch at index {index}")
        previous_hash = event.event_hash

    return CustodyVerification(
        valid=not errors and bool(chain),
        errors=tuple(errors),
        chain_head=chain[-1].event_hash if chain else "",
    )


def filesystem_metadata(path: str | Path) -> dict[str, Any]:
    """Capture observable filesystem metadata without inflating its authority.

    OS-level timestamps are mutable and filesystem-dependent. They are recorded
    as observations; they are not promoted to trusted acquisition timestamps by
    this function.
    """
    target = Path(path)
    stat = target.stat()
    return {
        "path": str(target),
        "size_bytes": stat.st_size,
        "mode": stat.st_mode,
        "inode": getattr(stat, "st_ino", None),
        "device": getattr(stat, "st_dev", None),
        "mtime_ns": getattr(stat, "st_mtime_ns", None),
        "ctime_ns": getattr(stat, "st_ctime_ns", None),
        "atime_ns": getattr(stat, "st_atime_ns", None),
        "birthtime": getattr(stat, "st_birthtime", None),
        "observation_class": "FILE_SYSTEM_METADATA_OBSERVED",
        "warning": "filesystem timestamps are contextual observations, not trusted chronology by themselves",
    }


def shannon_entropy_bytes(data: bytes) -> float:
    if not data:
        return 0.0
    counts = [0] * 256
    for byte in data:
        counts[byte] += 1
    total = len(data)
    value = 0.0
    for count in counts:
        if count:
            p = count / total
            value -= p * math.log2(p)
    return value


def entropy_observation(path: str | Path, *, block_size: int = 4096) -> dict[str, Any]:
    """Return a computed entropy observation, never a steganography verdict."""
    target = Path(path)
    global_counts = [0] * 256
    total = 0
    blocks: list[float] = []
    with target.open("rb") as handle:
        while True:
            chunk = handle.read(block_size)
            if not chunk:
                break
            blocks.append(shannon_entropy_bytes(chunk))
            total += len(chunk)
            for byte in chunk:
                global_counts[byte] += 1

    if total == 0:
        overall = 0.0
    else:
        overall = 0.0
        for count in global_counts:
            if count:
                p = count / total
                overall -= p * math.log2(p)

    return {
        "path": str(target),
        "overall_entropy_bits_per_byte": overall,
        "block_size": block_size,
        "block_entropy": tuple(blocks),
        "high_entropy_blocks": tuple(i for i, value in enumerate(blocks) if value > 7.5),
        "interpretation": "COMPUTED_STATISTIC_ONLY",
        "warning": "high entropy alone does not establish encryption, compression, or steganography",
    }


@dataclass(frozen=True)
class ForensicEvidence:
    evidence_id: str
    kind: ForensicArtifactKind
    locator: str
    artifact_hash: str
    expected_hash: str | None
    hash_verified: bool
    custody: tuple[CustodyEvent, ...]
    custody_verification: CustodyVerification
    metadata: Mapping[str, Any]
    authority: Mapping[str, float]
    state: ForensicState
    warnings: tuple[str, ...]
    forensic_hash: str
    receipt: Mapping[str, Any]


def _authority_for(
    kind: ForensicArtifactKind,
    *,
    hash_verified: bool,
    custody_verified: bool,
    metadata: Mapping[str, Any],
) -> dict[str, float]:
    base = {domain: 0.0 for domain in FORENSIC_AUTHORITY_DOMAINS}
    base.update(BASE_FORENSIC_AUTHORITY[kind])

    if hash_verified:
        base["integrity"] = max(base["integrity"], 0.95)
    else:
        base["integrity"] = min(base["integrity"], 0.30)

    if custody_verified:
        base["custody"] = 0.95
    else:
        base["custody"] = 0.00

    # Filesystem timestamps only rise when their filesystem/acquisition context
    # is itself verified; otherwise they remain contextual observations.
    if kind in {ForensicArtifactKind.FILE_SYSTEM_METADATA, ForensicArtifactKind.TIMELINE_OBSERVATION}:
        if metadata.get("filesystem_context_verified") is True:
            base["timestamp"] = max(base["timestamp"], 0.60)
        else:
            base["timestamp"] = min(base["timestamp"], 0.35)

    # A verified collector clock can support the time of acquisition, not the
    # historical timestamps contained in the acquired filesystem.
    if metadata.get("acquisition_clock_verified") is True:
        base["timestamp"] = max(base["timestamp"], 0.55)

    return base


def build_forensic_evidence(
    *,
    evidence_id: str,
    kind: ForensicArtifactKind,
    locator: str,
    artifact_bytes: bytes | None = None,
    artifact_hash: str | None = None,
    expected_hash: str | None = None,
    custody: Iterable[CustodyEvent] = (),
    metadata: Mapping[str, Any] | None = None,
) -> ForensicEvidence:
    if artifact_bytes is None and artifact_hash is None:
        raise ValueError("artifact_bytes or artifact_hash is required")

    digest = artifact_hash or _sha256_bytes(artifact_bytes or b"")
    chain = tuple(custody)
    custody_verification = verify_custody_chain(chain)
    metadata_dict = dict(metadata or {})
    hash_verified = expected_hash is not None and digest == expected_hash

    warnings: list[str] = []
    tampered = expected_hash is not None and digest != expected_hash
    if chain and not custody_verification.valid:
        tampered = True
        warnings.append("custody hash-chain failed verification")

    if kind in {ForensicArtifactKind.LOGICAL_COPY, ForensicArtifactKind.FORENSIC_IMAGE}:
        if metadata_dict.get("write_blocker_verified") is not True:
            warnings.append(
                "acquisition method is not independently verified as write-blocked; read-only software access is not a hardware write blocker"
            )

    if kind is ForensicArtifactKind.CARVED_ARTIFACT:
        warnings.append("carved bytes do not by themselves prove original filename, path, timestamps, or filesystem context")

    if kind is ForensicArtifactKind.ENTROPY_OBSERVATION:
        warnings.append("entropy is a computed statistic and must not be promoted to an encryption/steganography verdict")

    authority = _authority_for(
        kind,
        hash_verified=hash_verified,
        custody_verified=custody_verification.valid,
        metadata=metadata_dict,
    )

    if tampered:
        state = ForensicState.BLOCK_TAMPERED
        authority = {domain: 0.0 for domain in FORENSIC_AUTHORITY_DOMAINS}
    elif hash_verified and custody_verification.valid:
        state = ForensicState.FORENSIC_VERIFIED
    elif hash_verified:
        state = ForensicState.INTEGRITY_VERIFIED
    elif custody_verification.valid:
        state = ForensicState.CUSTODY_VERIFIED
    elif kind is ForensicArtifactKind.FORENSIC_IMAGE and metadata_dict.get("write_blocker_claimed") is True:
        state = ForensicState.HOLD_ACQUISITION_METHOD
    else:
        state = ForensicState.PARTIAL

    payload = {
        "evidence_id": evidence_id,
        "kind": kind.value,
        "locator": locator,
        "artifact_hash": digest,
        "expected_hash": expected_hash,
        "hash_verified": hash_verified,
        "custody_valid": custody_verification.valid,
        "custody_chain_head": custody_verification.chain_head,
        "metadata": metadata_dict,
        "authority": authority,
        "state": state.value,
        "warnings": warnings,
    }
    forensic_hash = _sha256_bytes(canonical_json(payload).encode("utf-8"))
    receipt = evidence_receipt(
        "FORENSIC_EVIDENCE",
        inputs={
            "evidence_id": evidence_id,
            "kind": kind.value,
            "locator": locator,
            "artifact_hash": digest,
        },
        outputs={
            "state": state.value,
            "hash_verified": hash_verified,
            "custody_valid": custody_verification.valid,
            "custody_chain_head": custody_verification.chain_head,
            "authority": authority,
            "forensic_hash": forensic_hash,
        },
    )

    return ForensicEvidence(
        evidence_id=evidence_id,
        kind=kind,
        locator=locator,
        artifact_hash=digest,
        expected_hash=expected_hash,
        hash_verified=hash_verified,
        custody=chain,
        custody_verification=custody_verification,
        metadata=metadata_dict,
        authority=authority,
        state=state,
        warnings=tuple(warnings),
        forensic_hash=forensic_hash,
        receipt=receipt,
    )


def verify_logical_copy(
    source_path: str | Path,
    copy_path: str | Path,
    *,
    evidence_id: str,
    custody: Iterable[CustodyEvent] = (),
    metadata: Mapping[str, Any] | None = None,
) -> ForensicEvidence:
    """Verify byte equality of an already-created logical copy.

    This function intentionally does not perform acquisition and does not claim
    hardware write-blocking. It reads both paths and compares SHA-256 digests.
    """
    source_digest = sha256_file(source_path)
    copy_digest = sha256_file(copy_path)
    meta = dict(metadata or {})
    meta.update(
        {
            "source_path": str(source_path),
            "copy_path": str(copy_path),
            "source_sha256": source_digest,
            "copy_sha256": copy_digest,
            "acquisition_claim": "LOGICAL_COPY_ALREADY_CREATED",
        }
    )
    return build_forensic_evidence(
        evidence_id=evidence_id,
        kind=ForensicArtifactKind.LOGICAL_COPY,
        locator=str(copy_path),
        artifact_hash=copy_digest,
        expected_hash=source_digest,
        custody=custody,
        metadata=meta,
    )
