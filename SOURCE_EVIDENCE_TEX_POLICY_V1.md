# Source Evidence TeX Policy v1

## Purpose

A preserved LaTeX source (`.tex`) is a structured textual representation and can be strong evidence of the exact source content of a version.

The policy separates two questions:

1. **What source text/version existed?**
2. **Was it externally published, peer reviewed, reproduced, or scientifically validated?**

A `.tex` file can answer the first question strongly when its provenance is anchored. It does not answer the second question by itself.

## Evidence classes

### Ordinary preserved TeX

`LATEX_SOURCE` is independent structured evidence with base priority 80 (`P3`).

A `.tex` file does **not** become official merely because of its extension, filename, title, folder, or prose inside the document.

### Official anchored TeX

A TeX source is promoted to strong official-version evidence only when:

- `official_version = true`; and
- at least one immutable provenance anchor is independently verified.

Accepted anchor classes:

- Git commit SHA + `commit_verified=true`;
- source commit + `commit_verified=true`;
- release tag + `tag_verified=true`;
- manifest SHA-256 + `manifest_verified=true`;
- canonical locator + `canonical_verified=true`;
- verified signature (`signature_verified=true`).

When both conditions hold, the representation receives effective priority 95 (`P5`) **for source/version identity**.

## Scoped authority

`P5` here is claim-scoped.

An official anchored `.tex` can strongly support:

- exact source text of the official version;
- title/author/version declarations present in that source;
- provenance of that source version;
- relation between a version label and an immutable commit/tag/manifest/signature when verified.

It does not by itself prove:

- DOI registration or resolution;
- external publication;
- peer review;
- independent reproduction;
- correctness of results;
- scientific validity;
- authorship beyond what the provenance chain independently establishes.

Formally:

`OFFICIAL_TEX_VERSION != EXTERNAL_PUBLICATION`

`OFFICIAL_TEX_VERSION != SCIENTIFIC_VALIDITY`

## Fail-closed rule

If `official_version=true` is present without a verified anchor, the Bridge records:

`OFFICIAL_VERSION_UNANCHORED`

The file remains ordinary `LATEX_SOURCE` evidence (`P3`) and does not receive official-version authority.

A disagreement between anchored official TeX and another high-priority structured representation is not silently resolved. It produces a blocking structured identifier/version conflict and the source resolution becomes `CONFLICT`.

## Version identity

`version` is a resolvable structured field alongside DOI, ORCID, repository, commit, canonical URL, title and author.

The content itself is SHA-256 hashed. The hash identifies exact bytes/text representation; the verified provenance anchor establishes why that exact source should be treated as the official version.

## Recommended chain

For papers and specifications:

`official .tex -> SHA-256 -> verified Git commit/tag/manifest -> compiled PDF -> publication metadata/DOI`

The PDF is a compiled representation of the source; DOI/publication metadata is external publication evidence. These representations corroborate different claims and should not be collapsed into one generic notion of proof.

## Priority summary

- API metadata: 100 (`P5`)
- Git commit: 95 (`P5`)
- official anchored TeX: effective 95 (`P5`, scoped to version identity)
- live HTML: 90 (`P4`)
- saved HTML / ORCID snapshot: 85 (`P4`)
- ordinary TeX / repository file: 80 (`P3`)
- Hugging Face snapshot: 75 (`P3`)
- saved PDF: 70 (`P2`)
- non-generated saved image: 60 (`P2`)
- screenshot: 55 (`P1`/`P2` boundary by current tier table)
- corpus copy: 50 (`P1`)
- document page render: 45 (`P1`, derivative)
- model report: 10 (`P0`)
- generated image: 0 (`P0`)
