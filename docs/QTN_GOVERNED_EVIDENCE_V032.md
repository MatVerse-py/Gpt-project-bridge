# QTN — contextual classification, longitudinal revision ledger, and claim receipts

Experimental appendix to [QTN_TSC_SEMANTIC_REVIEW_V031.md](QTN_TSC_SEMANTIC_REVIEW_V031.md), PR #106. **No canonization, production release, or Gate authorization**.

## 1 — Context-sensitive QTN family matching

`app/qtn_context.py` expands `PQC` using explicit title/summary cues. The bare acronym **does not** justify cryptographic QTN-007/QTN-011 mappings; post-quantum cryptography or an explicit algorithm identifier still maps. Parameterized/parameterised quantum circuits can map to QTN-017. An ambiguous or conflicting expansion is not promoted to crypto *merely* because of the acronym. Topic detection is a heuristic; any Source→QTN relation still needs independent scientific adjudication.

## 2 — SQLite revision observations

The watcher now supports opt-in, **read-only discovery mode** with a caller-supplied persistent path:

```shell
python scripts/run_qtn_watch.py --json --semantic-review --revision-ledger /mounted-durable/qtn-revisions.sqlite3
```

A SQLite transaction records pinned source-version IDs and fingerprints of *declared metadata* (title/summary/source/URL). Events:
`NEW_WORK`, `NEW_VERSION`, `SEEN_AGAIN`, `VERSION_METADATA_VARIATION`, and `HOLD_UNPINNED_OR_UNKNOWN_VERSION`.

The same SQLite file survives process restarts. **It does not survive ephemeral GitHub-hosted runners unless the operator provisions externally durable, backed-up storage.** The scheduled GitHub Actions production workflow does not enable this flag. This version does not publish new revision alerts, create issues automatically, fetch article bytes or assert a scientific conclusion changed. `VERSION_METADATA_VARIATION` is not `CLAIM_CHANGED`. Restrict access to the ledger and use a protected volume in later deployment.

The legacy `--github-issues` flow is deliberately incompatible with `--semantic-review`; this remains true with the ledger, preventing accidental publication. During migration, never interpret work-level alert dedup as a version-event detector.

## 3 — Exact UTF-8 excerpt receipt

Standalone local offline command:

```shell
python scripts/qtn_bind_excerpt.py --file /path/source.txt --source-url https://arxiv.org/abs/2610.11658v1 --expected-sha256 '<trusted-externally-obtained-sha256>' --excerpt 'exact quote'
```

Receipt binds *observed UTF-8 bytes* to a pinned source version URI, a caller-supplied SHA-256 expectation, and a unique exact quote span. Fail closed on binary input, non-pinned URL, wrong digest, absent or repeated quote.

A hash match proves only agreement with the **caller-supplied digest**. The code cannot establish where that digest came from, who signed the document, whether the publisher authored the material, whether the quoted claim is true or whether Gate authorizes an action. Fields `origin_authentication`, `claim_veracity` remain **HOLD** and `gate_authorization` is `NOT_GRANTED`. This first iteration is UTF-8 text only; PDF/HTML extraction, independent roots of trust, claim graph, and external witness are future HOLDs.

## Boundaries, controls and promotion

- PR #106 remains **draft**, `main` unchanged, human reviewer required.
- Python code/tests and opt-in CLI are not a deployed durable service.
- The TSC v0.3.1 policy engine is not enabled as Ω-Gate authority.
- No live article-body attestation or independent scientific replication.
- Adversarial fixtures reverse a hypothetical result across revisions. **They are not allegations about real articles.**
- Persistent ledger replication, source root authentication and signed receipts are **HOLD**.

Focused tests:
```shell
python -m pytest -q tests/test_qtn_watch.py tests/test_qtn_semantic_review.py tests/test_qtn_source_lineage.py tests/test_qtn_cross_pr_integration.py tests/test_qtn_governed_evidence.py
```

Promotion only after fully green CI, external source adjudication, durable cross-run state, publisher authentication and independently verified scientific/effect claims. Keep authorization independent of semantic review.
