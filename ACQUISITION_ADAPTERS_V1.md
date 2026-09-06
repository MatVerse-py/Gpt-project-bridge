# Acquisition Adapters v1

## Purpose

External acquisition is a sensor layer for the Bridge evidence catalog. It observes named public services and converts their responses into governed `SourceRepresentation` objects.

Acquisition is **not adjudication**:

`HTTP/API success != claim truth != scientific validity`.

Adapters never assign `claim_relation`. Claim-scoped support/contradiction must be established later by an explicit comparator/adjudication step.

## Providers

| provider | input | representation | primary bounded use |
|---|---|---|---|
| `crossref` | DOI | `DOI_METADATA` | DOI/publication metadata |
| `datacite` | DOI | `DOI_METADATA` | DOI/version/publication metadata |
| `zenodo` | record id or Zenodo DOI | `API_METADATA` | repository record metadata |
| `orcid` | ORCID | `ORCID_SNAPSHOT` | identity/account-linked metadata |
| `github` | `owner/repo` plus optional ref | `API_METADATA` or `GIT_COMMIT` | repo/commit lineage |
| `huggingface` / `hf` | namespace/name + model/dataset/space | `HF_SNAPSHOT` | Hub snapshot/version metadata |

## Evidence-root rule

An acquisition root is derived from:

`provider + normalized identifier + response content hash`.

Repeated acquisition of identical bytes from the same provider/identifier yields the same root and cannot inflate evidence count. A changed provider response becomes a new root/version observation.

## Integrity and timestamps

The SHA-256 stored for the representation is the hash of the exact response bytes observed by the adapter.

Provider timestamps (`published`, commit date, lastModified, etc.) remain metadata supplied by that provider. They are not interchangeable with the Bridge acquisition time or proof of an external event beyond that provider's competence.

HTTP `ETag`, `Last-Modified` and content type are preserved when available, but they do not replace content hashing.

## Secrets

GitHub and Hugging Face tokens may be supplied from environment variables in deployment wrappers. Tokens are request headers only and are never written into `SourceRepresentation`, catalog items or reports.

The acquisition CLI reads:

- `GITHUB_TOKEN` for GitHub when present;
- `HF_TOKEN` or `HUGGINGFACE_TOKEN` for Hugging Face when present.

## CLI

Use an explicit manifest:

```bash
python scripts/acquire_evidence.py \
  evidence/acquisition_manifest.example.json \
  --catalog evidence/source_catalog.json \
  --report /tmp/acquisition-report.json
```

Manifest schema:

`matverse.bridge-acquisition-manifest.v1`

Report schema:

`matverse.bridge-acquisition-report.v1`

Catalog schema:

`matverse.bridge-evidence-catalog.v1`

`fail_fast=false` records provider failures without discarding successful acquisitions. `fail_fast=true` stops on the first failure.

## Fail-closed boundaries

- unknown provider -> acquisition error;
- malformed provider JSON -> acquisition error;
- missing required provider structure -> acquisition error;
- provider failure does not mean a claim is false;
- no adapter may create `SUPPORTS`/`CONTRADICTS` merely because a record was found;
- metadata authority remains predicate-specific in `SourceEvidence`;
- duplicate roots are not counted twice;
- web/news search remains a separate discovery adapter and must preserve URLs, response hashes and provenance if added later.

## Testing

All provider parsers are exercised offline with injected transports. CI therefore tests deterministic normalization and evidence semantics without depending on live third-party availability.

A live deployment should add periodic contract probes separately from the deterministic unit suite; live API availability is operational evidence, not a substitute for parser tests.
