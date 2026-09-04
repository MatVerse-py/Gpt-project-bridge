# MARXIV Canon v1 — Governed Lifecycle Runtime for Scientific Objects

## Status

`CANONICAL_SPEC / IMPLEMENTATION_PASS_IN_DECLARED_SCOPE / LIVE_PUBLICATION_PILOT_HOLD`

This document freezes the current defensible definition of MARXIV without promoting hypotheses beyond available evidence.

## Canonical definition

MARXIV is a **governed lifecycle runtime for versioned scientific objects**.

It coordinates the longitudinal state of a scientific object across evidence, claims, reproduction, publication projection, human authorization, external publication effects and reconciliation.

It is not a scientific truth oracle, not an arXiv replacement, and not evidence that a computational organism or digital life has been demonstrated.

A compact model is:

```text
MARXIV = ScientificObject
       + EpistemicState
       + ReproductionState
       + PublicationState
       + GovernedPublisher
       + ExternalReconciliation
```

## Core invariants

```text
Publication != ScientificTruth
DESCRIPTION != SPEC != CODE != EXECUTION != RESULT != REPRODUCTION
ManuscriptCandidate != FrozenManuscript
FrozenManuscript != ApprovedPublicationPackage
Prepared != Approved
Approved != Submitted
Submitted != Moderated/Announced
```

Publication changes publication state. It does not automatically promote the epistemic state of any claim.

## Scientific Object

A MARXIV Scientific Object is the canonical longitudinal object. Venue-specific submissions are projections of it.

The canonical object may contain:

- identity and version;
- title, abstract, authors, ORCID and affiliations;
- claims and claim-state references;
- evidence references;
- lineage and parent-object references;
- reproduction state;
- publication intents and external identities;
- negative-result and retraction relationships where applicable.

A venue is never the canonical source of this object.

## Governed publication transition

The Runtime Publisher implements a controlled external transition:

```text
Paper Preflight
      -> Frozen Manuscript
      -> Scientific Object
      -> Publication Projection
      -> Sandbox
      -> HUMAN_REVIEW_REQUIRED
      -> Package-bound Human Approval
      -> Delegated Execution
      -> External Side Effect
      -> External Identity
      -> Reconciliation
      -> Scientific Object'
```

The publication sandbox is therefore not merely a temporary directory. It represents a **candidate publication state**.

## Authority separation

The authority invariant is:

```text
Organizer != Authorizer != Publication Authority
```

The same software agent may organize and later execute, but it has no publication authority before valid human authorization exists.

For a package `P`:

```text
Capability(agent, P) = PREPARE                         before approval
Capability(agent, P) = SUBMIT(P)                       after valid approval
Capability(agent, P') = BLOCK                          if P' != approved P
```

Human authorization is a capability grant, not a generic statement such as "publish this paper".

A manuscript freeze authorizes neither approval nor submission. The freeze can be scoped to preparation only, as demonstrated by Paper 1.

## Portable package identity

The runtime must not allow host-local filesystem paths to alter the canonical identity of an otherwise identical publication package.

The current preparation boundary therefore stages the frozen manuscript into the sandbox and uses stable relative transport references. For the same Scientific Object and pinned transport, distinct sandbox roots must preserve:

```text
object_hash
manifest_hash
manuscript_sha256
arxiv_subfile_sha256
review_packet_hash
package_hash
```

Paper 1 passed this two-root discriminant in Publication Bridge CI run `33828742642` on head `01adc23c39c2653aa0fafe54e429242500d0fd9e`.

This is portability evidence within the declared runtime/CI scope, not independent external reproduction.

## Package-bound approval

Approval is bound to the exact frozen publication package:

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

Any material mutation invalidates the approval, including changes to:

- manuscript bytes;
- authors;
- title or abstract;
- primary category;
- cross-lists;
- license;
- destination metadata.

A changed package returns to `HUMAN_REVIEW_REQUIRED`.

## Publication state machine

Canonical runtime publication states:

```text
HUMAN_REVIEW_REQUIRED
      -> APPROVED
      -> SUBMITTING
      -> SUBMITTED_TO_ARXIV
      -> RECONCILED
```

Failure before the final external effect:

```text
HOLD_PRE_SUBMIT
```

Uncertainty after a potentially successful final external effect:

```text
HOLD_RECONCILIATION_REQUIRED
```

`HOLD_RECONCILIATION_REQUIRED` MUST NOT automatically retry because a retry could duplicate an external submission.

`SUBMITTED_TO_ARXIV` means that the governed submission action was performed and the transport observed its expected confirmation boundary. It does not mean moderation, announcement, endorsement, acceptance of scientific claims or external verification.

## Reconciliation

An external identifier such as an arXiv ID is an observed external identity associated with the publication projection.

Reconciliation writes that identity back into MARXIV state while preserving provenance and the original package hash.

