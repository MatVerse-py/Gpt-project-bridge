# MatVerse No-Left-Behind v1

Protocol: `matverse.no-left-behind.v1`

## Purpose

No-Left-Behind is a coverage protocol, not a claim of omniscience. It proves what
was enumerated inside a declared, accessible partition scope and preserves every
known object as integrated, superseded, unresolved or orphaned rather than
silently dropping it.

```text
registered partitions
        ↓
complete sweep
        ↓
CoverageRegistry
        ↓
new / changed / missing / orphan detection
        ↓
classification + membership + lineage + relation adjudication
        ↓
repeat complete sweep
        ↓
two consecutive zero-new / zero-missing sweeps
        ↓
DISCOVERY_SATURATED
```

`DISCOVERY_SATURATED` is deliberately weaker than `COVERAGE_COMPLETE`.

Coverage closure additionally requires:

1. no ORPHAN item in the scope;
2. no membership item left `UNKNOWN` or `HOLD`;
3. the same scope hash for the saturation sweeps.

The protocol does not assert that inaccessible or unregistered partitions contain
nothing else.

## Stable identity and revisions

A coverage item is identified by:

```text
partition_id + source_uri + artifact_type
```

Content hashes are version observations, not object identity. Updating a file does
not create a fake second file; it creates a new recorded content version of the
same coverage item.

## Relation Integrity

A relation may not become registered merely because two endpoints exist or appear
semantically similar. `CoverageRegistry.record_relation` requires an independent
`evidence_ref`.

```text
Exist(A) ∧ Exist(B)  ≠  Evidence(Relation(A,B))
```

## Canonical states

- `DISCOVERED`
- `INDEXED`
- `CLASSIFIED`
- `RELATED`
- `ADJUDICATED`
- `SUPERSEDED`
- `CANONICAL`
- `UNRESOLVED`
- `ORPHAN`

Membership is tracked separately (`UNKNOWN`, `HOLD`, `MEMBER`, `NON_MEMBER`,
`UNASSIGNED`) so semantic similarity never silently becomes project membership.

## Relationship to corpus fractions

No-Left-Behind precedes canonical fractioning:

```text
Coverage sweep
→ membership adjudication
→ canonical ordering
→ matverse.corpus-fractions.v1
→ fraction manifests
→ merge_root
```

The fraction protocol is structural and semantic-free. A `merge_root` commits to
an ordered set of fraction manifest hashes; it is not a truth claim and is not a
publication authorization.

## Governed Zenodo staging

Protocol: `matverse.fraction-publication.v1`

Three disclosure modes exist:

- `METADATA_ONLY` (default)
- `REDACTED_BUNDLE`
- `PUBLIC_BUNDLE`

`METADATA_ONLY` emits only a privacy-safe structural envelope: corpus id, fraction
number/bounds/counts, fraction manifest hash and merge root. Document IDs,
conversation IDs and raw content are not placed in the public envelope.

Bundle modes require an explicit SHA-256 of the externally prepared bundle. The
fraction publication bridge never performs a network write. It produces a
`zenodo.create_draft` plan with `requires_authorization=true`. Execution must pass
through the existing governed publication boundary and Secret Plane/provider
controls.

```text
FractionPlan
   ↓
publication envelope
   ↓
disclosure governance
   ↓
Zenodo draft PLAN
   ↓
independent external authorization
   ↓
provider executor
```

## Boundary

The following implications are invalid:

```text
merge_root exists      ⇒ corpus may be disclosed        [false]
LLM recommends publish ⇒ publication is authorized      [false]
Zenodo draft exists    ⇒ claim is scientifically valid  [false]
coverage saturated     ⇒ universe is exhaustively known [false]
```

The protocols prove bounded structural facts only.
