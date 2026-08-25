# MatVerse Institutional Surface Contract v1

## Purpose

This document defines the authority boundary between the canonical MatVerse runtime and institutional user interfaces such as a Manus-built console.

The institutional surface is a projection client. It is not a constitutional runtime and it is never a source of truth for Bridge, HDB, Omega, EvidenceOS, Ledger, inherited constraints, or maturity promotion.

## Canonical authority

Canonical state is produced by `MatVerse-py/Gpt-project-bridge` and is bound to repository commit identity, frozen contract hash, gate fingerprint, constitutional contract hash, and evidence receipts.

A surface MUST NOT infer current authority or maturity from locally persisted UI records alone.

Git object identifiers and content digests are distinct types. Repository commits use a Git object id accepted as 40 or 64 lowercase hexadecimal characters; artifact, receipt, gate, constitutional and projection digests use exactly 64 lowercase hexadecimal SHA-256 characters.

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
        | CREATE_INTENT only
        v
CANONICAL RUNTIME
```

The surface has no direct canonical write authority.

## Permitted operations

The surface MAY read, list, filter, search, render and export projections. It MAY create a non-canonical intent requesting an action from the runtime.

The complete permitted operation set is fixed by the machine-readable projection contract. `CREATE_INTENT` is defined separately by `contracts/institutional-intent-v1.schema.json`.

## Forbidden operations

The surface MUST NOT mutate Omega policy or decisions, append directly to the canonical Ledger, fabricate EvidenceOS receipts, authorize an inherited constraint, promote a maturity gate, rewrite the Constitution or a frozen contract, or represent a locally cached row as current canonical state when the source is unavailable.

The complete forbidden-operation set is mandatory, not advisory. A schema-conformant projection cannot publish an empty or reduced forbidden-operation list.

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

## Projection integrity

`projection_hash` is SHA-256 over the RFC 8785 JSON Canonicalization Scheme serialization of the complete projection document after removing the single field `projection.projection_hash`. The field is excluded to avoid self-reference. Producers and consumers MUST use the same rule.

The projection metadata therefore declares:

```text
hash_algorithm = SHA-256
canonicalization = RFC8785_JCS
hash_excludes = [projection.projection_hash]
```

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

A displayed maturity state MUST identify the target kind and target id, gate, `PASS | HOLD | BLOCK | ESCALATE`, final validator identity, authority trace, and evidence array.

A maturity `PASS` MUST contain at least one valid evidence pointer. `HOLD`, `BLOCK`, and `ESCALATE` MAY contain an empty evidence array when the absence of evidence is itself the reason the state cannot be promoted; the `reason` field should explain that condition.

`SCIENTIFIC_PASS` is claim-scoped. The machine-readable schema requires `target_kind = CLAIM` whenever the gate is `SCIENTIFIC_PASS`. It MUST NOT be presented as blanket certification of MatVerse.

## Claim, experiment and artifact PASS

A claim, experiment, artifact, identifier or authority trace MUST NOT be projected as `PASS` without the evidence required by its corresponding schema rule. Visual success, a local boolean, an outbound identifier URL, or a human-readable note is insufficient.

## Relation integrity

Entity existence and relation validity are separate properties.

```text
Exist(A) AND Exist(B) DOES NOT IMPLY Valid(Relation(A,B))
```

Relations such as `MEMBER_OF`, `AUTHORED_BY`, `DERIVED_FROM`, `VALIDATES`, `SUPERSEDES`, `DEPENDS_ON`, `REPRODUCES`, and `IMPLEMENTS` MUST carry their own witness/evidence pointer before the surface renders them as `PASS` relations.

A relation with no witness is representable as `HOLD`; it MUST NOT be upgraded to `PASS` until a witness exists.

## Authority separation

The institutional surface MUST preserve:

```text
Proposer != FinalValidator
Generator != Authorizer
Execution != Evidence
EntityIntegrity != RelationIntegrity
MaturityTransition requires ValidatorEvidence
```

The projection schema carries explicit authority traces with proposer, executor, generator, authorizer, evidence producer, final validator and promoter identities where applicable. Because standard JSON Schema cannot compare arbitrary field values for inequality, the deterministic companion validator `app.institutional_contract.validate_projection_semantics` enforces the cross-field separation rules fail-closed.

A UI role or database permission MUST NOT be treated as constitutional authority unless the canonical runtime has granted the corresponding capability.

## Intent boundary

When a user requests a mutation, the surface MAY create an intent object. An intent is not a decision, is not a canonical state mutation, and is not an execution receipt.

The machine-readable contract is `contracts/institutional-intent-v1.schema.json`.

An intent contains a unique intent id, requested operation, actor identity available to the surface, target object, submitted parameters, creation time, and the source binding used when the intent was created. The source binding includes the projection hash observed by the client.

`intent_hash` is SHA-256 over RFC 8785 JCS serialization of the intent after removing `intent_hash` itself.

The runtime remains responsible for authentication, authorization, HDB/Omega evaluation, execution, EvidenceOS receipt generation, Ledger commitment, replay, and production of a refreshed projection.

The intended flow is:

```text
UI interaction
-> CREATE_INTENT
-> authenticated canonical runtime
-> authorization / HDB / Omega
-> execution or rejection
-> EvidenceOS receipt
-> Ledger commitment when applicable
-> refreshed institutional projection
```

## Failure behavior

The surface MUST fail closed for institutional claims.

```text
canonical source unavailable -> HOLD / SOURCE_UNAVAILABLE
receipt cannot be verified -> HOLD or BLOCK according to validator result
relation witness absent -> HOLD
contract binding mismatch -> BLOCK
unknown maturity evidence -> HOLD
```

A visual success state, optimistic cache, local database row, or human-readable note is never sufficient to generate canonical `PASS`.

## Manus implementation rule

A Manus implementation consuming this contract SHOULD keep its existing presentation and registry work where useful, but it MUST remove or demote any local implementation that acts as an independent source of truth for Bridge, Omega, EvidenceOS, canonical Ledger, inherited constraints, constitutional authorization, or maturity promotion.

Those features become adapters, projections, evidence viewers, or intent-submission surfaces over the canonical runtime.

The machine-readable projection schema is `contracts/institutional-surface-v1.schema.json`. The machine-readable intent schema is `contracts/institutional-intent-v1.schema.json`. Cross-field authority invariants are enforced by `app/institutional_contract.py` in addition to structural schema validation.
