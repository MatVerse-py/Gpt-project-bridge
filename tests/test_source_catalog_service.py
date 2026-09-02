from fastapi.testclient import TestClient

from app.source_catalog_service import (
    ARGUS_BATCH_SCHEMA,
    CATALOG_SCHEMA,
    BridgeEvidenceCatalog,
    create_app,
)


def catalog():
    return BridgeEvidenceCatalog.from_payload(
        {
            "schema": CATALOG_SCHEMA,
            "items": [
                {
                    "locator": "api://record/alpha",
                    "representation": "API_METADATA",
                    "source_content_hash": "a" * 64,
                    "evidence_root_id": "root-alpha",
                    "independent": True,
                    "claim_relation": "SUPPORTS",
                    "relation_claim_ref": "claim://bound",
                    "search_text": "MatVerse ARGUS factual integrity record",
                    "metadata": {"title": "ARGUS factual integrity record"},
                },
                {
                    "locator": "image://generated",
                    "representation": "GENERATED_IMAGE",
                    "source_content_hash": "b" * 64,
                    "evidence_root_id": "root-image",
                    "independent": False,
                    "model_generated": True,
                    "search_text": "decorative unrelated image",
                    "metadata": {},
                },
            ],
        }
    )


def test_catalog_search_is_deterministic_and_does_not_emit_search_text_or_unbound_relation():
    result = catalog().search("ARGUS factual integrity", claim_ref="claim://other", max_sources=5)
    assert result["schema"] == ARGUS_BATCH_SCHEMA
    assert result["state"] == "PARTIAL"
    assert result["catalog_match_count"] == 1
    assert result["items"][0]["locator"] == "api://record/alpha"
    assert "search_text" not in result["items"][0]
    assert "relation_claim_ref" not in result["items"][0]
    assert "claim_relation" not in result["items"][0]


def test_catalog_emits_relation_only_for_bound_claim():
    result = catalog().search("ARGUS factual integrity", claim_ref="claim://bound")
    assert result["items"][0]["claim_relation"] == "SUPPORTS"


def test_catalog_no_match_fails_closed_as_unavailable():
    result = catalog().search("completely absent phrase", claim_ref="claim://1")
    assert result["state"] == "UNAVAILABLE_AFTER_FALLBACK"
    assert result["items"] == []
    assert result["evidence_tier"] == "P0"


def test_sidecar_exposes_query_contract():
    client = TestClient(create_app(lambda: catalog()))
    response = client.post(
        "/evidence/query",
        json={
            "schema": "matverse.argus-evidence-query.v1",
            "claim_ref": "claim://bound",
            "claim_text": "ARGUS factual integrity",
            "max_sources": 8,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema"] == ARGUS_BATCH_SCHEMA
    assert payload["catalog_match_count"] == 1
    assert payload["items"][0]["claim_relation"] == "SUPPORTS"


def test_sidecar_rejects_unknown_query_schema():
    client = TestClient(create_app(lambda: catalog()))
    response = client.post(
        "/evidence/query",
        json={
            "schema": "wrong",
            "claim_ref": "claim://1",
            "claim_text": "ARGUS factual integrity",
        },
    )
    assert response.status_code == 422
