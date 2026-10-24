"""QTN/TSC controlled technical comparison v0.1.

Benchmarks (a) earlier lexical QTN-011 lookup vs current contextual classification,
(b) work-level dedup and version-aware in-memory baselines vs SQLite observations,
(c) metadata-only and bytes-only checks vs SHA-256+version+excerpt binding.

These are *authored adversarial fixtures*, not independent field-data evidence
or benchmarks of W3C SHACL, OpenLineage, or other real external implementations.
Results never imply scientific truth or publication authenticity.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import statistics
import tempfile
from time import perf_counter_ns

from app.qtn_watch import QTN_RULES, SourceItem, _digest, classify_qtn
from app.qtn_revision_ledger import record_observations
from app.qtn_source_lineage import pointer_identity
from app.qtn_evidence_binding import bind_claim_excerpt

BENCHMARK_ID = "qtn-technical-comparison-0.1"
BENCHMARK_KIND = "AUTHOR_CREATED_CONTROLLED_FIXTURES_NOT_INDEPENDENT_REPLICATION"


@dataclass(frozen=True)
class Case:
    label: str
    text: str
    crypto: bool
    category: str


# Fixed corpus: no stochastic rephrasing, no modification based on results.
# "crypto" means should infer the QTN-011 *cryptographic topic*, not authorisation.
SEMANTIC_CASES = (
    Case("c01", "Post-quantum cryptography key exchange", True, "explicit_crypto"),
    Case("c02", "ML-KEM cryptographic standard migration", True, "explicit_crypto"),
    Case("c03", "ML-DSA signatures and key management", True, "explicit_crypto"),
    Case("c04", "SLH-DSA quantum-safe cryptography", True, "explicit_crypto"),
    Case("c05", "A survey of Kyber algorithms", True, "explicit_crypto"),
    Case("c06", "PQC post-quantum cryptography deployment", True, "explicit_crypto"),
    Case("c07", "PQC and ML-KEM implementation benchmarks", True, "explicit_crypto"),
    Case("c08", "PQC with post-quantum signature proofs", True, "explicit_crypto"),
    Case("c09", "Dilithium digital signatures evaluation", True, "explicit_crypto"),
    Case("c10", "Post-quantum authentication protocols", True, "explicit_crypto"),
    Case("c11", "Public-key transport using post-quantum cryptographic schemes", True, "explicit_crypto"),
    Case("c12", "Safety analysis of post-quantum key encapsulation", True, "explicit_crypto"),
    Case("n01", "PQC parameterized quantum circuit for Bayesian inversion", False, "circuit"),
    Case("n02", "Parameterized quantum circuit PQC for molecular energy", False, "circuit"),
    Case("n03", "Parametrized quantum circuit and PQC optimization", False, "circuit"),
    Case("n04", "Parameterised quantum circuit PQC in variational chemistry", False, "circuit"),
    Case("n05", "PQC variational quantum circuit for image analysis", False, "circuit"),
    Case("n06", "PQC parameterized quantum circuit in neural surrogates", False, "circuit"),
    Case("n07", "A PQC circuit ansatz for combinatorial optimization", False, "bare_pqc"),
    Case("n08", "PQC latency on near-term quantum devices", False, "bare_pqc"),
    Case("n09", "PQC results in condensed matter simulations", False, "bare_pqc"),
    Case("n10", "PQC architecture, preliminary research", False, "bare_pqc"),
    Case("n11", "PQC is an undefined abbreviation in this title", False, "bare_pqc"),
    Case("n12", "PQC speed-up claims in hybrid solvers", False, "bare_pqc"),
    Case("n13", "Variational molecular simulation with no cryptographic primitives", False, "unrelated"),
    Case("n14", "Numerical tensor contraction error bounds", False, "unrelated"),
    Case("n15", "Quantum internet repeater routing", False, "unrelated"),
    Case("n16", "Graph shortest paths and scheduling", False, "unrelated"),
    # Hard negatives: lexical AND the present contextual matcher can still fail.
    Case("h01", "We reject post-quantum cryptography as irrelevant to this study", False, "negation"),
    Case("h02", "Parameterized quantum circuit PQC contrasted with post-quantum cryptography", False, "mixed_topic"),
    Case("h03", "Not an ML-KEM implementation: we only benchmark error correction", False, "negation"),
    Case("h04", "The phrase post-quantum cryptography appears only in related work", False, "mentioned_only"),
)


def confusion(gold: list[bool], pred: list[bool]) -> dict:
    tp = sum(g and p for g, p in zip(gold, pred))
    fp = sum(not g and p for g, p in zip(gold, pred))
    fn = sum(g and not p for g, p in zip(gold, pred))
    tn = sum(not g and not p for g, p in zip(gold, pred))
    assert tp + fp + fn + tn == len(gold)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    return {"n": len(gold), "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": precision, "recall": recall,
            "accuracy": (tp + tn) / len(gold),
            "false_positive_rate": fp / (fp + tn) if fp + tn else None}


def semantic_benchmark() -> dict:
    items = [
        SourceItem("arXiv", c.text,
                   f"https://arxiv.org/abs/2610.{18000 + i:05d}v1")
        for i, c in enumerate(SEMANTIC_CASES)
    ]
    gold = [c.crypto for c in SEMANTIC_CASES]
    crypto_terms = QTN_RULES["QTN-011"]
    # Frozen PR #95 lookup: raw substring against title + URL (+ summary).
    lexical = [any(term in f"{item.title} {item.summary} {item.url}".lower()
                   for term in crypto_terms) for item in items]
    contextual = ["QTN-011" in classify_qtn(item) for item in items]
    return {
        "task": "infer cryptographic topic association (QTN-011), not proof or authority",
        "baseline": "PR95-style lexical QTN-011 term lookup; comparator is a code-level reproduction",
        "comparator_code": "any(term in title_summary_url for term in QTN_RULES['QTN-011'])",
        "baseline_metrics": confusion(gold, lexical),
        "integrated_metrics": confusion(gold, contextual),
        "cases": [
            {"id": c.label, "category": c.category, "gold": c.crypto,
             "baseline": lexical[i], "integrated": contextual[i]}
            for i, c in enumerate(SEMANTIC_CASES)
        ],
    }


def version_sequence() -> list[tuple[SourceItem, str]]:
    return [
        (SourceItem("arXiv", "Study A", "https://arxiv.org/abs/2610.11658v1", summary="Result A"), "NEW_WORK"),
        (SourceItem("arXiv", "Study A", "https://arxiv.org/abs/2610.11658v1", summary="Result A"), "SEEN_AGAIN"),
        (SourceItem("arXiv", "Study A", "https://arxiv.org/abs/2610.11658v2", summary="Result B"), "NEW_VERSION"),
        (SourceItem("arXiv", "Study A", "https://arxiv.org/abs/2610.11658v2", summary="Corrected B"), "VERSION_METADATA_VARIATION"),
        (SourceItem("arXiv", "Study A", "https://arxiv.org/abs/2610.11658v3", summary="Result C"), "NEW_VERSION"),
        (SourceItem("IETF", "Draft A", "https://datatracker.ietf.org/doc/draft-ietf-pquip-pqc-hsm-constrained/08/", summary="Rev 08"), "NEW_WORK"),
        (SourceItem("IETF", "Draft A", "https://datatracker.ietf.org/doc/draft-ietf-pquip-pqc-hsm-constrained/09/", summary="Rev 09"), "NEW_VERSION"),
        (SourceItem("IETF", "Draft A", "https://datatracker.ietf.org/doc/draft-ietf-pquip-pqc-hsm-constrained/09/", summary="Rev 09"), "SEEN_AGAIN"),
        (SourceItem("arXiv", "Study A", "https://arxiv.org/abs/2610.11658", summary="floating URL"), "HOLD_UNPINNED_OR_UNKNOWN_VERSION"),
        (SourceItem("arXiv", "Study A Revised", "https://arxiv.org/abs/2610.11658v1", summary="Result A"), "VERSION_METADATA_VARIATION"),
        (SourceItem("arXiv", "Study B", "https://arxiv.org/abs/2610.14678v1", summary="Unrelated"), "NEW_WORK"),
        (SourceItem("arXiv", "Study B", "https://arxiv.org/abs/2610.14678v1", summary="Unrelated"), "SEEN_AGAIN"),
        (SourceItem("IETF", "Draft A", "https://datatracker.ietf.org/doc/draft-ietf-pquip-pqc-hsm-constrained/10/", summary="Rev 10"), "NEW_VERSION"),
        (SourceItem("arXiv", "Study C", "https://arxiv.org/abs/2610.18881v1", summary="First"), "NEW_WORK"),
    ]


def version_benchmark() -> dict:
    seen_works: set[str] = set()
    seen_versions: set[str] = set()
    baseline: list[str] = []
    integrated: list[str] = []
    seq = version_sequence()
    with tempfile.TemporaryDirectory() as root:
        database = Path(root) / "qtn-versions.sqlite3"
        for item, _ in seq:
            work_key = _digest(item)
            baseline.append("SEEN_AGAIN" if work_key in seen_works else "NEW_WORK")
            seen_works.add(work_key)
            integrated.append(record_observations(database, [item])[0]["status"])
    # Separate in-memory version-aware reference, with no persistent state.
    naive_revision: list[str] = []\n    version_seen: set[str] = set()
    work_seen: set[str] = set()
    for item, _ in seq:
        p = pointer_identity(item.url)
        w, v = p["work_id"], p["version_id"]
        if v is None:
            naive_revision.append("HOLD_UNPINNED_OR_UNKNOWN_VERSION")
        elif v in version_seen:
            naive_revision.append("SEEN_AGAIN")
        elif w in work_seen:
            naive_revision.append("NEW_VERSION")
        else:
            naive_revision.append("NEW_WORK")
        if v:
            version_seen.add(v)
            work_seen.add(w)
    gold = [expected for _, expected in seq]
    accuracy = lambda xs: sum(a == b for a, b in zip(xs, gold)) / len(gold)
    revision_total = sum(g == "NEW_VERSION" for g in gold)
    recall = lambda xs: sum(x == "NEW_VERSION" and g == "NEW_VERSION"
                            for x, g in zip(xs, gold)) / revision_total
    return {
        "task": "identify document version events across ordered observations",
        "reference_work_dedup": "PR95 work-level digest applied to an in-memory prior-alert set",
        "reference_version_only": "basic version-ID set in memory (purpose-built comparator, no SQLite)",
        "baseline_work_event_accuracy": accuracy(baseline),
        "baseline_work_new_version_recall": recall(baseline),
        "baseline_version_event_accuracy": accuracy(naive_revision),
        "baseline_version_new_version_recall": recall(naive_revision),
        "integrated_event_accuracy": accuracy(integrated),
        "integrated_new_version_recall": recall(integrated),
        "cases": [
            {"step": i + 1, "gold": gold[i], "work_only": baseline[i],
             "version_only": naive_revision[i], "integrated": integrated[i]}
            for i in range(len(gold))
        ],
        "limitation": "SQLite uses a local temp file here; this does not benchmark a multi-host durable service.",
    }


def receipt_benchmark() -> dict:
    doc = b"Title: Example\nClaim: simulator fidelity is 0.87\nScope: synthetic\n"
    sha = sha256(doc).hexdigest()
    alternate = b"Title: Example\nClaim: simulator fidelity is 0.99\nScope: synthetic\n"
    adversarial = [
        ("ok-original", doc, sha, "Claim: simulator fidelity is 0.87", True, True),
        ("tampered-body", alternate, sha, "Claim: simulator fidelity is 0.99", False, True),
        ("wrong-expected-digest", doc, "0" * 64, "Claim: simulator fidelity is 0.87", False, True),
        ("missing-excerpt", doc, sha, "Claim: simulator fidelity is 0.99", False, True),
        ("ambiguous-excerpt", b"Claim: x\nClaim: x\n", sha256(b"Claim: x\nClaim: x\n").hexdigest(), "Claim: x", False, True),
        ("unversioned", doc, sha, "Claim: simulator fidelity is 0.87", False, False),
        ("spoofed-origin", doc, sha, "Claim: simulator fidelity is 0.87", False, False),
        ("wrong-scheme", doc, sha, "Claim: simulator fidelity is 0.87", False, False),
        ("unrelated-excerpt", doc, sha, "Text absent", False, True),
        ("valid-substring", doc, sha, "Scope: synthetic", True, True),
        ("valid-other-document", alternate, sha256(alternate).hexdigest(), "Claim: simulator fidelity is 0.99", True, True),
        ("valid-title", doc, sha, "Title: Example", True, True),
    ]
    results = []
    base_url = "https://arxiv.org/abs/2610.11658v1"
    for label, data, expected, excerpt, gold, pinned in adversarial:
        url = base_url if pinned else (
            "https://arxiv.org/abs/2610.11658" if label == "unversioned"
            else "https://arxiv.org.attacker.invalid/abs/2610.11658v1"
            if label == "spoofed-origin" else "http://arxiv.org/abs/2610.11658v1"
        )
        baseline_meta = True  # supplied source metadata deliberately unchanged
        baseline_sha = sha256(data).hexdigest() == expected
        try:
            result = bind_claim_excerpt(
                source_url=url, document_bytes=data,
                expected_sha256=expected, exact_excerpt=excerpt
            )
            integrated = result["integrity_result"] == "MATCHES_CALLER_SUPPLIED_DIGEST"
        except (ValueError, UnicodeError):
            integrated = False
        results.append((label, gold, baseline_meta, baseline_sha, integrated))
    return {
        "task": "reject invalid byte+version+exact-excerpt bindings, not prove the truth of the excerpt",
        "baseline_metadata_only": "unchanged source metadata fingerprint; does not inspect bytes",
        "baseline_sha_only": "body SHA-256 comparison only; no version or excerpt binding",
        "metadata_metrics": confusion([x[1] for x in results], [x[2] for x in results]),
        "sha_only_metrics": confusion([x[1] for x in results], [x[3] for x in results]),
        "integrated_metrics": confusion([x[1] for x in results], [x[4] for x in results]),
        "cases": [{"id": x[0], "gold": x[1], "metadata": x[2],
                   "sha_only": x[3], "integrated": x[4]} for x in results],
        "limitation": "Caller-supplied matching digest is not an authenticated publisher root; this task tests integrity predicates only.",
    }


def timing_benchmark(*, repetitions: int = 300) -> dict:
    item = SourceItem("arXiv", "PQC parameterized quantum circuit for inversion",
                      "https://arxiv.org/abs/2610.18942v1")
    text = f"{item.title} {item.summary} {item.url}".lower()
    terms = QTN_RULES["QTN-011"]
    funcs = {
        "lexical_lookup": lambda: any(term in text for term in terms),
        "contextual_qtn": lambda: "QTN-011" in classify_qtn(item),
    }
    readings = {}
    for key, fn in funcs.items():
        for _ in range(30):
            fn()
        times = []
        for _ in range(repetitions):
            a = perf_counter_ns()
            fn()
            times.append(perf_counter_ns() - a)
        readings[key] = {
            "median_ns": statistics.median(times),
            "p95_ns": sorted(times)[int(0.95 * (len(times) - 1))],
        }
    return {
        "trials": repetitions, "machine": platform.platform(),
        "python": platform.python_version(),
        "units": "nanoseconds per function call, sequential microbench, approximate",
        "results": readings, "limitations": "No CPU isolation; no I/O or end-to-end workload. Never use as latency SLA.",
    }


def calculate(*, timing: bool = True) -> dict:
    semantic = semantic_benchmark()
    versions = version_benchmark()
    evidence = receipt_benchmark()
    payload = {
        "benchmark_id": BENCHMARK_ID,
        "kind": BENCHMARK_KIND,
        "git_sha": os.environ.get("GITHUB_SHA", "local-unpinned"),
        "definitions": {
            "gold": "Author-assigned labels; fixtures are not independently adjudicated",
            "semantic_positive": "Article should be associated with QTN-011 cryptography topic",
            "version_event": "Directly observed work/version/metadata identity state",
            "receipt_positive": "Version-pinned body bytes, digest and unique excerpt agree",
            "scope": "Comparators are narrow algorithms matching individual tasks, NOT actual SHACL/OpenLineage/Pandera systems",
        },
        "semantic": semantic,
        "version_events": versions,
        "evidence_bindings": evidence,
        "timing": timing_benchmark() if timing else None,
    }
    payload["case_count"] = (
        semantic["baseline_metrics"]["n"]
        + len(versions["cases"])
        + evidence["integrated_metrics"]["n"]
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--skip-timing", action="store_true")
    parser.add_argument("--summary-only", action="store_true", help="Print concise metrics; --output still saves full data")
    args = parser.parse_args()
    result = calculate(timing=not args.skip_timing)
    serialized = json.dumps(result, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    if args.summary_only:
        m = result["semantic"]
        v = result["version_events"]
        e = result["evidence_bindings"]
        brief = {
            "benchmark_id": result["benchmark_id"],
            "case_count": result["case_count"],
            "semantic_baseline": m["baseline_metrics"],
            "semantic_integrated": m["integrated_metrics"],
            "work_version_recall": v["baseline_work_new_version_recall"],
            "memory_version_recall": v["baseline_version_new_version_recall"],
            "sqlite_version_recall": v["integrated_new_version_recall"],
            "receipt_metadata_accuracy": e["metadata_metrics"]["accuracy"],
            "receipt_sha_only_accuracy": e["sha_only_metrics"]["accuracy"],
            "receipt_integrated_accuracy": e["integrated_metrics"]["accuracy"],
        }
        print(json.dumps(brief, ensure_ascii=False, sort_keys=True))
    else:
        print(serialized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
