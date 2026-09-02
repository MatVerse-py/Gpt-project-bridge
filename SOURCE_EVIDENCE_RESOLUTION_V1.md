# Source Evidence Resolution v1

## Purpose

The Bridge must not equate a blocked live page with missing evidence.

`LIVE_FAILURE != SOURCE_FAILURE`

Before returning `UNAVAILABLE_AFTER_FALLBACK`, the Bridge may inspect authorized alternative representations and preserve the relationship between them.

## Resolution order

1. `LIVE_HTML`
2. `API_METADATA`
3. `SAVED_HTML`
4. `LATEX_SOURCE`
5. `SAVED_PDF`
6. `SAVED_IMAGE`
7. `SCREENSHOT`
8. `DOCUMENT_PAGE_RENDER`
9. `DOI_METADATA`
10. `ORCID_SNAPSHOT`
11. `REPOSITORY_FILE`
12. `GIT_COMMIT`
13. `HF_SNAPSHOT`
14. `CORPUS_COPY`
15. `GENERATED_IMAGE`
16. `MODEL_REPORT`

The order is explicit and versionable. Adapters are I/O boundaries; adapter failure does not abort the full resolution path.

The fallback order is not identical to evidentiary authority. For example, a verified Git commit or DOI metadata has higher identity authority than an earlier low-authority representation reached in the fallback sequence.

## Evidence states

- `VERIFIED`
- `VERIFIED_SNAPSHOT`
- `PARTIAL`
- `CONFLICT`
- `HOLD_AUTHORITY`
- `HOLD_SEMANTICS`
- `UNAVAILABLE_AFTER_FALLBACK`
- `BLOCK_TAMPERED`

A conflict between high-priority structured identifiers fails closed. A stale human-readable description can be recorded as `STALE_PROSE` without overriding structured platform metadata.

## Structured metadata precedence

For a saved Zenodo HTML, fields such as `citation_doi`, `citation_author`, `citation_title`, `citation_pdf_url` and the canonical link are treated as structured platform metadata. If a prose description still says that a DOI is pending while `citation_doi` exists, the Bridge preserves both observations and records `STALE_PROSE`.

It does not silently rewrite history.

## LaTeX and official-version evidence

A preserved `.tex` source is represented as `LATEX_SOURCE` and is independent structured evidence with base priority 80 (`P3`).

The extension alone does not establish official status.

A TeX source becomes strong official-version evidence only when:

`official_version=true AND verified_immutable_anchor=true`

A verified immutable anchor may be a verified Git commit, source commit, release tag, manifest SHA-256, canonical locator, or verified signature.

When anchored, the TeX source receives effective priority 95 (`P5`) **for source/version identity**. This authority is scoped: it can establish which exact source text/version was official, but it does not by itself establish external publication, DOI resolution, peer review, reproduction, or scientific validity.

An `official_version=true` TeX without a verified anchor records `OFFICIAL_VERSION_UNANCHORED` and remains ordinary P3 TeX evidence.

See `SOURCE_EVIDENCE_TEX_POLICY_V1.md`.

## SourceEvidence

A resolved source preserves:

- original and resolved locator;
- all representations consulted;
- content hashes;
- capture time when known;
- identifiers such as DOI, ORCID, repository, commit SHA, canonical URL and version;
- whether a verified official-version TeX root exists;
- conflicts;
- ordinal evidence tier;
- deterministic evidence hash.

The resolution is then wrapped by the existing `matverse.evidence-receipt.v1` receipt.

## Semantic provenance

Source resolution is separate from semantic authority.

For terminology, the Bridge records speaker and speech act. An explicit user correction has greater project-semantic authority than a model proposal. Antiquity is used as a prior/tiebreaker among observations of equal authority; it does not override a later explicit user correction.

`older_model_inference < newer_user_correction`

`older_equal_authority_definition > newer_equal_authority_definition`

This is not a scientific-truth rule. Scientific claims still require claim-scoped evidence and reproduction.

## Constitutional invariant

A name, acronym, page or `.tex` file is not canonical merely because it is frequent, well formatted, currently reachable, or carries the word "official".

A source may only be promoted when provenance, identity and conflicts are resolved to the level required by the consuming gate.
