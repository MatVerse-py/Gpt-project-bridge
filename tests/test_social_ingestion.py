from __future__ import annotations

import pytest

from app.social_ingestion import SocialIngestionService
from app.social_transport import TransportResult


def test_authorized_transport_becomes_social_evidence():
    service = SocialIngestionService.default()
    result = TransportResult(
        url="https://www.instagram.com/p/ABC123/?igsh=tracking",
        payload={
            "id": "123",
            "username": "matverse",
            "caption": "governed social evidence",
            "media_type": "IMAGE",
            "_acquisition_mode": "official_api",
            "_transport": "meta.instagram.v1",
        },
        authorized=True,
        transport="meta.instagram.v1",
    )

    evidence = service.ingest_transport_result(result)

    assert evidence.source.authorized is True
    assert evidence.source.acquisition_mode.value == "official_api"
    assert evidence.source.canonical_url == "https://www.instagram.com/p/ABC123"
    assert evidence.external_id == "123"
    assert evidence.author_handle == "matverse"
    assert evidence.text == "governed social evidence"
    assert evidence.payload_hash
    assert evidence.receipt["event_type"] == "social.ingest"
    assert "_transport" not in evidence.attributes


def test_unauthorized_transport_is_rejected():
    service = SocialIngestionService.default()
    result = TransportResult(
        url="https://www.instagram.com/matverse/",
        payload={"username": "matverse", "_acquisition_mode": "official_api", "_transport": "meta.instagram.v1"},
        authorized=False,
        transport="meta.instagram.v1",
    )
    with pytest.raises(PermissionError, match="not authorized"):
        service.ingest_transport_result(result)


def test_transport_provenance_mismatch_is_rejected():
    service = SocialIngestionService.default()
    result = TransportResult(
        url="https://www.instagram.com/matverse/",
        payload={"username": "matverse", "_acquisition_mode": "official_api", "_transport": "other.transport"},
        authorized=True,
        transport="meta.instagram.v1",
    )
    with pytest.raises(ValueError, match="provenance mismatch"):
        service.ingest_transport_result(result)
