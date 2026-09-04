# MARXIV Canon v1 — Governed Lifecycle Runtime for Scientific Objects

## Status

`CANONICAL_SPEC / IMPLEMENTATION_PARTIAL_PASS / LIVE_PUBLICATION_PILOT_HOLD`

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

## Core invariant

```text
Publication != ScientificTruth
```

Publication changes publication state. It does not automatically promote the epistemic state of any claim.

Likewise:

```text
DESCRIPTION != SPEC != CODE != EXECUTION != RESULT != REPRODUCTION
```

MARXIV must preserve these boundaries in every projection and receipt.

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
Scientific Object
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

### PASS / implemented in the current publication branch

- MARXIV Scientific Object -> venue projection;
- deterministic publication sandbox;
- review packet;
- package hashing;
- human approval challenge;
- package-bound authorization;
- mutation invalidation;
- authority gate before external effects;
- publication state machine;
- EvidenceOS publication receipts;
- no-auto-retry on uncertain post-submit state;
- external identifier reconciliation.

### HOLD

- live author-authorized end-to-end arXiv pilot through this runtime;
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

The current publisher emits or is designed around distinct events such as:

```text
MARXIV_PUBLICATION_SANDBOX_PREPARED
MARXIV_HUMAN_PUBLICATION_APPROVAL
MARXIV_PUBLICATION_SUBMITTING
MARXIV_ARXIV_SUBMISSION_ACTION
MARXIV_PUBLICATION_HOLD
MARXIV_PUBLICATION_RECONCILED
```

No event named `PUBLISHED` should be emitted solely because a browser automation step completed. External publication/announcement state must be independently observed and reconciled.

## Next validation sequence

1. CI must pass on the exact PR head.
2. Run a complete dry-run with a real Scientific Object and no final external side effect.
3. Execute one author-authorized live arXiv pilot.
4. Reconcile the real external arXiv identifier into MARXIV state.
5. Archive the full evidence pack: object snapshot, package hashes, approval receipt, transport receipt, external identity and reconciliation receipt.
6. Add Zenodo/DOI as an independent transport rather than conflating venue authority.
7. Submit the MARXIV paper using MARXIV itself as a self-hosting demonstration, while keeping scientific claims separately adjudicated.

## Canonical short form

```text
MARXIV = Governed Lifecycle Runtime for Scientific Objects

RuntimePublisher = PublicationProjection
                 + Sandbox
                 + HumanAuthorization
                 + DelegatedExecution
                 + Reconciliation

Publication != ScientificTruth
```
