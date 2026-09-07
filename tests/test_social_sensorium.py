from __future__ import annotations

import pytest

from app.social_sensorium import (
    AcquisitionMode,
    InstagramAdapter,
    SocialObjectType,
    SocialRouter,
)


def test_instagram_profile_user_ingest_normalizes_and_receipts() -> None:
    router = SocialRouter()
    obj = router.ingest(
        url="https://www.instagram.com/dr.fabiojbio?igsi=tracking",
        payload={
            "username": "dr.fabiojbio",
            "biography": "Bio pública fornecida pelo usuário",
            "_acquisition_mode": AcquisitionMode.USER_INGEST.value,
        },
        authorized=True,
    )

    assert obj.object_type is SocialObjectType.PROFILE
    assert obj.source.canonical_url == "https://www.instagram.com/dr.fabiojbio"
    assert obj.source.authorized is True
    assert obj.author_handle == "dr.fabiojbio"
    assert obj.payload_hash
    assert obj.receipt["schema"] == "matverse.evidence-receipt.v1"
    assert obj.receipt["event_type"] == "social.ingest"


def test_instagram_reel_classification_and_media_deduplication() -> None:
    obj = InstagramAdapter().normalize(
        url="https://instagram.com/reel/ABC123/",
        payload={
            "id": "ABC123",
            "caption": "Teste",
            "media_url": "https://cdn.example/video.mp4",
            "thumbnail_url": "https://cdn.example/thumb.jpg",
            "children": {"data": [{"media_url": "https://cdn.example/video.mp4"}]},
            "_acquisition_mode": "public",
        },
        authorized=False,
    )

    assert obj.object_type is SocialObjectType.REEL
    assert obj.media_urls == (
        "https://cdn.example/video.mp4",
        "https://cdn.example/thumb.jpg",
    )


def test_official_api_mode_fails_closed_without_authorization() -> None:
    with pytest.raises(PermissionError):
        InstagramAdapter().normalize(
            url="https://instagram.com/p/ABC123/",
            payload={"id": "ABC123", "_acquisition_mode": "official_api"},
            authorized=False,
        )


def test_tracking_query_is_not_part_of_canonical_identity() -> None:
    adapter = InstagramAdapter()
    a = adapter.normalize(
        url="https://www.instagram.com/p/ABC123/?igsh=tracking-a",
        payload={"id": "ABC123", "_acquisition_mode": "user_ingest"},
        authorized=True,
    )
    b = adapter.normalize(
        url="https://www.instagram.com/p/ABC123/?utm_source=x",
        payload={"id": "ABC123", "_acquisition_mode": "user_ingest"},
        authorized=True,
    )

    assert a.source.canonical_url == b.source.canonical_url
    assert a.payload_hash == b.payload_hash


def test_unknown_hosts_fail_closed() -> None:
    router = SocialRouter()
    with pytest.raises(ValueError):
        router.ingest(url="https://example.com/x", payload={}, authorized=False)


def test_private_control_fields_are_excluded_from_payload_hash() -> None:
    adapter = InstagramAdapter()
    first = adapter.normalize(
        url="https://instagram.com/p/ABC123/",
        payload={"id": "ABC123", "caption": "x", "_acquisition_mode": "user_ingest", "_token": "secret-a"},
        authorized=True,
    )
    second = adapter.normalize(
        url="https://instagram.com/p/ABC123/",
        payload={"id": "ABC123", "caption": "x", "_acquisition_mode": "user_ingest", "_token": "secret-b"},
        authorized=True,
    )

    assert first.payload_hash == second.payload_hash
    assert "_token" not in first.attributes
