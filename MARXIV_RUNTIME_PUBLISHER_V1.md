# MARXIV Runtime Publisher v1

## Status

`IMPLEMENTED / LIVE_ARXIV_FINALIZER_NOT_YET_PILOTED`

## Canonical scientific boundary

This runtime is governed by [`MARXIV_CANON_V1.md`](./MARXIV_CANON_V1.md).

The canonical invariants are:

```text
MARXIV = Governed Lifecycle Runtime for Scientific Objects
Publication != ScientificTruth
Organizer != Authorizer != Publication Authority
```

The Runtime Publisher is an implemented governed external-effector subsystem. It is **not** evidence by itself that OCG, digital life, autopoiesis, consciousness or a new biological class has been demonstrated. Claims about measured publication speed, error-rate reduction, Monte Carlo reliability or operational cost remain inadmissible as factual results unless an experiment artifact is attached.

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

The approval phrase is an explicit delegation to submit the reviewed package to the named destination under the publication metadata and license visible in `review-packet.json`. Before approving, the human must also review the venue's current submission terms/attestations that the runtime will encounter. A generic old approval is not authority to accept materially changed terms.

## Scientific Object input

Example `scientific-object.json`:

```json
{
  "schema": "marxiv.scientific-object.v1",
  "object_id": "matverse-2.0",
  "version": "v1",
  "title": "MATVERSE 2.0: A Constitutional Architecture for Governed Informational Transformation in Federated Research Systems",
  "authors": [{"name": "Author Name", "orcid": null, "affiliation": null}],
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

## Prepare in sandbox

```bash
python -m app.marxiv_runtime_publisher prepare --object /absolute/path/scientific-object.json --sandbox-root .marxiv
```

The sandbox freezes the Scientific Object, venue manifest, review packet, transport files and package hashes. State becomes `HUMAN_REVIEW_REQUIRED`.

## Request and grant human approval

```bash
python -m app.marxiv_runtime_publisher request-approval --sandbox .marxiv/matverse-2.0/v1
export MARXIV_APPROVAL_SECRET="$(python -c 'import secrets; print(secrets.token_hex(32))')"
python -m app.marxiv_runtime_publisher approve --sandbox .marxiv/matverse-2.0/v1 --approver human-authority --confirm 'PUBLISH matverse-2.0-v1-arxiv 4f9a8b31d21c'
python -m app.marxiv_runtime_publisher verify-approval --sandbox .marxiv/matverse-2.0/v1
```

The approval is HMAC-bound to the exact package and an expiring random challenge.

## Author-authorized credentials

```bash
export ARXIV_EMAIL='author-account@example.org'
export ARXIV_PASSWORD='use-the-real-password-only-in-your-local-environment'
```

Credentials never enter Scientific Objects, manifests, receipts, Git or chat messages.

## Agent publication after human OK

```bash
python -m app.marxiv_runtime_publisher publish --sandbox .marxiv/matverse-2.0/v1
```

The runtime re-verifies approval and all hashes before external effects. If valid, delegated authority becomes active for that exact package/destination, and the finalizer performs the current arXiv submission flow including `Submit Article`.

State flow:

```text
HUMAN_REVIEW_REQUIRED -> APPROVED -> SUBMITTING -> SUBMITTED_TO_ARXIV -> RECONCILED
```

Pre-submit failure -> `HOLD_PRE_SUBMIT`.

Uncertain state after final external click -> `HOLD_RECONCILIATION_REQUIRED` with **no automatic retry**.

`SUBMITTED_TO_ARXIV` is not a claim that arXiv moderation/announcement has accepted the paper and never promotes the scientific truth-state of its claims automatically.

## Reconcile external identity

```bash
python -m app.marxiv_runtime_publisher reconcile --sandbox .marxiv/matverse-2.0/v1 --arxiv-id 2609.12345
```

The external identifier is written back into publisher state with an EvidenceOS receipt.

## Receipts

- `MARXIV_PUBLICATION_SANDBOX_PREPARED`
- `MARXIV_HUMAN_PUBLICATION_APPROVAL`
- `MARXIV_PUBLICATION_SUBMITTING`
- `MARXIV_ARXIV_SUBMISSION_ACTION`
- `MARXIV_PUBLICATION_HOLD`
- `MARXIV_PUBLICATION_RECONCILED`

## Security invariants

1. `Generator != Authorizer`.
2. Approval is package-bound and expires.
3. Credentials do not enter persistent MARXIV state.
4. Package mutation invalidates approval.
5. External effects are blocked until approval verifies.
6. Unknown post-click state is HOLD, never retry.
7. External identity is reconciled only after an external effect exists.
8. `.marxiv/` and approval artifacts are excluded from Git.
9. Publication state never silently promotes epistemic claim state.

## Current evidence boundary

Implemented and CI-testable: MARXIV object projection, deterministic sandbox/review packet, package hashing, human challenge/approval, mutation invalidation, authority gate, publication state machine, EvidenceOS receipts, no-auto-retry and identifier reconciliation.

The finalizer follows the current arXiv workflow encoded by the pinned PaperPush adapter. A real author-authorized final submission through this MARXIV runtime has **not yet been executed as a live pilot**. Until then, live portal completion remains an operational validation target, not a claimed result.
