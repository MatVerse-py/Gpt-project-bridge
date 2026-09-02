# Real evidence fixture manifests

This directory stores **manifests and observations**, not a public dump of the user's raw preserved evidence corpus.

## Source roots represented

- saved Zenodo HTML records with structured citation metadata;
- saved ORCID/Authorea/Hugging Face PDFs;
- native screenshots and visual artifacts analyzed from the Library;
- SHA-256 duplicate-root groups.

## Raw evidence policy

Raw HTML/PDF/PNG/JPG/WebP files remain outside the public repository by default. Tests use synthetic equivalents for behavior, while manifests pin identifiers, hashes, provenance and expected observations from the real corpus.

## Visual evidence rules

- `model_generated=true` overrides appearance: it is non-independent even if the image looks like a report or screenshot.
- PDF page PNG renders are derivative of the PDF and do not create a second root.
- OCR/visible text is `VISUAL_OBSERVATION`, not structured metadata.
- exact SHA-256 duplicate files collapse to one probative root.
- perceptual similarity is only a near-duplicate review signal; different SHA-256 values remain separate roots unless a derivation relation is independently verified.
- same file size is insufficient to assert duplication.
- unavailable raw backing bytes => `HOLD`, never an inferred hash/duplicate result.

See:
- `real_evidence_manifest.json`
- `real_image_observations.json`
- `visual_duplicate_roots.json`
- `SOURCE_EVIDENCE_IMAGE_POLICY_V1.md`
