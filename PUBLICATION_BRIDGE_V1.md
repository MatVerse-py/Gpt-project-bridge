# MatVerse Publication Bridge v1

## Role

The Publication Bridge is the **venue transport layer**. It does not own scientific identity, claims, lineage or publication authority.

Canonical ownership is now:

```text
MARXIV Scientific Object
        |
        v
MARXIV Runtime Publisher
  metadata / sandbox / approval
        |
        v
MatVerse Publication Bridge
  venue transport / authentication
        |
        v
arXiv
```

For the full governed lifecycle, use `app.marxiv_runtime_publisher` and see `MARXIV_RUNTIME_PUBLISHER_V1.md`.

## arXiv transport boundary

`app.publication_bridge` provides:

- strict arXiv manifest validation;
- secret-field rejection;
- deterministic manuscript/manifest hashes;
- generation and validation of a PaperPush `arxiv.sub`;
- EvidenceOS preparation receipts;
- runtime-only arXiv credentials through `ARXIV_EMAIL` and `ARXIV_PASSWORD`;
- author/browser review transport;
- tamper verification.

The MARXIV Runtime Publisher adds the higher-order authority model in which the human approves the exact immutable publication package before delegated final submission authority becomes active.

## Runtime credentials

Credentials must never be written to a publication manifest, MARXIV Scientific Object, receipt or repository.

Local environment only:

```bash
export ARXIV_EMAIL='author-account@example.org'
export ARXIV_PASSWORD='set-only-in-the-local-runtime'
```

The bridge maps those values in memory to the pinned PaperPush login transport. Partial credentials fail closed.

## Prepare a raw arXiv transport package

The lower-level bridge remains directly usable:

```bash
python -m app.publication_bridge prepare \
  --manifest /absolute/path/arxiv-manifest.json \
  --work-root .publication
```

This creates a validated transport package and state receipt. It is not a scientific publication authorization.

## Preferred path: MARXIV

For governed publication:

```bash
python -m app.marxiv_runtime_publisher prepare --object scientific-object.json
python -m app.marxiv_runtime_publisher request-approval --sandbox .marxiv/<object>/<version>
python -m app.marxiv_runtime_publisher approve --sandbox .marxiv/<object>/<version> --approver <id> --confirm '<exact challenge phrase>'
python -m app.marxiv_runtime_publisher publish --sandbox .marxiv/<object>/<version>
```

The MARXIV publisher re-verifies package hashes and human authority immediately before any external submission effect.

## Transport dependency

The publication transport is pinned in `requirements-publication.txt` to a specific PaperPush revision so portal automation does not silently drift between installations.

## Evidence boundary

The lower-level preparation flow and secret boundary are covered by CI. The MARXIV sandbox/approval/publish state machine is independently covered by `tests/test_marxiv_runtime_publisher.py`.

A real author-authorized final arXiv submission through the MARXIV finalizer remains a live operational pilot target until it is actually executed and reconciled.
