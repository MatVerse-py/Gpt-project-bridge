# Source Evidence Resolution v1

## Purpose

The Bridge must not equate a blocked live page with missing evidence.

`LIVE_FAILURE != SOURCE_FAILURE`

Before returning `UNAVAILABLE_AFTER_FALLBACK`, the Bridge may inspect authorized alternative representations and preserve the relationship between them.

## Resolution order

1. `LIVE_HTML`
2. `API_METADATA`
3. `SAVED_HTML`
4. `SAVED_PDF`
5. `DOI_METADATA`
6. `ORCID_SNAPSHOT`
7. `REPOSITORY_FILE`
8. `GIT_COMMIT`
9. `HF_SNAPSHOT`
10. `CORPUS_COPY`
11. `MODEL_REPORT`

The order is explicit and versionable. Adapters are I/O boundaries; adapter failure does not abort the full resolution path.

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

## SourceEvidence

A resolved source preserves:

- original and resolved locator;
- all representations consulted;
- content hashes;
- capture time when known;
- identifiers such as DOI, ORCID, repository, commit SHA and canonical URL;
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

A name, acronym or page is not canonical merely because it is frequent or currently reachable.

A source may only be promoted when provenance, identity and conflicts are resolved to the level required by the consuming gate.
