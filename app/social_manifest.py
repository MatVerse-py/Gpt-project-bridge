from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.social_transport import SocialCapability


@dataclass(frozen=True)
class SourceCapabilityManifest:
    schema: str
    source_id: str
    provider: str
    account_id: str
    capabilities: frozenset[SocialCapability]
    credential_ref: str

    @classmethod
    def build(
        cls,
        *,
        source_id: str,
        provider: str,
        account_id: str,
        capabilities: Iterable[SocialCapability],
        credential_ref: str,
    ) -> "SourceCapabilityManifest":
        if not source_id.strip() or not provider.strip() or not account_id.strip():
            raise ValueError("source identity fields are required")
        if not credential_ref.strip():
            raise ValueError("credential_ref is required")
        granted = frozenset(capabilities)
        if not granted:
            raise ValueError("at least one capability is required")
        return cls(
            schema="matverse.source-capability-manifest.v1",
            source_id=source_id.strip(),
            provider=provider.strip(),
            account_id=account_id.strip(),
            capabilities=granted,
            credential_ref=credential_ref.strip(),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "source_id": self.source_id,
            "provider": self.provider,
            "account_id": self.account_id,
            "capabilities": sorted(cap.value for cap in self.capabilities),
            "credential_ref": self.credential_ref,
        }
