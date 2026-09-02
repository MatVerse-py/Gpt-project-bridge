# Source Evidence TeX Policy v1

## Purpose

A preserved LaTeX source (`.tex`) is a structured textual representation and can be strong evidence of the exact source content of a version.

The policy separates four questions:

1. **What source content existed?**
2. **What complete source closure constituted that version?**
3. **Was that source externally published/custodied?**
4. **Are the scientific claims correct?**

A `.tex` source may strongly answer (1) and (2). It does not answer (3) or (4) by itself.

## Core rule

`LATEX_SOURCE != EXTERNAL_PUBLICATION`

`OFFICIAL_TEX_VERSION != SCIENTIFIC_VALIDITY`

`CLAIMED_IDENTIFIER != RESOLVED_IDENTIFIER`

## 1. TeX as source representation

### Ordinary preserved TeX

`LATEX_SOURCE` is independent structured evidence with base compatibility priority 80 (`P3`) **when its source closure is complete**.

A `.tex` file does **not** become official merely because of its extension, filename, title, folder, prose, embedded DOI, embedded ORCID or version macro.

### Source closure

The evidence object is not only the entry `.tex`. The local artifact closure includes, transitively, resolvable references such as:

- `\input` / `\include`;
- `\includegraphics`;
- `\bibliography` / `\addbibresource`;
- local `.sty` files;
- local `.cls` files.

Bare toolchain packages/classes are not treated as missing local artifacts unless they are explicitly path-like or resolve locally.

The closure is hashed over sorted relative paths and file-content SHA-256 digests. Changing any local dependency changes the closure digest.

If a mandatory local reference cannot be resolved:

`closure_complete = false`

The TeX representation becomes `PARTIAL`, its compatibility tier is capped at `P1`, and it has zero claim-authority until the closure is recovered. It cannot establish an official version.

## 2. Claimed identifiers are not resolved identifiers

A DOI, ORCID, commit-like string, contract address or other identifier written inside TeX is source text. It is recorded as a `CLAIMED_IDENTIFIER`.

It is **not** promoted into `SourceEvidence.identifiers` by the TeX representation itself.

Resolution requires an independent representation, for example:

- DOI/DataCite/Crossref/Zenodo metadata;
- ORCID metadata;
- Git commit lookup;
- chain explorer/API;
- another authoritative external registry.

This is the same fail-closed principle applied to generated images: a string rendered or authored inside an artifact does not self-verify.

## 3. Official anchored TeX

A complete TeX source is promoted to strong official-version evidence only when:

- `official_version = true`;
- `closure_complete = true`; and
- at least one immutable provenance anchor is independently verified.

Accepted anchor classes:

- Git commit SHA + `commit_verified=true`;
- source commit + `commit_verified=true`;
- release tag + `tag_verified=true`;
- manifest SHA-256 + `manifest_verified=true`;
- canonical locator + `canonical_verified=true`;
- verified signature (`signature_verified=true`).

When all conditions hold, the representation receives effective compatibility priority 95 (`P5`) **for official source/version identity**.

If `official_version=true` is present without a verified anchor, the Bridge records:

`OFFICIAL_VERSION_UNANCHORED`

If the closure is incomplete, it records:

`LATEX_CLOSURE_INCOMPLETE`

Neither case gains official-version authority.

## 4. Authority is a vector, not a scalar

`P0–P5` remains only a compatibility/fallback ranking. It is not universal proof strength.

The Bridge also exposes claim-scoped policy authority across:

`content | version | authorship | publication | timestamp | execution`

Examples:

| Representation | content | version | authorship | publication | timestamp | execution |
|---|---:|---:|---:|---:|---:|---:|
| `LATEX_SOURCE` (complete) | 1.00 | 1.00 | 0.30 | 0.00 | 0.00 | 0.00 |
| `ARXIV_EPRINT_SOURCE` with verified third-party custody | 1.00 | 1.00 | 0.60 | 0.70 | 0.90 | 0.00 |
| `SAVED_PDF` | 0.80 | 0.60 | 0.30 | 0.00 | 0.00 | 0.00 |
| `DOI_METADATA` | 0.20 | 0.70 | 0.60 | 1.00 | 0.90 | 0.00 |
| `GIT_COMMIT` | 0.90 | 1.00 | 0.60 | 0.00 | 0.85 | 0.00 |
| `GENERATED_IMAGE` | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |

These numbers are **policy weights, not probabilities, empirical confidence or scientific truth scores**.

Aggregation currently uses the maximum authority from independent admissible roots per domain. It deliberately does not use probabilistic noisy-OR because derivational independence is not statistical independence.

## 5. arXiv source is a distinct evidence class

`LATEX_SOURCE` and `ARXIV_EPRINT_SOURCE` are not synonyms.

A local/repository TeX source can establish content/version but has zero publication authority by itself.

An arXiv-custodied source can gain partial publication/timestamp authority only when third-party custody and canonical/timestamp metadata are independently verified. Merely using an `arxiv://` locator or filename does not create authority.

## 6. Source → compiled artifact relation

The statement “this TeX source produced that PDF” is a separate claim and must not be inferred from filenames.

The intended future derivation protocol is:

`complete closure -> pinned build environment -> compile -> normalize PDF -> compare invariants`

Byte-for-byte equality is not required because PDF compilation may inject non-semantic entropy such as creation/modification dates, IDs, producer metadata, font subsets and object ordering.

Derivation results should distinguish at least:

- `MATCH`;
- `BENIGN_DIVERGENCE` — semantic/text/page invariants agree but binary internals differ;
- `SUBSTANTIVE_DIVERGENCE` — semantic/text/page invariants differ;
- `BUILD_FAILED`.

A failed derivation does not erase the TeX source. It means the source and deposited artifact have not been proven to be the same version and should produce `SOURCE_ARTIFACT_DIVERGENCE` at the appropriate severity.

## 7. Current implementation boundary

Implemented now:

- `LATEX_SOURCE`;
- `ARXIV_EPRINT_SOURCE` classification;
- claimed-vs-resolved identifier separation;
- complete/incomplete closure state;
- transitive local closure scanner;
- closure SHA-256 digest;
- official-version P5 only with complete closure + verified anchor;
- claim-scoped authority vector;
- fail-closed conflicts for unanchored/incomplete official TeX;
- receipts expose authority, claimed identifiers and closure status.

Not yet promoted to operational PASS:

- reproducible LaTeX→PDF derivation CI over real deposited paper pairs.

Reason: the Bridge repository currently has no declared canonical paper `.tex` → deposited `.pdf` pairs. Those mappings must be supplied in `evidence/latex_roots.json` before expensive compilation verification is meaningful.

## 8. Recommended chain

For papers/specifications:

`entry.tex + transitive closure`
`-> closure SHA-256`
`-> verified Git commit/tag/manifest/signature`
`-> compiled PDF relation verification`
`-> external publication metadata / DOI / arXiv custody`

Each step supports different predicates. They must remain distinct in EvidenceOS/Bridge rather than being collapsed into one generic notion of “proof”.
