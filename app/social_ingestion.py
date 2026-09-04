from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.social_sensorium import SocialEvidenceObject, SocialRouter
from app.social_transport import TransportResult


class SocialTransport(Protocol):
    name: str


@dataclass(frozen=True)
class SocialIngestionService:
    router: SocialRouter

    @classmethod
    def default(cls) -> "SocialIngestionService":
        return cls(router=SocialRouter())

    def ingest_transport_result(self, result: TransportResult) -> SocialEvidenceObject:
        if result.authorized is not True:
            raise PermissionError("transport result is not authorized")
        if not result.transport.strip():
            raise ValueError("transport identity is required")
        payload = dict(result.payload)
        if payload.get("_acquisition_mode") != "official_api":
            raise ValueError("authorized transport result must declare official_api acquisition")
        declared_transport = payload.get("_transport")
        if declared_transport != result.transport:
            raise ValueError("transport provenance mismatch")
        return self.router.ingest(url=result.url, payload=payload, authorized=True)
