# Source Evidence Image Policy v1

Native screenshots and exported images (`.png`, `.jpg`, `.jpeg`, `.webp`) are valid preserved representations, but they do not have the same authority as structured HTML/API metadata.

## Representation classes

- `SAVED_IMAGE`: native preserved image whose origin is known or linked to a source.
- `SCREENSHOT`: image capturing a rendered UI/page state.
- `GENERATED_IMAGE`: synthetic/model-generated visual; never used as independent external evidence.

## What an image can support

A screenshot can support observations visibly present in pixels, for example:
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
- that an interface value was not mocked/generated.

Those claims require cross-check with structured metadata, APIs, DOI registry, Git commits, chain explorer, or other independent source.

## Fail-closed rules

1. `GENERATED_IMAGE` has `independent_evidence = false`.
2. Screenshot text is recorded as `VISUAL_OBSERVATION`, not canonical structured metadata.
3. If screenshot content conflicts with structured platform metadata, record `IMAGE_METADATA_CONFLICT` and prefer structured metadata for machine identity fields.
4. If the image claims an external event (deploy, transaction, DOI, publication), require an external identifier cross-check before `VERIFIED`.
5. Store SHA-256 of the native image bytes. A byte mismatch against an expected hash is `BLOCK_TAMPERED`.
6. Duplicate images with identical hash count as one evidence root, not independent corroboration.

## MatVerse examples observed in corpus

- `URANO OSX MatVerse Organism Console v2.0`: screenshot can prove the interface displayed labels/status values, but not that the backend services actually held those states.
- `Production Gate v0.3` PNG report/slide: supports the historical claim that the report displayed `PASS_LOCAL_PRODUCTION__HOLD_EXTERNAL_WITNESS`; it remains artifact-reported state.
- `Ω-SEED Genesis Artifact Deployed`: contains displayed GitHub/HF/Sepolia/Zenodo claims; each external deployment must be independently resolved before upgrading from visual observation to verified external evidence.

## Ordering

For identifiers and publication metadata:

`API/structured HTML > saved HTML > repository/commit metadata > saved PDF > screenshot/image > model report`

For rendered historical UI state, screenshots can be the primary representation, but only for what is visibly rendered.
