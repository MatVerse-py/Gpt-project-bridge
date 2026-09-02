from dataclasses import replace
from pathlib import Path
import shutil

from app.forensic_evidence import (
    CustodyEventType,
    ForensicArtifactKind,
    ForensicState,
    append_custody_event,
    build_forensic_evidence,
    create_custody_event,
    entropy_observation,
    sha256_file,
    shannon_entropy_bytes,
    verify_custody_chain,
    verify_logical_copy,
)


def test_custody_hash_chain_detects_tampering():
    first = create_custody_event(
        sequence=0,
        event_type=CustodyEventType.ACQUIRED,
        evidence_id="ev-1",
        actor="collector-a",
        input_hash="a" * 64,
        output_hash="a" * 64,
        observed_at="2026-09-02T12:00:00Z",
    )
    chain = append_custody_event(
        (first,),
        event_type=CustodyEventType.HASHED,
        evidence_id="ev-1",
        actor="collector-a",
        input_hash="a" * 64,
        output_hash="b" * 64,
        observed_at="2026-09-02T12:01:00Z",
    )
    assert verify_custody_chain(chain).valid

    tampered = replace(chain[1], metadata={"changed": True})
    verification = verify_custody_chain((chain[0], tampered))
    assert not verification.valid
    assert any("event_hash mismatch" in error for error in verification.errors)


def test_hash_chain_is_not_presented_as_signature():
    event = create_custody_event(
        sequence=0,
        event_type=CustodyEventType.ACQUIRED,
        evidence_id="ev-2",
        actor="collector-a",
        input_hash="c" * 64,
        output_hash="c" * 64,
        observed_at="2026-09-02T12:00:00Z",
    )
    assert event.event_hash
    assert not hasattr(event, "signature")


def test_verified_logical_copy_proves_integrity_not_write_blocking(tmp_path: Path):
    source = tmp_path / "source.bin"
    copy = tmp_path / "copy.bin"
    source.write_bytes(b"matverse forensic evidence\x00" * 64)
    shutil.copyfile(source, copy)

    evidence = verify_logical_copy(source, copy, evidence_id="copy-1")

    assert evidence.state is ForensicState.INTEGRITY_VERIFIED
    assert evidence.hash_verified
    assert evidence.authority["integrity"] >= 0.95
    assert evidence.authority["custody"] == 0.0
    assert evidence.authority["publication"] == 0.0
    assert any("not a hardware write blocker" in warning for warning in evidence.warnings)


def test_hash_mismatch_fails_closed():
    evidence = build_forensic_evidence(
        evidence_id="mismatch",
        kind=ForensicArtifactKind.LOGICAL_COPY,
        locator="memory://copy",
        artifact_bytes=b"changed",
        expected_hash="0" * 64,
    )
    assert evidence.state is ForensicState.BLOCK_TAMPERED
    assert all(value == 0.0 for value in evidence.authority.values())


def test_verified_integrity_and_custody_produce_forensic_verified_receipt():
    payload = b"evidence bytes"
    digest = sha256_file.__globals__["_sha256_bytes"](payload)
    first = create_custody_event(
        sequence=0,
        event_type=CustodyEventType.ACQUIRED,
        evidence_id="ev-3",
        actor="collector-a",
        input_hash=digest,
        output_hash=digest,
        observed_at="2026-09-02T12:00:00Z",
    )
    chain = append_custody_event(
        (first,),
        event_type=CustodyEventType.HASHED,
        evidence_id="ev-3",
        actor="collector-a",
        input_hash=digest,
        output_hash=digest,
        observed_at="2026-09-02T12:01:00Z",
    )

    evidence = build_forensic_evidence(
        evidence_id="ev-3",
        kind=ForensicArtifactKind.ORIGINAL_FILE,
        locator="memory://original",
        artifact_bytes=payload,
        expected_hash=digest,
        custody=chain,
    )

    assert evidence.state is ForensicState.FORENSIC_VERIFIED
    assert evidence.authority["integrity"] >= 0.95
    assert evidence.authority["custody"] >= 0.95
    assert evidence.receipt["schema"] == "matverse.evidence-receipt.v1"
    assert evidence.receipt["event_type"] == "FORENSIC_EVIDENCE"


def test_entropy_is_computed_observation_not_steganography_verdict(tmp_path: Path):
    target = tmp_path / "uniform.bin"
    target.write_bytes(bytes(range(256)) * 32)
    result = entropy_observation(target, block_size=1024)

    assert result["overall_entropy_bits_per_byte"] > 7.9
    assert result["interpretation"] == "COMPUTED_STATISTIC_ONLY"
    assert "does not establish" in result["warning"]


def test_carved_artifact_keeps_origin_claims_conservative():
    evidence = build_forensic_evidence(
        evidence_id="carved-1",
        kind=ForensicArtifactKind.CARVED_ARTIFACT,
        locator="memory://carved",
        artifact_bytes=b"%PDF-synthetic-carved-bytes",
        metadata={"parent_hash": "f" * 64, "offset": 4096},
    )

    assert evidence.state is ForensicState.PARTIAL
    assert evidence.authority["content"] <= 0.55
    assert evidence.authority["timestamp"] <= 0.05
    assert any("do not by themselves prove original filename" in warning for warning in evidence.warnings)


def test_filesystem_timestamps_are_not_max_authority_without_context():
    evidence = build_forensic_evidence(
        evidence_id="fs-1",
        kind=ForensicArtifactKind.FILE_SYSTEM_METADATA,
        locator="memory://metadata",
        artifact_bytes=b"metadata",
        metadata={"mtime_ns": 1, "ctime_ns": 2},
    )
    assert evidence.authority["timestamp"] <= 0.35

    contextual = build_forensic_evidence(
        evidence_id="fs-2",
        kind=ForensicArtifactKind.FILE_SYSTEM_METADATA,
        locator="memory://metadata-verified",
        artifact_bytes=b"metadata",
        metadata={"filesystem_context_verified": True},
    )
    assert contextual.authority["timestamp"] == 0.60


def test_shannon_entropy_empty_is_zero():
    assert shannon_entropy_bytes(b"") == 0.0
