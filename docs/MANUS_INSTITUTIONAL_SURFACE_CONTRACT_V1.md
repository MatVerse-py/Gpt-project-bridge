# MatVerse Institutional Surface Contract v1

## Purpose

This document defines the authority boundary between the canonical MatVerse runtime and institutional user interfaces such as a Manus-built console.

The institutional surface is a projection client. It is not a constitutional runtime and it is never a source of truth for Bridge, HDB, Omega, EvidenceOS, Ledger, inherited constraints, or maturity promotion.

## Canonical authority

Canonical state is produced by `MatVerse-py/Gpt-project-bridge` and is bound to repository commit SHA, frozen contract hash, gate fingerprint, constitutional contract hash, and evidence receipts.

A surface MUST NOT infer current authority or maturity from locally persisted UI records alone.

## Mandatory boundary

```text
CANONICAL RUNTIME
Bridge / Federation / Contract Registry
HDB / Omega / Governed Organism
EvidenceOS / Ledger / Replay
        |
        | source-bound projection
        v
INSTITUTIONAL SURFACE
registry views / claim views / experiments
maturity dashboard / publication workflows
        |
        | intent only
        v
CANONICAL RUNTIME
```

The surface has no direct canonical write authority.

## Permitted operations

The surface MAY read, list, filter, search, render and export projections. It MAY create a non-canonical intent requesting an action from the runtime.

## Forbidden operations

The surface MUST NOT mutate Omega policy or decisions, append directly to the canonical Ledger, fabricate EvidenceOS receipts, authorize an inherited constraint, promote a maturity gate, rewrite the Constitution or a frozen contract, or represent a locally cached row as current canonical state when the source is unavailable.

## Projection persistence

A database used by the surface is a read model/cache only. Drizzle, PostgreSQL, SQLite, browser storage, or equivalent persistence MAY store a projection for rendering and search. Such records MUST carry source metadata and MUST NOT become the authority for the represented object.

Persisted projections MUST preserve at least:

```text
source_repository
source_commit
frozen_contract_hash
gate_fingerprint
constitutional_contract_hash
source_receipt
projection_hash
generated_at
freshness
```

If the canonical source cannot be reached, the surface MUST expose `SOURCE_UNAVAILABLE` or the corresponding HOLD state. It MUST NOT silently promote a cached record to `LIVE`.

## Maturity

The surface displays maturity decisions; it does not make them.

```text
CONCEPT
SPEC_PASS
BUILD_PASS
IMPLEMENTATION_PASS
REPRODUCTION_PASS
EXTERNAL_PASS
WORLD_REAL_PASS
SCIENTIFIC_PASS
INDEPENDENT_REPLICATION_PASS
```

A displayed maturity state MUST identify the target object or claim, gate, `PASS | HOLD | BLOCK | ESCALATE`, validator identity, and one or more evidence pointers.

`SCIENTIFIC_PASS` is claim-scoped. It MUST NOT be presented as blanket certification of MatVerse.

## Relation integrity

Entity existence and relation validity are separate properties.

```text
Exist(A) AND Exist(B) DOES NOT IMPLY Valid(Relation(A,B))
```

Relations such as `MEMBER_OF`, `AUTHORED_BY`, `DERIVED_FROM`, `VALIDATES`, `SUPERSEDES`, `DEPENDS_ON`, `REPRODUCES`, and `IMPLEMENTS` MUST carry their own witness/evidence pointer before the surface renders them as accepted relations.

## Authority separation

The institutional surface MUST preserve:

```text
Proposer != FinalValidator
Generator != Authorizer
Execution != Evidence
EntityIntegrity != RelationIntegrity
MaturityTransition requires ValidatorEvidence
```

A UI role or database permission MUST NOT be treated as constitutional authority unless the canonical runtime has granted the corresponding capability.

## Intent boundary

When a user requests a mutation, the surface MAY create an intent object. An intent is not a decision and is not an execution receipt.

An intent MUST contain a unique intent id, requested operation, actor identity available to the surface, target object, submitted parameters, creation time, and the projection/source binding used when the intent was created.

The runtime remains responsible for authentication, authorization, HDB/Omega evaluation, execution, EvidenceOS receipt generation, Ledger commitment, and replay.

## Failure behavior

The surface MUST fail closed for institutional claims.

```text
canonical source unavailable -> HOLD / SOURCE_UNAVAILABLE
receipt cannot be verified    -> HOLD or BLOCK according to validator result
relation witness absent       -> HOLD
contract binding mismatch     -> BLOCK
unknown maturity evidence     -> HOLD
```

A visual success state, optimistic cache, local database row, or human-readable note is never sufficient to generate canonical `PASS`.

## Manus implementation rule

A Manus implementation consuming this contract SHOULD keep its existing presentation and registry work where useful, but it MUST remove or demote any local implementation that acts as an independent source of truth for Bridge, Omega, EvidenceOS, canonical Ledger, inherited constraints, constitutional authorization, or maturity promotion.

Those features become adapters, projections, evidence viewers, or intent-submission surfaces over the canonical runtime.

The machine-readable projection schema is `contracts/institutional-surface-v1.schema.json`.
