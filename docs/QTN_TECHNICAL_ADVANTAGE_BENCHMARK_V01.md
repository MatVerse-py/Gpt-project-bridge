# QTN/TSC — comparative technical-effect benchmark v0.1

Status: **controlled self-authored engineering experiment**. NOT independent scientific validation, a patent novelty conclusion, or a comparison against fully implemented SHACL/OpenLineage/Pandera.

Implementation: benchmarks/qtn_technical_comparison.py; tests/test_qtn_technical_comparison.py.

## Scope and controls

| Task | Lightweight baseline | Integrated mechanism | Metric |
|---|---|---|---|
| Crypto-topic QTN-011 | Literal substring match, reproducing #95 QTN-011 terms | Contextual PQC + classify_qtn | TP/FP/FN/TN, precision, recall |
| New version event | #95 work-digest-only; and stronger version-ID memory set | SQLite revision ledger | Ordered event accuracy, new-version recall |
| Pinned text excerpt | Metadata fingerprint; and stronger SHA-256-only check | Version + bytes + expected hash + exact unique excerpt | Acceptance confusion matrix |

The lexical reference reimplements the algorithm from #95, app/qtn_watch.py pinned blob 61d91240fa1d355dcd477c0c66ae117280f68cf9. The work-level dedup comparison invokes the actual _digest from the integrated branch; the stronger version-ID comparator is disclosed. These are **algorithms**, not benchmarks of complete third-party products.

The fixed hand-authored fixture corpus includes explicit cryptography, parametrized quantum circuits, undecidable bare PQC, negated mentions, altered IETF/arXiv revisions and altered UTF-8 bytes. All input cases, gold labels and per-case predictions are exported. Ground truth was authored by the same developers, not independently adjudicated. No confidence interval or generalization to unknown real articles is warranted.

## Execute

Run from repository root:

    python -m benchmarks.qtn_technical_comparison --output /tmp/qtn-benchmark.json --summary-only
    python -m pytest -q tests/test_qtn_technical_comparison.py

On pull requests, the QTN workflow runs the benchmark and uploads JSON if Actions upload succeeds. Report the complete confusion matrix and all negative results, not only a selected percentage. Call times are crude Python microbenchmarks, not production latency SLAs.

## Why no claim over full industry alternatives yet

- W3C SHACL https://www.w3.org/TR/shacl/ validates RDF graphs against shapes, including extensible constraints. A real SHACL comparator must use equivalent rules, source information and scope.
- OpenLineage https://openlineage.io/docs/spec/facets/ provides run/job/dataset provenance and configurable facets. It may support durable version-aware lineage when appropriately instrumented. A full implementation has not been tested here.
- Pandera https://pandera.readthedocs.io/en/stable/dataframe_schemas.html validates typed datasets with extensible checks, a different scope from automatic scientific-topic classification.
- W3C PROV-O, RO-Crate and existing signed evidence systems may duplicate or surpass parts of these functions after fair engineering configuration.

No percentages from narrow reference algorithms may be asserted as performance advantages relative to these complete tools.

## Promotion prerequisites

Independent, blinded, source-backed labels; task-matched real implementations with equal evidence and trust roots; persistent-storage parity; false authorization and false block measurements; end-to-end latency/resource budgets; predefined split with held-out sources; uncertainty estimates; replication by another operator and documented negative results.

VERDICT: CONTROLLED_COMPARATIVE_CI_ONLY / EXTERNAL_ADVANTAGE_HOLD / SCIENTIFIC_HOLD / AUTHORISATION_HOLD. Do not infer merge or patent readiness.
