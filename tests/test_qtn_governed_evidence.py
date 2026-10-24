"""Adversarial regression: PQC meaning, SQLite across runs, byte-level claim receipts.

All scientific claims in this file are synthetic test fixtures.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys

import pytest

from app.qtn_watch import SourceItem, classify_qtn, evaluate
from app.qtn_context import resolve_pqc
from app.qtn_revision_ledger import record_observations
from app.qtn_evidence_binding import bind_claim_excerpt


def src(version: str, summary: str = "Quantum network fidelity"):
    return SourceItem("arXiv", "Quantum network benchmark",
                      f"https://arxiv.org/abs/2610.11658{version}",
                      summary=summary)


def test_pqc_parameterized_quantum_circuit_avoids_crypto_families():
    item = SourceItem("arXiv", "PQC parameterized quantum circuit for Bayesian inversion",
                      "https://arxiv.org/abs/2610.18942v1",
                      summary="Quantum-classical optimization uses a parametrized quantum circuit.")
    mapped = set(classify_qtn(item))
    assert "QTN-007" not in mapped
    assert "QTN-011" not in mapped
    assert "QTN-017" in mapped
    assert resolve_pqc(item.title + " " + item.summary) == "PARAMETERIZED_QUANTUM_CIRCUIT"


def test_ambiguous_bare_pqc_never_grants_crypto_relation():
    item = SourceItem("arXiv", "A PQC experiment", "https://arxiv.org/abs/2610.18942v1")
    assert resolve_pqc(item.title) == "AMBIGUOUS_PQC"
    assert "QTN-007" not in classify_qtn(item)
    assert "QTN-011" not in classify_qtn(item)


def test_explicit_cryptographic_pqc_keeps_crypto_relations():
    item = SourceItem("arXiv", "PQC post-quantum cryptography migration",
                      "https://arxiv.org/abs/2610.27111v1")
    assert {"QTN-007", "QTN-011"}.issubset(set(classify_qtn(item)))


def test_conflicting_expansions_do_not_infer_crypto_from_bare_pqc():
    item = SourceItem("arXiv", "PQC in two fields",
                      "https://arxiv.org/abs/2610.18942v1",
                      summary="PQC is a parameterized quantum circuit and another context mentions post-quantum cryptography")
    assert resolve_pqc(item.title + " " + item.summary) == "CONFLICTING_EXPANSIONS_REVIEW"
    # Legitimate explicit 'post-quantum' still maps; the ambiguous acronym
    # itself is never the sole basis for a cryptographic family.
    assert "QTN-011" in classify_qtn(item)


def test_legacy_qtn_explicit_terms_remain_compatible():
    item = SourceItem("arXiv", "Error correction for quantum network fidelity",
                      "https://arxiv.org/abs/2610.23840v1")
    assert {"QTN-001", "QTN-008", "QTN-012"}.issubset(set(classify_qtn(item)))
    assert evaluate([item], threshold=0.0)


def test_ledger_new_work_new_version_and_repeat_across_connections(tmp_path):
    db = tmp_path / "revisions.sqlite3"
    first = record_observations(db, [src("v1")], observed_at="2026-10-24T12:00:00+00:00")
    assert first[0]["status"] == "NEW_WORK"
    second = record_observations(db, [src("v2")], observed_at="2026-10-24T12:01:00+00:00")
    assert second[0]["status"] == "NEW_VERSION"
    again = record_observations(db, [src("v2")])
    assert again[0]["status"] == "SEEN_AGAIN"
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM qtn_observations").fetchone()[0] == 2


def test_ledger_persists_across_separate_processes(tmp_path):
    db = tmp_path / "restart.sqlite3"
    statement = (
        "import sys; from app.qtn_revision_ledger import record_observations;"
        "from app.qtn_watch import SourceItem;"
        "s=SourceItem('arXiv','Sample',sys.argv[2],summary='Quantum network');"
        "print(record_observations(sys.argv[1],[s])[0]['status'])"
    )
    def run(version):
        result = subprocess.run(
            [sys.executable, "-c", statement, str(db),
             f"https://arxiv.org/abs/2610.11658{version}"],
            text=True, capture_output=True, timeout=15, check=True,
        )
        return result.stdout.strip()
    assert run("v1") == "NEW_WORK"
    assert run("v2") == "NEW_VERSION"
    assert run("v2") == "SEEN_AGAIN"


def test_ledger_changed_metadata_is_not_false_claim_change(tmp_path):
    db = tmp_path / "versions.sqlite3"
    one, two = src("v1", "Trial outcome A"), src("v1", "Trial outcome B")
    assert record_observations(db, [one])[0]["status"] == "NEW_WORK"
    change = record_observations(db, [two])[0]
    assert change["status"] == "VERSION_METADATA_VARIATION"
    assert change["claim_adjudication"] == "HOLD_MANUAL_ADJUDICATION"


def test_ledger_does_not_fabricate_version_from_latest_url(tmp_path):
    db = tmp_path / "versions.sqlite3"
    latest = SourceItem("arXiv", "Research", "https://arxiv.org/abs/2610.11658")
    result = record_observations(db, [latest])
    assert result[0]["status"] == "HOLD_UNPINNED_OR_UNKNOWN_VERSION"
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM qtn_observations").fetchone()[0] == 0


def test_ledger_rejects_invalid_time_or_missing_parent(tmp_path):
    with pytest.raises(ValueError):
        record_observations(tmp_path / "missing" / "db.sqlite3", [src("v1")])
    with pytest.raises(ValueError):
        record_observations(tmp_path / "db.sqlite3", [src("v1")], observed_at="2026-10-24")


def test_claim_excerpt_byte_span_and_hash_receipt():
    body = "Research fixture\nClaim: simulated fidelity 0.87\nResults are preliminary.\n".encode()
    excerpt = "Claim: simulated fidelity 0.87"
    sha = hashlib.sha256(body).hexdigest()
    rec = bind_claim_excerpt(
        source_url="https://arxiv.org/abs/2610.11658v1",
        document_bytes=body, expected_sha256=sha, exact_excerpt=excerpt)
    assert rec["source_version_id"] == "arxiv:2610.11658v1"
    assert body[rec["byte_start"]:rec["byte_end"]] == excerpt.encode()
    assert rec["content_sha256"] == sha
    assert rec["gate_authorization"] == "NOT_GRANTED"
    assert rec["claim_veracity"].startswith("HOLD")
    assert rec["origin_authentication"].startswith("HOLD")


def test_tampered_document_rejected():
    body = b"Claim: simulated fidelity 0.87"
    bad = body.replace(b"0.87", b"0.99")
    digest = hashlib.sha256(body).hexdigest()
    with pytest.raises(ValueError, match="digest mismatch"):
        bind_claim_excerpt(source_url="https://arxiv.org/abs/2610.11658v1",
                           document_bytes=bad, expected_sha256=digest,
                           exact_excerpt="Claim: simulated fidelity 0.99")


def test_pinned_version_required_for_claim_receipt():
    body = b"Claim: a"
    with pytest.raises(ValueError, match="pinned"):
        bind_claim_excerpt(source_url="https://arxiv.org/abs/2610.11658",
                           document_bytes=body,
                           expected_sha256=hashlib.sha256(body).hexdigest(),
                           exact_excerpt="Claim: a")


def test_claim_missing_or_ambiguous_excerpt_rejected():
    data = b"Claim: a\nClaim: a\n"
    digest = hashlib.sha256(data).hexdigest()
    for excerpt in ("Claim: b", "Claim: a"):
        with pytest.raises(ValueError):
            bind_claim_excerpt(source_url="https://arxiv.org/abs/2610.11658v1",
                               document_bytes=data, expected_sha256=digest,
                               exact_excerpt=excerpt)


def test_sha256_is_not_publisher_authentication():
    # Caller controls both data and expected hash: valid integrity still HOLD.
    forged = b"Claim: this fictional source is not publisher-authenticated"
    rec = bind_claim_excerpt(source_url="https://arxiv.org/abs/2610.11658v1",
                             document_bytes=forged,
                             expected_sha256=hashlib.sha256(forged).hexdigest(),
                             exact_excerpt=forged.decode())
    assert rec["integrity_result"] == "MATCHES_CALLER_SUPPLIED_DIGEST"
    assert rec["origin_authentication"] == "HOLD_NO_TRUSTED_EXTERNAL_ROOT"
    assert rec["claim_veracity"] == "HOLD_MANUAL_SCIENTIFIC_ADJUDICATION"


def test_binary_non_utf8_not_misread_as_text():
    data = b"\xff\xfeClaim"
    with pytest.raises(UnicodeDecodeError):
        bind_claim_excerpt(source_url="https://arxiv.org/abs/2610.11658v1",
                           document_bytes=data, expected_sha256=hashlib.sha256(data).hexdigest(),
                           exact_excerpt="Claim")


def test_cli_requires_explicit_semantic_review_and_never_publishes():
    for args in (
        ("--json", "--revision-ledger", "local.sqlite3"),
        ("--json", "--semantic-review", "--revision-ledger", "local.sqlite3",
         "--github-issues"),
    ):
        proc = subprocess.run([sys.executable, "scripts/run_qtn_watch.py", *args],
                              capture_output=True, text=True, timeout=10)
        assert proc.returncode != 0
        assert "error:" in proc.stderr
