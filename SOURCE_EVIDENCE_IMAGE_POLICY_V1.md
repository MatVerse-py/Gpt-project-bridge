# Source Evidence Image Policy v1.1

Native screenshots and exported images (`.png`, `.jpg`, `.jpeg`, `.webp`) are valid preserved representations, but they do not have the same authority as structured HTML/API metadata.

The governing rule is **provenance before appearance**: an image marked `model_generated=true` remains generated evidence even if it visually resembles a report, browser page, dashboard, transaction receipt, or scientific figure.

## Representation classes

- `SAVED_IMAGE`: native preserved image whose non-generated origin is known or linked to a source.
- `SCREENSHOT`: image capturing a rendered UI/page state.
- `DOCUMENT_PAGE_RENDER`: derived image rendered from a PDF/document; its evidence root is the parent document, not the PNG render.
- `GENERATED_IMAGE`: synthetic/model-generated visual; never used as independent external evidence.

## What an image can support

A non-generated screenshot can support observations visibly present in pixels, for example:
- displayed title or label;
- visible timestamp/date;
- status badge;
- DOI/ORCID/repository identifier rendered in the interface;
- visible state of a dashboard or public surface.

It cannot by itself prove:
- that the displayed backend state was genuine;
- that an on-chain transaction exists;
- that a DOI resolves;
- that a scientific claim is valid;
- that an interface value was not mocked;
- that a plugin shown in UI was authorized/connected;
- that a suggested Crossref/Zenodo action contains Crossref/Zenodo source data.

Those claims require cross-check with structured metadata, APIs, DOI registry, Git commits, chain explorer, or other independent source.

## Fail-closed rules

1. `GENERATED_IMAGE`, `model_generated=true`, or `generated=true` => `independent_evidence = false`, regardless of visual appearance.
2. `DOCUMENT_PAGE_RENDER` is derivative and cannot create an additional independent root beyond its parent PDF/document.
3. Screenshot/OCR text is recorded as `VISUAL_OBSERVATION`, not canonical structured metadata.
4. Generated-image DOI/ORCID/repo strings are claims only and are excluded from resolved identifier selection until independently observed.
5. If screenshot content conflicts with structured platform metadata, record `IMAGE_METADATA_CONFLICT` and prefer structured metadata for machine identity fields.
6. If the image claims an external event (deploy, transaction, DOI, publication), require an external identifier cross-check before promotion.
7. Store SHA-256 of native image bytes. A byte mismatch against an expected hash is `BLOCK_TAMPERED`.
8. Images with identical SHA-256 count as one evidence root, not independent corroboration.
9. Perceptual hash, OCR similarity, same dimensions, or human visual similarity identify only `NEAR_DUPLICATE_CANDIDATE`; they do not collapse evidence roots automatically.
10. Same file size is never sufficient evidence of duplication. If raw backing bytes are unavailable, duplicate status remains `HOLD`.

## MatVerse examples observed in corpus

- `URANO OSX MatVerse Organism Console v2.0`: a captured screenshot can prove that the interface displayed labels/status values, but not that backend services actually held those states.
- `Production Gate v0.3` `chart-*.png`: Library provenance marks these images as model-generated. They therefore preserve what a generated rendering asserted, but do **not** independently corroborate production/replay/witness state.
- `Ω-SEED Genesis Artifact Deployed`: generated visual containing GitHub/HF/Sepolia/Zenodo claims. Each external deployment must be independently resolved before promotion.
- `Meus plugins` screenshot: proves GitHub/Hugging Face options were displayed; it does not prove connector authorization or account content.
- Crossref/Zenodo suggestion screenshot: proves those suggestions were rendered; it contains no Crossref/Zenodo evidence payload.

## Duplicate roots observed

Exact SHA groups are kept in `tests/fixtures/visual_duplicate_roots.json`. Exact byte duplicates collapse to one probative root. Visual near-duplicates remain separate unless a verified derivation relation is available.

## Ordering

For identifiers and publication metadata:

`API/structured HTML > saved HTML > repository/commit metadata > saved PDF > non-generated screenshot/image > model report > generated image`

For rendered historical UI state, a non-generated screenshot can be the primary representation, but only for what is visibly rendered.
