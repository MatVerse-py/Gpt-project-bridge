# MARXIV Runtime Publisher v1

## Status

`IMPLEMENTED / LIVE_ARXIV_FINALIZER_NOT_YET_PILOTED`

This runtime couples MARXIV Scientific Objects to the governed MatVerse Publication Bridge. It is deliberately split into two planes:

- **MARXIV plane** — scientific object, metadata organization, claims/evidence/lineage, sandbox, approval, state and reconciliation.
- **Publication transport plane** — venue-specific validation, authentication and submission mechanics.

The venue never becomes the canonical source of the scientific object. arXiv receives a frozen publication projection of a MARXIV object.

## Authority model

```text
Scientific Object
      |
      v
MARXIV Publisher Agent
      |
      | organize / normalize / validate
      v
Publication Sandbox
      |
      | immutable package hashes
      v
HUMAN_REVIEW_REQUIRED
      |
      | explicit package-bound approval
      v
APPROVED
      |
      | delegated publication authority
      v
Publisher Agent
      |
      v
arXiv submission
      |
      v
SUBMITTED_TO_ARXIV
      |
      | arXiv identifier observed later
      v
RECONCILED
```

The agent has **no publication authority before approval**.

Approval is not bound to a paper title or filename. It is bound to the exact package:

```text
object_hash
+ manifest_hash
+ manuscript_sha256
+ arxiv_subfile_sha256
+ review_packet_hash
+ destination
= package_hash
```

Any changed byte, author list, abstract, category, cross-list, license or venue metadata invalidates the approval and returns the workflow to human review.

## Scientific Object input

Example `scientific-object.json`:

```json
{
  "schema": "marxiv.scientific-object.v1",
  "object_id": "matverse-2.0",
  "version": "v1",
  "title": "MATVERSE 2.0: A Constitutional Architecture for Governed Informational Transformation in Federated Research Systems",
  "authors": [
    {
      "name": "Author Name",
      "orcid": null,
      "affiliation": null
    }
  ],
  "abstract": "Full publication abstract goes here.",
  "manuscript_file": "./paper.zip",
  "keywords": ["AI governance", "reproducibility"],
  "claims": ["claim://matverse/C1"],
  "evidence_refs": ["evidence://matverse/E1"],
  "parent_object_id": null,
  "publication": {
    "venue": "arxiv",
    "primary_archive": "cs",
    "primary_category": "cs.AI",
    "crosslist_archives": ["cs"],
    "crosslist_categories": ["cs.SE"],
    "license": "CC BY 4.0",
    "keep_all_files": false,
    "comments": "Preprint",
    "report_number": null,
    "journal_reference": null,
    "acm_class": null,
    "msc_class": null,
    "doi": null
  }
}
```

MARXIV-only metadata such as ORCID, affiliation, claims, evidence and lineage remains in the Scientific Object/review packet even when a target venue does not accept those fields.

## 1. Prepare in sandbox

```bash
python -m app.marxiv_runtime_publisher prepare \
  --object /absolute/path/scientific-object.json \
  --sandbox-root .marxiv
```

The sandbox is created as:

```text
.marxiv/<object_id>/<version>/
├── scientific-object.snapshot.json
├── arxiv-manifest.json
├── review-packet.json
├── publisher-state.json
└── transport/<publication_id>/
    ├── arxiv.sub
    ├── values.json
    ├── manifest.snapshot.json
    └── publication-state.json
```

At this point the state is:

```text
HUMAN_REVIEW_REQUIRED
```

The agent may organize metadata and prepare the projection, but cannot publish.

## 2. Request human approval

```bash
python -m app.marxiv_runtime_publisher request-approval \
  --sandbox .marxiv/matverse-2.0/v1
```

This emits an expiring challenge with a random nonce and the exact confirmation phrase, for example:

```text
PUBLISH matverse-2.0-v1-arxiv 4f9a8b31d21c
```

The human reviews `review-packet.json`, the rendered/source manuscript, authors, abstract, categories, cross-lists and selected license before approving.

## 3. Human approval

Create a local approval signing secret. It is never committed:

```bash
export MARXIV_APPROVAL_SECRET="$(python -c 'import secrets; print(secrets.token_hex(32))')"
```

