# QTN-Watch × TSC semantic review (v0.3.1, experimental)

## Scope
This proposal adds a **non-authoritative, opt-in JSON overlay** to QTN Watch.
Legacy finding values, SHA/digest, issues and recommendations remain unchanged.
It does not import TSC policy evaluation into the bridge Gate.

Run it with:

    python scripts/run_qtn_watch.py --json --semantic-review

Regular QTN Watch continues to work exactly as before:

    python scripts/run_qtn_watch.py --json

The two flags --semantic-review and --github-issues cannot be combined. A review
failure raises an error rather than silently returning a successful classification.

## Why

QTN issue #104 called a formal verification arXiv preprint STANDARDIZATION,
while issue #102 contains an energy benchmarking preprint similarly classified.
Issue #98 classified a paper on a parameterized quantum circuit (PQC) as
SECURITY_MIGRATION: another field uses PQC for post-quantum cryptography.

The current QTN Watch impact classifier uses keyword matching on title, URL and
summary. Consequently source document *kind*, scientific subject and operational
recommendation are not interchangeable.

## Independent fields

- document_kind: PREPRINT / IETF_DRAFT / PUBLISHED_RFC / ...
- pointer_status: pinned version or mutable/latest URL
- pqc_expansion: parameterized circuit / post-quantum cryptography / ambiguous
- scientific_subject: research topic, not an authoritative review
- relation_status: REVIEW_REQUIRED for every inferred Source→QTN relation
- evidence_status: EXTERNAL_CONTENT_NOT_AUTHENTICATED
- semantic_review_verdict: HOLD_MANUAL_ADJUDICATION

All original finding fields are preserved. A published RFC is not necessarily a
standards-track RFC; a source URL is not document-body authentication.
A JSON metadata hash is not a source file content hash.

## Boundaries

- No Gate approval, cryptographic trust root, scientific verification or QPU validation.
- A title/URL exact join is required; duplicate source identities fail closed.
- Only domains/URL forms recognized as source pointers are classified.
- No modification of historical QTN issues or draft PR #95.
- Classification remains heuristic; unknown terms require manual adjudication.
- External article bytes, version contents and per-QTN relations are NOT attested.
- Full v0.3.1 TSC package is **not** being added to production by this PR.
- The local TSC mutable-policy P0 repair is separately tested but not a GitHub
  Bridge trust-root guarantee. Do not activate runtime authorization based on it.

## Tests and promotion criteria

Run:

    python -m pytest -q tests/test_qtn_watch.py tests/test_qtn_semantic_review.py
    python -m pytest -q

The GitHub PR CI is authoritative for repository-level pass/fail, not a local
fixture test or an arXiv abstract. Any network smoke failure should be
distinguished from deterministic regression failure.

Before merge: inspect tests/checks and get human review for scientific
classification; this draft does not claim EXTERNAL_PASS or WORLD_REAL_PASS.
