# Real Evidence Fixtures

This directory contains a manifest of real MatVerse evidence sources observed in the user's preserved corpus.

The raw binary/source files are intentionally not committed here by default. The manifest records the expected identity/visible fields and the evidence policy for each source class. Raw files should be mounted/materialized in CI or local audit jobs from an authorized evidence vault.

Why:
- avoid silently duplicating private/library artifacts into a public repository;
- keep source hashes/provenance separate from semantic assertions;
- allow the same test contract to run against authorized raw HTML/PDF/PNG/JPG/WebP files;
- generated images are explicitly marked non-independent evidence.

For HTML, tests may assert structured metadata.
For PDFs, tests assert extracted metadata plus native-byte hash when available.
For screenshots, tests assert only visible observations unless independently cross-checked.
