# MARXIV Paper 1 — Governed Dry-Run Evidence

## Status

`MANUSCRIPT_FROZEN / SCIENTIFIC_OBJECT_PROMOTED / PORTABLE_DRY_RUN_PASS / HUMAN_REVIEW_REQUIRED`

This record applies the MARXIV fail-closed publication boundary to the first public MATVERSE paper and records the completed no-side-effect dry-run.

## Paper 1

**Title**

`MATVERSE 2.0: A Constitutional Architecture for Governed Informational Transformation in Federated Research Systems`

**Scientific Object**

`matverse-2.0 / v1`

**Frozen manuscript**

`papers/matverse-2.0/main.tex` — manuscript label `v0.1`

**Author**

`Mateus Alves Arêas` — ORCID `0009-0008-2973-4047`

Affiliation is intentionally `null`.

## Human-confirmed freeze

The human authority confirmed the exact dry-run configuration:

```text
affiliation = null
primary     = cs.SE
cross-list  = cs.AI
license     = CC BY 4.0
abstract    = current manuscript abstract
```

The authority scope is strictly:

```text
FREEZE_AND_DRY_RUN_ONLY
```

It does not authorize arXiv login, approval challenge creation, package approval, final submission, or any external publication side effect.

The durable freeze record is:

`papers/matverse-2.0/FREEZE_v0.1.json`

## Promotion

The governed preflight:

`examples/marxiv/matverse-2.0/paper1-preflight.json`

now evaluates to:

```text
READY_FOR_PROMOTION
```

with no blockers.

The deterministic promotion result is committed as:

`examples/marxiv/matverse-2.0/scientific-object.v1.json`

CI independently regenerates the Scientific Object from the preflight and requires exact JSON equality with the committed object.

## Portable real dry-run

Publication Bridge CI run `33828742642` (`run_number=83`) on head:

`01adc23c39c2653aa0fafe54e429242500d0fd9e`

executed the frozen Paper 1 twice using the pinned PaperPush transport commit:

`3cc701d91bf78c046f008477baad40e7fa53ff4f`

The two preparations used distinct sandbox roots:

```text
.marxiv/run-a
.marxiv/run-b
```

Both produced the same six canonical package hashes and both stopped exactly at:

```text
HUMAN_REVIEW_REQUIRED
```

No arXiv credentials were present. No approval challenge, human approval, submission-result artifact, browser submission, or other external side effect was created.

## Canonical dry-run hashes

```text
object_hash
836e48d2d5cbea02c315445fbc6674614771b6edb0812a26667fe71fe0216eae

manifest_hash
1956987219b7f24b26306def9b7da16402af972bea84fca2880ff15830a1f84a

manuscript_sha256
87726ef5e5e1c487837eaf285684f1ba5569f7ae5c929480892fe8ca804cc81e

arxiv_subfile_sha256
f67ad986c6e687c0b2045198e8d94fdf76d7a39dc4677ff20b6bf767374e61ab

review_packet_hash
c95b77fbd18b9e468e741d747b86cbe9c43a6fcf79094ce5f9c98c0239be2ffe

package_hash
4ef1c650ccf52054cb77adc5d1a1e8d5a19785bcdbe23a644470ee707e97b2aa
```

The machine-readable evidence summary is committed at:

`examples/marxiv/matverse-2.0/dry-run-result.v1.json`

## Portability correction discovered by the test

The stronger two-root test exposed path dependence in the original transport package. PaperPush resolves filemap fields against the `-d` manuscript directory and serializes the resulting path into the `.sub` file. Passing an absolute manuscript directory therefore caused `arxiv_subfile_sha256` to differ between otherwise identical sandboxes.

The bridge was corrected so that:

1. the Scientific Object preserves a portable manuscript reference;
2. the Runtime Publisher stages the manuscript under `sandbox/manuscript/`;
3. the arXiv manifest uses a stable relative manuscript reference;
4. Publication Bridge supplies PaperPush a manuscript `-d` relative to its transport workdir;
5. only the actual execution boundary rehydrates a runtime-local absolute path when needed.

The real two-root CI then produced identical `object_hash`, `manifest_hash`, `manuscript_sha256`, `arxiv_subfile_sha256`, `review_packet_hash`, and `package_hash`.

## Dry-run acceptance criteria

| Criterion | State |
|---|---|
| Human manuscript freeze | PASS |
| Verified author identity | PASS |
| ORCID | PASS |
| Explicit category decision | PASS |
| Explicit cross-list decision | PASS |
| Explicit license decision | PASS |
| Final abstract confirmation | PASS |
| Preflight `READY_FOR_PROMOTION` | PASS |
| Deterministic Scientific Object promotion | PASS |
| Real PaperPush transport preparation | PASS |
| Two-root portable package identity | PASS |
| Sandbox state `HUMAN_REVIEW_REQUIRED` | PASS |
| Credentials absent | PASS |
| Approval absent | PASS |
| External side effect absent | PASS |
| Live arXiv publication | HOLD |
| External arXiv identifier | HOLD |
| Independent external reproduction | HOLD |
| Scientific novelty of complete MARXIV composition | HOLD / prior-art review required |

## State transition achieved

```text
PREFLIGHT_RECORDED
      -> MANUSCRIPT_FROZEN
      -> READY_FOR_PROMOTION
      -> SCIENTIFIC_OBJECT_V1
      -> PREPARE x 2
      -> PORTABLE_PACKAGE_IDENTITY_PASS
      -> HUMAN_REVIEW_REQUIRED
```

The transition stops here by authority design.

No `request-approval`, `approve`, `login`, `publish`, or `reconcile` action is authorized by the freeze decision.

## Scientific boundary

```text
Publication != ScientificTruth
PASS_LOCAL != ExternalReproduction
ManuscriptCandidate != FrozenManuscript
FrozenManuscript != ApprovedPublicationPackage
Prepared != Approved
Approved != Submitted
Submitted != Moderated/Announced
```

This dry-run demonstrates the governed preparation and portable package-identity path within the declared CI/runtime scope. It does not demonstrate live arXiv publication, external scientific validation, independent reproduction, or any organism-level claim.
