# QTN Watch v1

## Objective

QTN Watch is the Bridge external-intelligence monitor for the historical MatVerse inventory `QTN-001` through `QTN-028`.

It does **not** treat external news, papers, standards, or demonstrations as validation of MatVerse claims. Every emitted finding is classified as:

`EXTERNAL_RELEVANCE_NOT_MATVERSE_VALIDATION`

The purpose is to detect when the external state of the art changes enough that a QTN object should be rebased, benchmarked, retested, or have its engineering readiness reviewed.

## Sources

The default zero-cost source set is:

- arXiv API — current research candidates;
- NIST quantum-information news — public research/infrastructure signals;
- IETF Datatracker — RFCs and active Internet-Drafts relevant to quantum/PQC networking;
- Quantum Internet Alliance — quantum-network prototype/ecosystem developments.

Each source is isolated. The run is fail-closed if the configured minimum source quorum is not met. Default quorum: 2 of 4 sources.

## Pipeline

`source -> normalize -> QTN mapping -> materiality score -> classification -> dedup -> GitHub issue`

The mapping is explicit and deterministic in `app/qtn_watch.py`.

### Materiality

The score combines:

- source authority;
- number of affected QTN objects;
- high-impact terms such as standard, RFC, deployment, field trial, prototype, interoperability, fault tolerance and logical qubits;
- domain specificity.

Default threshold: `0.62`.

The score is a triage score, not scientific confidence and not proof of novelty.

## Action classes

- `REBASE_AGAINST_EXTERNAL_STANDARD`
- `ADD_EXTERNAL_BASELINE_AND_RETEST`
- `REVIEW_ENGINEERING_READINESS`
- `UPDATE_CRYPTOGRAPHIC_BASELINE`
- `REVIEW_FOR_DELTA_AND_BENCHMARK`

## Deduplication

Each source item receives a deterministic SHA-256 digest over canonicalized source, title and URL. GitHub issue search checks the marker:

`<!-- qtn-watch:<digest> -->`

A previously reported item is not emitted again.

## Operation

Manual:

```bash
python scripts/run_qtn_watch.py --json
```

Publish unreported material findings as a GitHub issue:

```bash
GITHUB_TOKEN=... GITHUB_REPOSITORY=MatVerse-py/Gpt-project-bridge \
python scripts/run_qtn_watch.py --github-issues --json
```

GitHub Actions runs the watch daily and also supports `workflow_dispatch`.

## Configuration

- `QTN_WATCH_THRESHOLD` — default `0.62`;
- `QTN_WATCH_MIN_SOURCES` — default `2`;
- `QTN_WATCH_TIMEOUT` — request timeout in seconds, default `20`;
- `QTN_WATCH_ARXIV_MAX` — maximum arXiv entries, default `40`.

## Failure policy

- source failure: isolated and recorded;
- source quorum below minimum: `BLOCK`, exit code 2, no alert publication;
- no material finding: `PASS`, no issue created;
- material but already reported: `PASS`, no duplicate issue;
- GitHub publication failure: non-zero execution failure.

## Future extension boundary

QTN Watch v1 intentionally separates discovery from scientific adjudication. URANO/Tesseu may consume a QTN Watch finding as an experiment trigger, but promotion of any QTN state requires independent tests and gates outside this monitor.
