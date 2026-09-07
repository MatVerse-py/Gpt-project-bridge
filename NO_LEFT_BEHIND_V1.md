# MatVerse No-Left-Behind v1

Protocol: `matverse.no-left-behind.v1`

## Purpose

No-Left-Behind is a bounded coverage protocol, not a claim of omniscience. It
tracks what was enumerated inside a declared accessible scope, what changed, what
is missing, what remains outside the registered source set, and what still lacks
adjudication.

```text
registered partitions + registered sources
        ↓
complete sweep
        ↓
CoverageRegistry
        ↓
Δobjects + Δsources + missing + R_ext
        ↓
classification + relation + membership + lineage + adjudication
        ↓
repeat over the same scope hash
        ↓
two consecutive clean complete sweeps
        ↓
DISCOVERY_SATURATED
```

A clean sweep requires all four conditions:

```text
Δobjects = 0
missing  = 0
Δsources = 0
R_ext    = ∅
```

Therefore saturation means the known accessible universe is stable **and closed
under currently observed source references**. It does not assert that inaccessible
or unregistered environments contain nothing else.

`DISCOVERY_SATURATED` is deliberately weaker than `COVERAGE_COMPLETE`.

## Orthogonal state axes

Coverage does not use one overloaded enum. Lifecycle, verdict and presence are
separate:

```text
Lifecycle = DISCOVERED | INDEXED | CLASSIFIED | RELATED | ADJUDICATED
Verdict   = PENDING | CANONICAL | SUPERSEDED | UNRESOLVED | ARCHIVED
Presence  = PRESENT | MISSING
```

Membership, lineage, semantic state, implementation state and evidence state are
also independent fields.

`orphan` is **derived**, not stored. An item is orphaned when it has no
non-refuted relation to another `CANONICAL` item in the governed relation graph.

Illegal closure examples:

```text
CANONICAL ∧ orphan
UNRESOLVED ∧ orphan ∧ no hashed justification
SUPERSEDED ∧ no successor
PENDING residual
MISSING without governed resolution
```

This prevents the prior ambiguity where `ORPHAN` and `CANONICAL` competed inside
the same lifecycle enum.

## Stable identity and revisions

A coverage object is identified by:

```text
partition_id + source_uri + artifact_type
```

Content hashes are version observations, not identity. When content changes, the
same object returns to `DISCOVERED/PENDING`, semantic state becomes `UNRESOLVED`,
and prior evidence is not silently inherited.

## Relation Integrity

A relation requires its own evidence reference:

```text
Exist(A) ∧ Exist(B)  ≠  Evidence(Relation(A,B))
```

`record_relation` rejects missing `evidence_ref`.

## Relationship to corpus fractions

Coverage precedes fractioning:

```text
Coverage sweep
→ membership adjudication
→ canonical ordering
→ matverse.corpus-fractions.v1
→ private deterministic structural plan
→ matverse.corpus-commitments.v1
→ public salted commitment roots
```

The deterministic `merge_root` of `matverse.corpus-fractions.v1` is an internal
structural identifier. It must not be treated as a hiding commitment for private
or low-entropy content.

## Hiding public commitments

Protocol: `matverse.corpus-commitments.v1`

Public fraction integrity uses salted, domain-separated SHA-256 commitments:

```text
leaf = H(0x00 || salt_128 || canonical(member))
node = H(0x01 || left || right)
root = H(0x02 || commitment_protocol || source_protocol || n || k || count || fraction_roots...)
```

The 128-bit salts and membership openings remain private staging material. The
public root is binding to the staged state while resisting dictionary confirmation
of low-entropy source objects. Revealing an opening is a separate disclosure act
and requires authorization.

## Governed Zenodo staging

Protocol: `matverse.fraction-publication.v1`

Disclosure modes:

- `METADATA_ONLY` (default)
- `REDACTED_BUNDLE`
- `PUBLIC_BUNDLE`

The public envelope exposes salted commitment roots, not deterministic source
manifest hashes, source IDs, salts, raw chats or the private structural merge root.

```text
private FractionPlan
   ↓
salted CommitmentWork
   ↓
privacy-safe publication envelope
   ↓
MARXIV.Prepared
   ↓
Ω-GATE::CanPublish
   ↓
Body-D independent adjudication
   ↓
MARXIV.Approved
   ↓
Body-X external executor + provider receipt
   ↓
MARXIV.Submitted
```

`ADMIT` never performs a network write. Only the external execution layer may
produce `Submitted`, and it must use a principal distinct from proposer and
adjudicator.

## Q-Gate terminology

`Q-Gate` is not a second constitutional organ. When retained as a historical or UI
label it resolves to:

```text
Q-Gate ≡ Ω-GATE::CanPublish
```

The canonical Ω sequence remains:

```text
CanExist → CanExecute → CanPersist → CanPublish
```

## Boundary

The following implications are invalid:

```text
structural hash exists       ⇒ source content is hidden       [false]
commitment root exists       ⇒ disclosure is authorized       [false]
LLM recommends publication   ⇒ publication is authorized      [false]
MARXIV.Prepared              ⇒ MARXIV.Approved                [false]
MARXIV.Approved              ⇒ external state changed         [false]
coverage saturated           ⇒ all possible information known [false]
```

The protocols prove bounded structural and governance facts only.
