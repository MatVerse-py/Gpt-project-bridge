from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError

import pytest

from app.social_transport import CapabilityPolicy, MetaInstagramTransport, SocialCapability


class _Response:
    def __init__(self, payload: dict):
        self._buffer = BytesIO(json.dumps(payload).encode("utf-8"))

    def read(self, size: int = -1) -> bytes:
        return self._buffer.read(size)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_capability_policy_fails_closed():
    policy = CapabilityPolicy.from_values([])
    with pytest.raises(PermissionError, match="capability denied"):
        policy.require(SocialCapability.READ_SELF)


def test_unknown_capability_is_rejected():
    with pytest.raises(ValueError, match="unknown social capability"):
        CapabilityPolicy.from_values(["social.instagram.root"])


def test_transport_rejects_missing_token_and_insecure_base_url():
    policy = CapabilityPolicy.from_values([SocialCapability.READ_SELF.value])
    with pytest.raises(ValueError, match="access token"):
        MetaInstagramTransport(access_token=" ", capabilities=policy)
    with pytest.raises(ValueError, match="HTTPS"):
        MetaInstagramTransport(access_token="token", capabilities=policy, base_url="http://graph.instagram.com")


def test_read_self_uses_bearer_header_and_never_returns_token():
    policy = CapabilityPolicy.from_values([SocialCapability.READ_SELF.value])
    transport = MetaInstagramTransport(access_token="secret-token", capabilities=policy)
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["auth"] = request.get_header("Authorization")
        captured["timeout"] = timeout
        return _Response({"id": "42", "username": "matverse", "name": "MatVerse"})

    with patch("app.social_transport.urlopen", fake_urlopen):
        result = transport.read_self()

    assert captured["auth"] == "Bearer secret-token"
    assert "secret-token" not in captured["url"]
    assert result.authorized is True
    assert result.payload["_acquisition_mode"] == "official_api"
    assert "secret-token" not in json.dumps(dict(result.payload))
    assert result.url == "https://www.instagram.com/matverse/"


def test_read_media_requires_explicit_capability_before_network():
    transport = MetaInstagramTransport(access_token="token", capabilities=CapabilityPolicy())
    with patch("app.social_transport.urlopen") as opener:
        with pytest.raises(PermissionError):
            transport.read_media()
    opener.assert_not_called()


def test_media_detail_rejects_path_injection_before_network():
    policy = CapabilityPolicy.from_values([SocialCapability.READ_MEDIA_DETAIL.value])
    transport = MetaInstagramTransport(access_token="token", capabilities=policy)
    with patch("app.social_transport.urlopen") as opener:
        with pytest.raises(ValueError, match="media id"):
            transport.read_media_detail("123/../../me")
    opener.assert_not_called()


def test_media_detail_returns_permalink_when_valid():
    policy = CapabilityPolicy.from_values([SocialCapability.READ_MEDIA_DETAIL.value])
    transport = MetaInstagramTransport(access_token="token", capabilities=policy)

    def fake_urlopen(request, timeout):
        assert request.full_url.startswith("https://graph.instagram.com/123?")
        return _Response({
            "id": "123",
            "media_type": "IMAGE",
            "permalink": "https://www.instagram.com/p/ABC123/",
            "username": "matverse",
        })

    with patch("app.social_transport.urlopen", fake_urlopen):
        result = transport.read_media_detail("123")

    assert result.url == "https://www.instagram.com/p/ABC123/"
    assert result.payload["id"] == "123"


def test_api_http_error_is_sanitized():
    policy = CapabilityPolicy.from_values([SocialCapability.READ_SELF.value])
    transport = MetaInstagramTransport(access_token="token", capabilities=policy)

    def fake_urlopen(request, timeout):
        raise HTTPError(request.full_url, 401, "Unauthorized secret-token", hdrs=None, fp=None)

    with patch("app.social_transport.urlopen", fake_urlopen):
        with pytest.raises(RuntimeError) as exc:
            transport.read_self()

    assert str(exc.value) == "Instagram API HTTP error: 401"
    assert "secret-token" not in str(exc.value)
