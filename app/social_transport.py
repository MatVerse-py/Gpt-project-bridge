from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class SocialCapability(str, Enum):
    READ_SELF = "social.instagram.read_self"
    READ_MEDIA = "social.instagram.read_media"
    READ_MEDIA_DETAIL = "social.instagram.read_media_detail"


@dataclass(frozen=True)
class CapabilityPolicy:
    granted: frozenset[SocialCapability] = field(default_factory=frozenset)

    @classmethod
    def from_values(cls, values: list[str] | tuple[str, ...] | set[str] | frozenset[str]) -> "CapabilityPolicy":
        parsed: set[SocialCapability] = set()
        for value in values:
            try:
                parsed.add(SocialCapability(str(value)))
            except ValueError as exc:
                raise ValueError(f"unknown social capability: {value}") from exc
        return cls(frozenset(parsed))

    def require(self, capability: SocialCapability) -> None:
        if capability not in self.granted:
            raise PermissionError(f"capability denied: {capability.value}")


@dataclass(frozen=True)
class TransportResult:
    url: str
    payload: Mapping[str, Any]
    authorized: bool
    transport: str


class MetaInstagramTransport:
    """Minimal authorized transport for the Instagram API.

    The transport owns network access and authorization. Domain normalization
    remains in ``InstagramAdapter``. Access tokens are carried only in the
    Authorization header and are never inserted into returned payloads or URLs.
    """

    name = "meta.instagram.v1"
    _MEDIA_ID = re.compile(r"^[0-9]{1,64}$")
    _DEFAULT_BASE_URL = "https://graph.instagram.com"
    _DEFAULT_TIMEOUT = 15.0
    _MAX_RESPONSE_BYTES = 4 * 1024 * 1024

    def __init__(
        self,
        *,
        access_token: str,
        capabilities: CapabilityPolicy,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        token = access_token.strip()
        if not token:
            raise ValueError("Instagram access token is required")
        normalized_base = base_url.strip().rstrip("/")
        if not normalized_base.startswith("https://"):
            raise ValueError("Instagram API base URL must use HTTPS")
        if timeout <= 0 or timeout > 60:
            raise ValueError("timeout must be in (0, 60]")
        self._access_token = token
        self._capabilities = capabilities
        self._base_url = normalized_base
        self._timeout = float(timeout)

    @classmethod
    def from_env(cls, *, capabilities: CapabilityPolicy) -> "MetaInstagramTransport":
        token = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "").strip()
        if not token:
            raise RuntimeError("INSTAGRAM_ACCESS_TOKEN is not configured")
        base_url = os.environ.get("INSTAGRAM_API_BASE_URL", cls._DEFAULT_BASE_URL)
        timeout_raw = os.environ.get("INSTAGRAM_API_TIMEOUT_SECONDS", str(cls._DEFAULT_TIMEOUT))
        try:
            timeout = float(timeout_raw)
        except ValueError as exc:
            raise RuntimeError("INSTAGRAM_API_TIMEOUT_SECONDS must be numeric") from exc
        return cls(access_token=token, capabilities=capabilities, base_url=base_url, timeout=timeout)

    def _get_json(self, path: str, *, fields: tuple[str, ...]) -> dict[str, Any]:
        if not path.startswith("/") or ".." in path or "?" in path or "#" in path:
            raise ValueError("unsafe Instagram API path")
        query = urlencode({"fields": ",".join(fields)}) if fields else ""
        url = f"{self._base_url}{path}" + (f"?{query}" if query else "")
        request = Request(
            url,
            method="GET",
            headers={
                "Authorization": f"Bearer {self._access_token}",
                "Accept": "application/json",
                "User-Agent": "MatVerse-Governed-Bridge/1.0",
            },
        )
        try:
            with urlopen(request, timeout=self._timeout) as response:  # nosec B310 - fixed HTTPS base enforced above
                raw = response.read(self._MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            raise RuntimeError(f"Instagram API HTTP error: {exc.code}") from exc
        except URLError as exc:
            raise RuntimeError("Instagram API network error") from exc
        if len(raw) > self._MAX_RESPONSE_BYTES:
            raise RuntimeError("Instagram API response exceeds size limit")
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Instagram API returned invalid JSON") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("Instagram API response must be a JSON object")
        if "error" in parsed:
            raise RuntimeError("Instagram API returned an application error")
        return parsed

    @staticmethod
    def _profile_url(username: str | None) -> str:
        if username and username.strip():
            safe = username.strip().lstrip("@").replace("/", "")
            if safe:
                return f"https://www.instagram.com/{safe}/"
        return "https://www.instagram.com/"

    def read_self(self) -> TransportResult:
        self._capabilities.require(SocialCapability.READ_SELF)
        payload = self._get_json("/me", fields=("id", "username", "name", "biography"))
        username = payload.get("username") if isinstance(payload.get("username"), str) else None
        return TransportResult(
            url=self._profile_url(username),
            payload={**payload, "_acquisition_mode": "official_api", "_transport": self.name},
            authorized=True,
            transport=self.name,
        )

    def read_media(self) -> TransportResult:
        self._capabilities.require(SocialCapability.READ_MEDIA)
        payload = self._get_json(
            "/me/media",
            fields=("id", "caption", "media_type", "media_url", "permalink", "thumbnail_url", "timestamp", "username"),
        )
        return TransportResult(
            url="https://www.instagram.com/",
            payload={**payload, "_acquisition_mode": "official_api", "_transport": self.name},
            authorized=True,
            transport=self.name,
        )

    def read_media_detail(self, media_id: str) -> TransportResult:
        self._capabilities.require(SocialCapability.READ_MEDIA_DETAIL)
        value = str(media_id).strip()
        if not self._MEDIA_ID.fullmatch(value):
            raise ValueError("invalid Instagram media id")
        payload = self._get_json(
            f"/{value}",
            fields=("id", "caption", "media_type", "media_url", "permalink", "thumbnail_url", "timestamp", "username", "children"),
        )
        permalink = payload.get("permalink")
        url = permalink if isinstance(permalink, str) and permalink.startswith("https://www.instagram.com/") else "https://www.instagram.com/"
        return TransportResult(
            url=url,
            payload={**payload, "_acquisition_mode": "official_api", "_transport": self.name},
            authorized=True,
            transport=self.name,
        )
