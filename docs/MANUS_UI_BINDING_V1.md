# Manus UI Binding v1

This binding maps the current institutional UI surfaces to the canonical MatVerse projection contract.

## Evidence class

The current Manus screenshots are evidence of presentation structure and local product behavior only. They are not evidence that a displayed badge, count, identity, maturity state, verification state, blockchain state, research relation, or scientific claim has been validated by the canonical runtime.

## Overview dashboard

Dashboard totals, activity cards, research counters, registry counts, publication counts and similar summary values are derived views.

They MUST be recomputed from the current institutional projection or explicitly labeled as cached. A dashboard aggregate MUST NOT become a source object for a canonical claim.

```text
projection subjects/artifacts/claims/experiments/relations/receipts
                |
                v
          dashboard aggregates
```

When canonical source is unavailable, the dashboard MUST expose stale/source-unavailable state rather than presenting cached totals as current.

## Researcher registry and profile

A researcher profile is represented as a `subject` projection.

Visible identity links such as ORCID, GitHub, Hugging Face, Zenodo, DOI-associated authorship, institutional email/domain, or equivalent identifiers MUST be represented as `verifiedIdentifier` objects.

The presence of an identifier string or outbound URL is not equivalent to verification. A verified identity indicator requires:

```text
scheme
value
decision
validator_id
witness.receipt_hash
witness.source_commit
```

Affiliation, authorship, membership and validation are relations and require their own witness. A verified researcher identity MUST NOT implicitly verify an affiliation, paper, project, skill, institution, or scientific claim.

## Status badges

Badges such as verified, validated, reproduced, on-chain, approved, scientific, trusted or equivalent UI labels MUST be projections of explicit canonical decisions.

A badge MUST be rendered from one of:

- an identity identifier decision;
- a relation decision;
- a claim decision;
- an artifact status;
- a maturity gate decision;
- a receipt/integrity result.

A badge MUST NOT be generated from a local boolean whose authority is only the UI database.

## Blockchain / anchor displays

An on-chain or blockchain indicator is evidence about an anchor or receipt, not proof that the underlying scientific or engineering claim is true.

The UI MUST distinguish:

```text
anchor exists
receipt verifies
claim supported
maturity gate passed
```

These are separate states.

## Research, publications and artifacts

Research objects and publications shown by the UI are projected artifacts and claims. Publication or upload is not equivalent to scientific validation.

A publication-oriented action MAY create an intent, but the institutional surface MUST NOT promote maturity or generate a canonical receipt locally.

## Activity feed

Activity items SHOULD be projected from canonical receipts and runtime events. UI-only events MAY be displayed only if clearly identified as local interface activity and never mixed with canonical runtime events without a provenance marker.

## Actions and buttons

Any current UI action that semantically means verify, validate, publish, anchor, approve, promote, sign, authorize, execute, reproduce or similar MUST be split into:

```text
UI interaction -> CREATE_INTENT -> canonical runtime -> gate -> execution -> receipt -> refreshed projection
```

The button success state is therefore not the canonical result. The canonical result is the returned decision/receipt and the subsequent source-bound projection.

## Persistence rule

Drizzle/PostgreSQL/SQLite/browser state used by Manus is a read model/cache. It MAY store profile fields, filters, layouts, pending intents and canonical projection snapshots.

It MUST NOT act as the authoritative store for:

- Omega decisions;
- canonical Ledger entries;
- EvidenceOS receipts;
- constitutional bindings;
- inherited constraints;
- maturity promotion;
- verified relations;
- verified identity assertions.

## Current-screen architectural verdict

The visual architecture is compatible with the MatVerse institutional layer if its data flow is inverted from local-authority CRUD to canonical projection consumption.

The UI does not need to be rebuilt. The authority model underneath it does.
