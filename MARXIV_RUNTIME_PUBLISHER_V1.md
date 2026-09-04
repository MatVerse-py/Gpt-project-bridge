# MARXIV Runtime Publisher v1

## Canonical boundary

The governing scientific definition is frozen in `MARXIV_CANON_V1.md`.

The runtime implementation described here MUST preserve the canon's boundaries:

```text
Publication != ScientificTruth
Organizer != Authorizer != Publication Authority
ManuscriptCandidate != FrozenManuscript
FrozenManuscript != ApprovedPublicationPackage
```

A publication-state transition never silently promotes a claim's epistemic state.

## Purpose

This module connects MARXIV Scientific Objects to the MatVerse Publication Bridge as a governed external-effect runtime.

The authority sequence is:

```text
Scientific Object
  -> deterministic venue projection
  -> local sandbox
  -> HUMAN_REVIEW_REQUIRED
  -> package-bound human approval
  -> delegated submission authority
  -> arXiv transport
  -> external-state reconciliation
```

Before valid human approval, the runtime has **PREPARE** capability only. After approval it may submit only the exact approved package. Any package mutation invalidates authority.

## Components

- `app/marxiv_preflight.py` — fail-closed paper preflight and promotion gate.
- `app/marxiv_runtime_publisher.py` — Scientific Object sandbox, package integrity, approval and lifecycle state machine.
- `app/publication_bridge.py` — arXiv-specific projection and pinned PaperPush transport preparation.
- `examples/marxiv/matverse-2.0/` — real Paper 1 preflight, Scientific Object and governed dry-run evidence.

## Preflight boundary

The runtime distinguishes:

```text
manuscript absent
!= manuscript candidate exists
!= manuscript frozen for publication
```

`marxiv_preflight assess` returns `READY_FOR_PROMOTION` only after verified authorship, real manuscript existence, explicit manuscript freeze, category/cross-list decisions, publication license and final abstract confirmation exist.

Promotion produces `marxiv.scientific-object.v1`; promotion itself performs no external side effect.

## Portable package identity

The Scientific Object carries a portable logical manuscript reference rather than a runtime-local absolute path.

At preparation time the Runtime Publisher:

1. resolves the source manuscript from the Scientific Object location;
2. stages the exact bytes into `sandbox/manuscript/<filename>`;
3. verifies source/staged SHA-256 equality;
4. writes a stable relative `manuscript/<filename>` reference into the arXiv manifest;
5. passes PaperPush a manuscript data directory relative to the transport workdir;
6. hashes the resulting transport artifact as part of the package identity.

This is required because PaperPush's filemap resolution can serialize the manuscript-directory path into `arxiv.sub`. Absolute paths would make a semantically identical package hash differently on different hosts or sandbox roots.

The Paper 1 governed dry-run now verifies portability empirically by preparing the same frozen Scientific Object in two distinct sandbox roots and requiring equality of:

```text
object_hash
manifest_hash
manuscript_sha256
arxiv_subfile_sha256
review_packet_hash
package_hash
```

All six matched in Publication Bridge CI run `33828742642` on head `01adc23c39c2653aa0fafe54e429242500d0fd9e`.

## Package-bound approval

The frozen package identity is:

```text
package_hash = H(
    object_hash,
    manifest_hash,
    manuscript_sha256,
    arxiv_subfile_sha256,
    review_packet_hash,
    destination
)
```

Approval is invalid if any bound component changes.

The approval sequence is intentionally separate:

```text
HUMAN_REVIEW_REQUIRED
  -> request-approval
  -> explicit human confirmation phrase
  -> APPROVED
```

No approval challenge should be created merely because `prepare` succeeded.

## Runtime states

```text
HUMAN_REVIEW_REQUIRED
      -> APPROVED
      -> SUBMITTING
      -> SUBMITTED_TO_ARXIV
      -> RECONCILED
```

Pre-final-effect failure:

```text
HOLD_PRE_SUBMIT
```

Uncertain post-final-effect outcome:

```text
HOLD_RECONCILIATION_REQUIRED
```

The latter MUST NOT automatically retry because the first external effect may already have succeeded.

## Credentials

The runtime may read:

- `ARXIV_EMAIL`
- `ARXIV_PASSWORD`
- `MARXIV_APPROVAL_SECRET`

from the local execution environment only when the corresponding authorized stage is actually invoked.

Credentials are not part of Scientific Objects, publication manifests, receipts, committed evidence artifacts or package hashes.

`prepare` requires none of these credentials.

## Real Paper 1 dry-run

Frozen configuration:

```text
manuscript   papers/matverse-2.0/main.tex (v0.1)
author       Mateus Alves Arêas
ORCID        0009-0008-2973-4047
affiliation  null
primary      cs.SE
cross-list   cs.AI
license      CC BY 4.0
abstract     current
```

The authorized scope was `FREEZE_AND_DRY_RUN_ONLY`.

The real pinned-transport dry-run produced:

```text
status = HUMAN_REVIEW_REQUIRED
portable_package_identity = true
credentials_present = false
approval_created = false
external_side_effect = false
```

Machine-readable evidence is stored at:

`examples/marxiv/matverse-2.0/dry-run-result.v1.json`

The current canonical dry-run package hash is:

`4ef1c650ccf52054cb77adc5d1a1e8d5a19785bcdbe23a644470ee707e97b2aa`

## What this proves and does not prove

Within the declared CI/runtime scope, the implementation demonstrates:

- fail-closed preflight;
- deterministic Scientific Object promotion;
- frozen manuscript staging and byte-integrity verification;
- stable two-root package identity using the pinned publication transport;
- preparation through PaperPush validation;
- stop at `HUMAN_REVIEW_REQUIRED` without credentials or external side effect.

It does **not** demonstrate:

- live arXiv submission;
- arXiv moderation or announcement;
- external scientific verification;
- independent reproduction by another party/provider;
- novelty of the full MARXIV composition;
- OCG, digital life, consciousness or autopoiesis.

## Current evidence state

```text
PRELIGHT / PROMOTION                    PASS
FROZEN PAPER 1                         PASS
PINNED PAPERPUSH PREPARATION           PASS
TWO-ROOT PORTABLE PACKAGE IDENTITY     PASS
HUMAN_REVIEW_REQUIRED                  PASS
LIVE ARXIV PILOT                       HOLD
EXTERNAL IDENTIFIER RECONCILIATION     HOLD
INDEPENDENT EXTERNAL REPRODUCTION      HOLD
FULL MARXIV NOVELTY CLAIM              HOLD
```

The next authority transition is not implied by this result. `request-approval`, `approve`, `login`, `publish`, and `reconcile` require their own explicit authority and evidence boundaries.
