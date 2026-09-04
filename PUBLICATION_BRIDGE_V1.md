# MatVerse Publication Bridge v1

## Scope

`Publication Bridge v1` converts a reviewed manuscript plus explicit author-supplied metadata into a validated arXiv submission workspace, authenticates to the author's arXiv account without persisting credentials in the repository, and opens the filled arXiv submission flow for final author review.

It deliberately does **not** perform the final arXiv submission click. arXiv expects authors to self-submit and requires agreement to its submission terms. The bridge therefore automates preparation, validation, authentication, upload and form filling while preserving the final legal/editorial action for the author.

## Security boundary

The following values are forbidden in publication manifests, receipts and Git history:

- passwords;
- tokens;
- API keys;
- stored browser credentials;
- credential payloads.

arXiv credentials are supplied only at runtime through local environment variables:

```bash
export ARXIV_EMAIL='author@example.org'
export ARXIV_PASSWORD='...'
```

`app.publication_bridge` maps them in-memory to the credential variables supported by the pinned PaperPush transport. They are never written to a MatVerse file and error messages are redacted.

Prefer a local terminal/session for this flow. Do not add these values to workflow YAML, manifests, issues, pull requests, chat messages or source files.

## Installation

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-publication.txt
playwright install chromium
```

The PaperPush transport is pinned to commit:

```text
pachterlab/paperpush@3cc701d91bf78c046f008477baad40e7fa53ff4f
```

## Manifest

Example:

```json
{
  "schema": "matverse.publication-bridge.v1",
  "publication_id": "matverse-2.0",
  "manuscript_file": "dist/matverse-2.0-arxiv.zip",
  "primary_archive": "cs",
  "primary_category": "cs.AI",
  "crosslist_archives": ["cs"],
  "crosslist_categories": ["cs.SE"],
  "license": "CC BY 4.0",
  "title": "MATVERSE 2.0: A Constitutional Architecture for Governed Informational Transformation in Federated Research Systems",
  "authors": ["Mateus Alves Areas"],
  "abstract": "Replace this example with the final reviewed abstract before preparing the submission.",
  "comments": "Preprint"
}
```

`license` is mandatory because the bridge must not infer a legal distribution choice. The author chooses it explicitly before preparation.

## Execution

### 1. Prepare and validate

```bash
python -m app.publication_bridge prepare \
  --manifest publication/matverse-2.0.json \
  --work-root .publication
```

The command:

1. validates the MatVerse publication manifest;
2. rejects secret-like fields fail-closed;
3. hashes the manuscript;
4. creates a PaperPush arXiv submission file;
5. applies explicit metadata using the deterministic/manual PaperPush path;
6. validates the arXiv submission description;
7. produces `publication-state.json` and a MatVerse evidence receipt.

### 2. Authenticate with the author's account

```bash
export ARXIV_EMAIL='author@example.org'
export ARXIV_PASSWORD='...'
python -m app.publication_bridge login --workdir .publication/matverse-2.0
```

Alternative, if the author prefers browser-only entry rather than environment variables:

```bash
python -m app.publication_bridge login \
  --workdir .publication/matverse-2.0 \
  --interactive-login
```

### 3. Check authentication

```bash
python -m app.publication_bridge login-status \
  --workdir .publication/matverse-2.0
```

### 4. Open the arXiv form for author review

```bash
python -m app.publication_bridge open-author-review \
  --state .publication/matverse-2.0/publication-state.json
```

This validates the submission again, opens/fills the arXiv portal and leaves the final submission action to the author.

### 5. Verify local integrity

```bash
python -m app.publication_bridge verify \
  --state .publication/matverse-2.0/publication-state.json \
  --manifest publication/matverse-2.0.json
```

If the manuscript changes after preparation, verification fails closed and the publication must be prepared again.

## State semantics

```text
MANUSCRIPT
   ↓
MANIFEST
   ↓
secret boundary
   ↓
hash + metadata validation
   ↓
PaperPush arXiv transport
   ↓
VALIDATED
   ↓
author-authorized authentication
   ↓
portal upload/form fill
   ↓
AUTHOR REVIEW
   ↓
final author action outside Bridge authority
```

A successful `open-author-review` event means only that the author review session was opened/completed by the transport. It does not claim that arXiv accepted, announced or published the paper.

## Evidence semantics

The bridge emits a `matverse.evidence-receipt.v1` receipt for preparation and another receipt for the author-review session. Credentials never enter either receipt.

The bridge does not emit `PUBLISHED` from browser automation. Publication/announcement must later be reconciled from the externally assigned arXiv identifier and external state.