Approve exactly the prepared package:

```bash
python -m app.marxiv_runtime_publisher approve \
  --sandbox .marxiv/matverse-2.0/v1 \
  --approver human-authority \
  --confirm 'PUBLISH matverse-2.0-v1-arxiv 4f9a8b31d21c'
```

The resulting `human-approval.json` is HMAC-bound to the package and the expiring challenge. It is a local runtime authorization artifact and is gitignored.

Verify before publication:

```bash
python -m app.marxiv_runtime_publisher verify-approval \
  --sandbox .marxiv/matverse-2.0/v1
```

## 4. Author-authorized arXiv credentials

Credentials are runtime-only inputs:

```bash
export ARXIV_EMAIL='author-account@example.org'
export ARXIV_PASSWORD='use-the-real-password-only-in-your-local-environment'
```

Do not put credentials in Scientific Objects, manifests, review packets, receipts, Git or chat messages.

## 5. Agent publication after human OK

```bash
python -m app.marxiv_runtime_publisher publish \
  --sandbox .marxiv/matverse-2.0/v1
```

The runtime re-verifies the approval and every package hash **before authentication and before external effects**. If verification passes, delegated authority is active for that exact package and destination.

The arXiv finalizer then executes the venue workflow using the current pinned PaperPush/Playwright transport and performs the final `Submit Article` action.

### State semantics

```text
HUMAN_REVIEW_REQUIRED
  -> APPROVED
  -> SUBMITTING
  -> SUBMITTED_TO_ARXIV
  -> RECONCILED
```

Failure before the final arXiv click:

```text
HOLD_PRE_SUBMIT
```

Uncertainty or transport failure after the final click:

```text
HOLD_RECONCILIATION_REQUIRED
```

The runtime **never automatically retries** from `HOLD_RECONCILIATION_REQUIRED`, because doing so could create a duplicate submission.

`SUBMITTED_TO_ARXIV` means the submission action was performed and the portal confirmation heuristic was observed. It does **not** mean moderation/announcement/acceptance by arXiv.

## 6. Reconcile external arXiv identity

Once the real identifier is known:

```bash
python -m app.marxiv_runtime_publisher reconcile \
  --sandbox .marxiv/matverse-2.0/v1 \
  --arxiv-id 2609.12345
```

The identifier is written back into the MARXIV publisher state with a new EvidenceOS receipt:

```text
MARXIV Scientific Object
  -> publication projection
  -> human approval
  -> venue submission
  -> external identifier
  -> MARXIV lineage/state
```

## Receipts

The runtime emits deterministic EvidenceOS receipts for:

- `MARXIV_PUBLICATION_SANDBOX_PREPARED`
- `MARXIV_HUMAN_PUBLICATION_APPROVAL`
- `MARXIV_PUBLICATION_SUBMITTING`
- `MARXIV_ARXIV_SUBMISSION_ACTION`
- `MARXIV_PUBLICATION_HOLD`
- `MARXIV_PUBLICATION_RECONCILED`

No receipt claims that arXiv moderation accepted the paper unless that state is independently observed and reconciled.

## Security invariants

1. `Generator != Authorizer`: the metadata-organizing agent cannot create its own valid human approval without the local approval secret.
2. Approval is package-bound and expires.
3. Credentials do not enter persistent MARXIV state.
4. Any package mutation invalidates approval.
5. External side effects are blocked until approval verifies.
6. Unknown state after final external effect is `HOLD_RECONCILIATION_REQUIRED`, not retry.
7. arXiv ID is reconciled only after a submission effect exists.
8. `.marxiv/` and approval artifacts are excluded from Git.

## Current evidence boundary

Implemented and CI-testable now:

- MARXIV object -> arXiv projection;
- deterministic sandbox and review packet;
- package hashing;
- human challenge/approval binding;
- mutation invalidation;
- authority gate before publish;
- state machine and EvidenceOS receipts;
- no-auto-retry after an uncertain final effect;
- external identifier reconciliation.

The finalizer follows the current arXiv workflow encoded by the pinned PaperPush arXiv adapter. A real author-authorized final submission through this MARXIV runtime has **not yet been executed as a live pilot**. Until that pilot succeeds, live portal completion remains an operational validation target, not a claimed result.
