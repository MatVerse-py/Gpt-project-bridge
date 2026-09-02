# Source Evidence Resolution v1

## Purpose

The Bridge resolves evidence from multiple representations without confusing accessibility, visual appearance, version identity, publication status or scientific validity.

A resolution may combine live pages, APIs, preserved documents, LaTeX sources, repository objects, images and model reports. Provenance determines authority.

## Representation principle

A representation supports only the predicates for which its provenance is competent.

Examples:

- `LATEX_SOURCE`: source content/version;
- `ARXIV_EPRINT_SOURCE`: source content/version plus bounded third-party custody when independently verified;
- `DOI_METADATA`: publication/identifier metadata;
- `GIT_COMMIT`: repository version/timestamp lineage;
- `SAVED_PDF`: preserved compiled document content;
- `SCREENSHOT`: visible rendered state;
- `GENERATED_IMAGE`: generated visual claim only.

No representation receives universal authority merely because it has a high compatibility tier.

## Compatibility tier versus claim authority

`P0..P5` is retained as a compatibility/fallback ranking.

It is **not** a universal truth score.

Claim-scoped authority is separately exposed for:

`content | version | authorship | publication | timestamp | execution`

Policy authority values are non-probabilistic weights. Aggregation uses the strongest independent admissible root per domain rather than probabilistic multiplication.

## LaTeX source rules

### Complete closure

A `.tex` entry is a complete source only after its transitive local artifact closure is resolved. The closure includes local inputs/includes, figures, bibliographies and local class/style files.

Incomplete source closure:

- state: `PARTIAL` when it is the only independent root;
- compatibility tier capped at `P1`;
- zero claim-scoped authority;
- not admissible as an official source;
- records `LATEX_CLOSURE_INCOMPLETE` when officiality was requested.

### Claimed identifiers

DOI/ORCID strings written in TeX are `CLAIMED_IDENTIFIER`, not resolved metadata.

They are exposed under `claimed_identifiers` and do not enter `identifiers` until independently resolved by a competent representation such as DOI metadata, ORCID metadata, Git lookup or another external registry.

### Official version

`official_version=true` is necessary but insufficient.

Official source/version evidence requires:

`complete closure + verified immutable provenance anchor`

Accepted verified anchors include commit, release tag, manifest digest, canonical locator or signature.

A complete anchored TeX source receives effective `P5` **only for source/version identity**. Publication authority remains zero unless an independent publication representation exists.

## arXiv distinction

`LATEX_SOURCE != ARXIV_EPRINT_SOURCE`

An arXiv-shaped locator or filename does not produce publication authority. `ARXIV_EPRINT_SOURCE` receives third-party custody/timestamp authority only when the adapter independently verifies external timestamp plus canonical/signature custody.

## Structured conflicts

High-priority structured identifier/version disagreements are blocking `IDENTIFIER_CONFLICT` events.

Image-vs-structured disagreement is retained as `IMAGE_METADATA_CONFLICT`; structured metadata keeps identity precedence, but the visual observation is preserved.

## Stale prose

Human-readable prose that says a DOI is pending may be stale while structured DOI metadata is current. `STALE_PROSE` is recorded without allowing stale prose to overwrite structured metadata.

## Generated and derivative representations

Generated images never become independent evidence roots.

Document page renders are derivative and cannot create a second root from the document they render.

Exact SHA-256 duplicates count as one root. Perceptual similarity is only a review signal.

## Tamper rule

Any representation marked with a mismatched expected hash produces `BLOCK_TAMPERED` and zero authority for the resolution.

## Receipts

`matverse.evidence-receipt.v1` commits by hash to:

- resolved state;
- compatibility tier;
- claim-scoped authority;
- resolved identifiers;
- claimed identifiers;
- official-version decision;
- conflicts;
- representation hashes;
- TeX closure status when applicable.

The receipt stores commitments (`input_hash`, `output_hash`, `merkle_root`, `receipt_hash`), not a plaintext duplicate of the full output payload.

## Bridge → ARGUS exchange

`app/source_exchange.py` defines the transport-neutral contract:

`matverse.bridge-evidence-batch.v1`

Each exported representation carries:

- `locator`;
- `representation`;
- original `source_content_hash`;
- `evidence_root_id`;
- independent/derivative provenance;
- a small allowlisted metadata set;
- optional explicit `claim_relation`;
- optional `observed_text` only when the caller deliberately supplies it;
- `observed_text_sha256` when text is disclosed.

Raw source content is never exported implicitly. Private/unrecognized metadata is omitted from the wire representation.

The consumer must not treat Bridge authority as a universal truth score. The URANO ARGUS adapter re-evaluates authority locally from the representation class and uses Bridge hashes/relations as provenance inputs. In particular:

`Bridge claim_relation=SUPPORTS != ARGOS PASS`

A source may support a claim semantically while still remaining below the local ARGOS authority threshold, producing `SUPPORTED/HOLD` rather than bypassing policy.

The exchange module is transport-agnostic: HTTP, MCP, a local catalog or another Bridge adapter may expose the same schema without changing evidence semantics.

## Current LaTeX operational boundary

Transitive closure scanning and closure hashing are implemented and covered by 12 dedicated TeX policy tests.

The governed root manifest is `evidence/latex_roots.json`. It currently remains `HOLD_NO_DECLARED_CANONICAL_ROOTS`; no paper `.tex -> deposited PDF` pair is invented.

The dedicated LaTeX diagnostic CI may therefore be green while reporting that HOLD. CI PASS proves that the diagnostic/policy behaved as specified; it does not prove a real source-to-PDF derivation.

LaTeX-to-PDF derivation verification remains HOLD until real canonical source/PDF pairs are declared. The intended next stage is a pinned build environment and normalized PDF invariant comparison, not naive byte equality.
