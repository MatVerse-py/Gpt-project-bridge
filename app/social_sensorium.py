from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Protocol
from urllib.parse import urlparse

from app.evidence import canonical_json, evidence_receipt, sha256_text


class SocialPlatform(str, Enum):
    INSTAGRAM = "instagram"
    GENERIC_WEB = "generic_web"


class AcquisitionMode(str, Enum):
    PUBLIC = "public"
    OFFICIAL_API = "official_api"
    USER_INGEST = "user_ingest"


class SocialObjectType(str, Enum):
    PROFILE = "profile"
    POST = "post"
    REEL = "reel"
    STORY = "story"
    COMMENT = "comment"
    MESSAGE = "message"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SocialSource:
    platform: SocialPlatform
    canonical_url: str
    acquisition_mode: AcquisitionMode
    adapter: str
    authorized: bool


@dataclass(frozen=True)
class SocialEvidenceObject:
    schema: str
    source: SocialSource
    object_type: SocialObjectType
    external_id: str | None
    author_handle: str | None
    published_at: str | None
    text: str | None
    media_urls: tuple[str, ...]
    attributes: Mapping[str, Any] = field(default_factory=dict)
    acquired_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    payload_hash: str = ""
    receipt: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "source": {
                "platform": self.source.platform.value,
                "canonical_url": self.source.canonical_url,
                "acquisition_mode": self.source.acquisition_mode.value,
                "adapter": self.source.adapter,
                "authorized": self.source.authorized,
            },
            "object_type": self.object_type.value,
            "external_id": self.external_id,
            "author_handle": self.author_handle,
            "published_at": self.published_at,
            "text": self.text,
            "media_urls": list(self.media_urls),
            "attributes": dict(self.attributes),
            "acquired_at": self.acquired_at,
            "payload_hash": self.payload_hash,
            "receipt": dict(self.receipt),
        }


class SocialSourceAdapter(Protocol):
    name: str

    def can_handle(self, url: str) -> bool: ...

    def normalize(self, *, url: str, payload: Mapping[str, Any], authorized: bool) -> SocialEvidenceObject: ...


def _normalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("invalid social URL")
    host = parsed.netloc.lower()
    path = "/" + "/".join(segment for segment in parsed.path.split("/") if segment)
    return f"https://{host}{path}" if path != "/" else f"https://{host}/"


def _first_nonempty(payload: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _media_urls(payload: Mapping[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for key in ("media_url", "thumbnail_url"):
        value = payload.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            values.append(value)
    children = payload.get("children")
    if isinstance(children, Mapping):
        children = children.get("data")
    if isinstance(children, list):
        for child in children:
            if isinstance(child, Mapping):
                value = child.get("media_url")
                if isinstance(value, str) and value.startswith(("http://", "https://")):
                    values.append(value)
    return tuple(dict.fromkeys(values))


class InstagramAdapter:
    name = "instagram.v1"
    _hosts = {"instagram.com", "www.instagram.com"}

    def can_handle(self, url: str) -> bool:
        try:
            return urlparse(_normalize_url(url)).netloc in self._hosts
        except ValueError:
            return False

    @staticmethod
    def classify(url: str, payload: Mapping[str, Any]) -> SocialObjectType:
        path = urlparse(_normalize_url(url)).path.lower()
        media_type = str(payload.get("media_type", "")).upper()
        if "/reel/" in path or media_type == "REELS":
            return SocialObjectType.REEL
        if "/p/" in path or media_type in {"IMAGE", "VIDEO", "CAROUSEL_ALBUM"}:
            return SocialObjectType.POST
        if "/stories/" in path:
            return SocialObjectType.STORY
        if _first_nonempty(payload, "username", "name") and path.count("/") <= 2:
            return SocialObjectType.PROFILE
        return SocialObjectType.UNKNOWN

    def normalize(self, *, url: str, payload: Mapping[str, Any], authorized: bool) -> SocialEvidenceObject:
        if not self.can_handle(url):
            raise ValueError("InstagramAdapter cannot handle URL")
        canonical_url = _normalize_url(url)
        mode_raw = str(payload.get("_acquisition_mode", AcquisitionMode.USER_INGEST.value))
        try:
            mode = AcquisitionMode(mode_raw)
        except ValueError as exc:
            raise ValueError("unsupported acquisition mode") from exc
        if mode is AcquisitionMode.OFFICIAL_API and not authorized:
            raise PermissionError("official API payload requires an authorized acquisition context")

        clean_payload = {str(k): v for k, v in payload.items() if not str(k).startswith("_")}
        payload_hash = sha256_text(canonical_json(clean_payload))
        source = SocialSource(
            platform=SocialPlatform.INSTAGRAM,
            canonical_url=canonical_url,
            acquisition_mode=mode,
            adapter=self.name,
            authorized=authorized,
        )
        core = {
            "source": {
                "platform": source.platform.value,
                "canonical_url": source.canonical_url,
                "acquisition_mode": source.acquisition_mode.value,
                "adapter": source.adapter,
                "authorized": source.authorized,
            },
            "object_type": self.classify(canonical_url, clean_payload).value,
            "external_id": _first_nonempty(clean_payload, "id", "media_id"),
            "author_handle": _first_nonempty(clean_payload, "username", "owner_username", "author"),
            "published_at": _first_nonempty(clean_payload, "timestamp", "published_at", "created_time"),
            "text": _first_nonempty(clean_payload, "caption", "text", "biography"),
            "media_urls": list(_media_urls(clean_payload)),
            "attributes": clean_payload,
            "payload_hash": payload_hash,
        }
        receipt = evidence_receipt("social.ingest", {"url": canonical_url, "payload_hash": payload_hash}, core)
        return SocialEvidenceObject(
            schema="matverse.social-evidence.v1",
            source=source,
            object_type=SocialObjectType(core["object_type"]),
            external_id=core["external_id"],
            author_handle=core["author_handle"],
            published_at=core["published_at"],
            text=core["text"],
            media_urls=tuple(core["media_urls"]),
            attributes=clean_payload,
            payload_hash=payload_hash,
            receipt=receipt,
        )


class SocialRouter:
    def __init__(self, adapters: tuple[SocialSourceAdapter, ...] | None = None) -> None:
        self._adapters = adapters or (InstagramAdapter(),)

    def adapter_for(self, url: str) -> SocialSourceAdapter:
        matches = [adapter for adapter in self._adapters if adapter.can_handle(url)]
        if len(matches) != 1:
            raise ValueError("social source must resolve to exactly one adapter")
        return matches[0]

    def ingest(self, *, url: str, payload: Mapping[str, Any], authorized: bool = False) -> SocialEvidenceObject:
        return self.adapter_for(url).normalize(url=url, payload=payload, authorized=authorized)
