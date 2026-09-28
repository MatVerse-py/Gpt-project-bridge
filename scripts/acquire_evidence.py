from __future__ import annotations

"""Populate a Bridge evidence catalog from explicit provider requests.

Usage:
    python scripts/acquire_evidence.py requests.json --catalog evidence/catalog.json

The command performs acquisition only. It does not assign claim relations and
never adjudicates truth. Provider secrets may be supplied by environment-backed
adapter construction in a later deployment wrapper; this CLI never writes
secrets into the catalog.
"""

from pathlib import Path
import argparse
import json
import os
import tempfile

from app.acquisition_adapters import (
    AcquisitionError,
    AcquisitionRegistry,
    AcquisitionRequest,
    GitHubAdapter,
    HuggingFaceAdapter,
    CrossrefAdapter,
    DataCiteAdapter,
    ZenodoAdapter,
    OrcidAdapter,
    append_observations_to_catalog,
)


MANIFEST_SCHEMA = "matverse.bridge-acquisition-manifest.v1"
CATALOG_SCHEMA = "matverse.bridge-evidence-catalog.v1"


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _registry() -> AcquisitionRegistry:
    github_token = os.environ.get("GITHUB_TOKEN") or None
    hf_token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN") or None
    return AcquisitionRegistry(
        {
            "crossref": CrossrefAdapter(),
            "datacite": DataCiteAdapter(),
            "zenodo": ZenodoAdapter(),
            "orcid": OrcidAdapter(),
            "github": GitHubAdapter(token=github_token),
            "huggingface": HuggingFaceAdapter(token=hf_token),
            "hf": HuggingFaceAdapter(token=hf_token),
        }
    )


def run_manifest(manifest: dict, catalog: dict, registry: AcquisitionRegistry) -> tuple[dict, list[dict]]:
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise AcquisitionError("unsupported acquisition manifest schema")
    requests = manifest.get("requests")
    if not isinstance(requests, list):
        raise AcquisitionError("manifest requires requests[]")

    observations = []
    report = []
    for index, row in enumerate(requests):
        if not isinstance(row, dict):
            raise AcquisitionError(f"request {index} must be an object")
        provider = str(row.get("provider") or "").strip()
        identifier = str(row.get("identifier") or "").strip()
        if not provider or not identifier:
            raise AcquisitionError(f"request {index} requires provider and identifier")
        req = AcquisitionRequest(
            provider=provider,
            identifier=identifier,
            resource_type=(str(row.get("resource_type")).strip() if row.get("resource_type") is not None else None),
            ref=(str(row.get("ref")).strip() if row.get("ref") is not None else None),
        )
        try:
            observation = registry.acquire(req)
        except Exception as exc:
            report.append(
                {
                    "index": index,
                    "provider": provider,
                    "identifier": identifier,
                    "status": "ERROR",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            if manifest.get("fail_fast") is True:
                raise
            continue
        observations.append(observation)
        report.append(
            {
                "index": index,
                "provider": provider,
                "identifier": identifier,
                "status": "ACQUIRED",
                "representation": observation.representation.kind.value,
                "content_hash": observation.representation.content_hash,
                "evidence_root_id": observation.evidence_root_id,
            }
        )

    updated = append_observations_to_catalog(catalog, observations)
    return updated, report


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as fh:
        fh.write(data)
        temp_name = fh.name
    Path(temp_name).replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Acquire governed evidence into the Bridge catalog")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    manifest = _load_json(args.manifest)
    if args.catalog.exists():
        catalog = _load_json(args.catalog)
    else:
        catalog = {"schema": CATALOG_SCHEMA, "items": []}

    updated, report = run_manifest(manifest, catalog, _registry())
    _atomic_write(args.catalog, updated)

    report_payload = {
        "schema": "matverse.bridge-acquisition-report.v1",
        "request_count": len(manifest.get("requests") or []),
        "acquired_count": sum(1 for row in report if row["status"] == "ACQUIRED"),
        "error_count": sum(1 for row in report if row["status"] == "ERROR"),
        "results": report,
    }
    if args.report:
        _atomic_write(args.report, report_payload)
    else:
        print(json.dumps(report_payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report_payload["error_count"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
