import json

import pytest

from app.acquisition_adapters import (
    AcquisitionError,
    AcquisitionRegistry,
    AcquisitionRequest,
    CrossrefAdapter,
    DataCiteAdapter,
    GitHubAdapter,
    HuggingFaceAdapter,
    OrcidAdapter,
    ZenodoAdapter,
    append_observations_to_catalog,
)
from app.source_evidence import RepresentationType


def transport_for(payload, *, headers=None):
    raw = json.dumps(payload).encode("utf-8")
    response_headers = headers or {"content-type": "application/json", "etag": '"fixture"'}

    def transport(url, request_headers, timeout):
        assert url.startswith("https://")
        assert timeout > 0
        assert "Accept" in request_headers
        return raw, response_headers

    return transport


def test_crossref_maps_doi_metadata_without_claim_relation():
    adapter = CrossrefAdapter(
        transport=transport_for(
            {
                "message": {
                    "DOI": "10.1234/ABC",
                    "title": ["A governed paper"],
                    "author": [{"given": "Ada", "family": "Example"}],
                    "publisher": "Example Press",
                    "published": {"date-parts": [[2026, 9, 2]]},
                    "URL": "https://doi.org/10.1234/abc",
                    "container-title": ["Journal"],
                    "type": "journal-article",
                }
            }
        )
    )
    observation = adapter.acquire(AcquisitionRequest("crossref", "https://doi.org/10.1234/ABC"))
    assert observation.representation.kind is RepresentationType.DOI_METADATA
    assert observation.representation.metadata["doi"] == "10.1234/abc"
    assert observation.representation.metadata["author"] == ["Ada Example"]
    assert "claim_relation" not in observation.catalog_item()
    assert observation.catalog_item()["independent"] is True


def test_datacite_maps_version_and_publication_metadata():
    adapter = DataCiteAdapter(
        transport=transport_for(
            {
                "data": {
                    "attributes": {
                        "doi": "10.5678/example",
                        "titles": [{"title": "Dataset title"}],
                        "creators": [{"name": "Researcher One"}],
                        "publisher": "Repository",
                        "published": "2026-09-02",
                        "version": "v2",
                        "url": "https://example.test/dataset",
                        "types": {"resourceTypeGeneral": "Dataset"},
                    }
                }
            }
        )
    )
    observation = adapter.acquire(AcquisitionRequest("datacite", "10.5678/example"))
    assert observation.representation.kind is RepresentationType.DOI_METADATA
    assert observation.representation.metadata["version"] == "v2"
    assert observation.representation.metadata["type"] == "Dataset"


def test_zenodo_accepts_zenodo_doi_and_preserves_record_identity():
    adapter = ZenodoAdapter(
        transport=transport_for(
            {
                "id": 19112302,
                "doi": "10.5281/zenodo.19112302",
                "created": "2026-05-23T00:00:00Z",
                "links": {"self_html": "https://zenodo.org/records/19112302"},
                "metadata": {
                    "title": "Paper 06 Runtime",
                    "creators": [{"name": "Mateus Arêas"}],
                    "publication_date": "2026-05-23",
                    "version": "1.0",
                    "resource_type": {"type": "publication"},
                },
            }
        )
    )
    observation = adapter.acquire(AcquisitionRequest("zenodo", "10.5281/zenodo.19112302"))
    assert observation.identifier == "19112302"
    assert observation.representation.metadata["doi"] == "10.5281/zenodo.19112302"
    assert observation.representation.kind is RepresentationType.API_METADATA


def test_orcid_maps_identity_but_not_work_content():
    adapter = OrcidAdapter(
        transport=transport_for(
            {
                "person": {
                    "name": {
                        "given-names": {"value": "Mateus"},
                        "family-name": {"value": "Arêas"},
                    }
                }
            }
        )
    )
    observation = adapter.acquire(AcquisitionRequest("orcid", "https://orcid.org/0009-0008-2973-4047"))
    assert observation.representation.kind is RepresentationType.ORCID_SNAPSHOT
    assert observation.representation.metadata["orcid"] == "0009-0008-2973-4047"
    assert observation.representation.metadata["author"] == "Mateus Arêas"


def test_github_commit_is_git_commit_representation():
    adapter = GitHubAdapter(
        transport=transport_for(
            {
                "sha": "a" * 40,
                "html_url": "https://github.com/MatVerse-py/urano-os/commit/" + "a" * 40,
                "commit": {
                    "message": "feat: governed evidence",
                    "author": {"name": "SimbiOS/Matverse", "date": "2026-09-02T18:00:00Z"},
                },
            }
        )
    )
    observation = adapter.acquire(AcquisitionRequest("github", "MatVerse-py/urano-os", ref="a" * 40))
    assert observation.representation.kind is RepresentationType.GIT_COMMIT
    assert observation.representation.metadata["commit_sha"] == "a" * 40
    assert observation.representation.metadata["repo"] == "https://github.com/MatVerse-py/urano-os"


def test_huggingface_dataset_snapshot_is_bounded():
    adapter = HuggingFaceAdapter(
        transport=transport_for(
            {
                "author": "MatverseHub",
                "sha": "b" * 40,
                "lastModified": "2026-06-20T00:00:00Z",
                "cardData": {"title": "MatVerse dataset"},
            }
        )
    )
    observation = adapter.acquire(
        AcquisitionRequest("huggingface", "MatverseHub/matverse-superdataset", resource_type="dataset")
    )
    assert observation.representation.kind is RepresentationType.HF_SNAPSHOT
    assert observation.representation.metadata["resource_type"] == "dataset"
    assert "/datasets/MatverseHub/matverse-superdataset" in observation.representation.metadata["canonical_url"]


def test_registry_unknown_provider_fails_closed():
    registry = AcquisitionRegistry.default(transport=transport_for({}))
    with pytest.raises(AcquisitionError):
        registry.acquire(AcquisitionRequest("unknown", "id"))


def test_catalog_append_deduplicates_same_root():
    adapter = CrossrefAdapter(transport=transport_for({"message": {"DOI": "10.1/x", "title": ["T"]}}))
    observation = adapter.acquire(AcquisitionRequest("crossref", "10.1/x"))
    base = {"schema": "matverse.bridge-evidence-catalog.v1", "items": []}
    result = append_observations_to_catalog(base, [observation, observation])
    assert len(result["items"]) == 1
    assert result["items"][0]["evidence_root_id"].startswith("ext:")