This closes the transition:

```text
Internal Scientific Object
      -> governed external effect
      -> externally assigned identity
      -> reconciled Scientific Object state
```

## Living Paper semantics

A Living Paper is a versioned Scientific Object whose evidence, claims, reproduction and publication states may evolve without erasing previous states.

A later version can be expressed as:

```text
ScientificObject[t+1] = Update(
    ScientificObject[t],
    delta_evidence,
    delta_claim_state,
    delta_reproduction,
    delta_publication
)
```

Every materially changed publication projection requires a new package-bound human authorization.

## MARXIV discriminant

A useful current discriminant is:

```text
D_M = L AND G AND R
```

where:

- `L` = longitudinal scientific state;
- `G` = governed publication transition;
- `R` = external-state reconciliation.

This is a design discriminant, not yet a novelty claim. Prior-art review is required before claiming originality for the composition.

## Relationship to MatVerse / OCG

The Runtime Publisher can be interpreted architecturally as a governed external effector: it converts internal scientific state into an authorized external action and reconciles the consequence.

This does **not** prove OCG, digital life, consciousness, autopoiesis or a new biological class.

The defensible statement is narrower:

> MARXIV implements governed lifecycle and publication-state transitions for versioned computational scientific objects.

Any organism-level interpretation remains a higher-order hypothesis unless independently tested against explicit discriminators and controls.

## Evidence classes

### PASS / implemented and exercised in the declared scope

- paper preflight assessment;
- explicit manuscript freeze boundary;
- deterministic preflight -> Scientific Object promotion;
- MARXIV Scientific Object -> venue projection;
- deterministic publication sandbox/review packet;
- frozen-manuscript byte hashing and staging;
- package hashing;
- two-root portable package identity with pinned PaperPush transport;
- `HUMAN_REVIEW_REQUIRED` real-object preparation without credentials;
- human approval challenge/approval machinery in automated tests;
- mutation invalidation in automated tests;
- authority gate before external effects in automated tests;
- publication state machine in automated tests;
- EvidenceOS publication receipts;
- no-auto-retry on uncertain post-submit state;
- external identifier reconciliation logic in automated tests.

Paper 1 evidence:

```text
manuscript        papers/matverse-2.0/main.tex (v0.1)
scientific object examples/marxiv/matverse-2.0/scientific-object.v1.json
dry-run evidence  examples/marxiv/matverse-2.0/dry-run-result.v1.json
state             HUMAN_REVIEW_REQUIRED
package hash      4ef1c650ccf52054cb77adc5d1a1e8d5a19785bcdbe23a644470ee707e97b2aa
```

### HOLD

- live author-authorized end-to-end arXiv publication through this runtime;
- external arXiv identifier for Paper 1;
- independent external reproduction of the MARXIV publisher workflow;
- multi-venue production validation;
- scientific novelty claim for the full MARXIV composition;
- any claim that MARXIV or Runtime Publisher proves OCG/digital life.

### Not admissible as factual results without experiments

The following must not be stated as measured results unless an evidence artifact is attached:

- preparation time `<5 s`;
- submission time `<30 s`;
- metadata error rate `<1%`;
- 80–90% workflow time reduction;
- Monte Carlo publication reliability figures;
- cost claims presented as measured operational results.

These may be experiment targets, not evidence.

## EvidenceOS event boundary

The publisher emits or is designed around distinct events such as:

```text
MARXIV_PUBLICATION_SANDBOX_PREPARED
MARXIV_HUMAN_PUBLICATION_APPROVAL
MARXIV_PUBLICATION_SUBMITTING
MARXIV_ARXIV_SUBMISSION_ACTION
MARXIV_PUBLICATION_HOLD
MARXIV_PUBLICATION_RECONCILED
```

No event named `PUBLISHED` should be emitted solely because a browser automation step completed. External publication/announcement state must be independently observed and reconciled.

## Current validation sequence

Completed:

1. real Paper 1 manuscript candidate;
2. explicit human freeze for dry-run-only scope;
3. preflight promotion;
4. real pinned-PaperPush preparation;
5. two-root portable package-identity proof;
6. `HUMAN_REVIEW_REQUIRED` without credentials, approval or external side effect;
7. committed machine-readable dry-run evidence.

Still requiring separate authority/evidence:

1. package review;
2. optional approval challenge;
3. explicit exact-package publication approval;
4. live author-authorized arXiv pilot;
5. external-ID reconciliation;
6. archived post-publication EvidencePack;
7. independent reproduction / additional venue adapters.

## Canonical short form

```text
MARXIV = Governed Lifecycle Runtime for Scientific Objects

RuntimePublisher = PublicationProjection
                 + Sandbox
                 + HumanAuthorization
                 + DelegatedExecution
                 + Reconciliation

Publication != ScientificTruth
Prepared != Approved
```
