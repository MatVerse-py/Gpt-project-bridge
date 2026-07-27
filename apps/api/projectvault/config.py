from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


def _csv(name: str, default: str = "") -> tuple[str, ...]:
    raw = os.getenv(name, default)
    return tuple(part.strip() for part in raw.split(",") if part.strip())


@dataclass(frozen=True)
class Settings:
    database_path: Path
    host: str
    port: int
    public_base_url: str
    auth_mode: str
    static_token: str | None
    oidc_issuer: str | None
    oidc_audience: str | None
    oidc_jwks_url: str | None
    required_scope: str
    allowed_origins: tuple[str, ...]
    max_results: int

    @classmethod
    def from_env(cls) -> "Settings":
        settings = cls(
            database_path=Path(os.getenv("PROJECTVAULT_DB", "data/projectvault.db")).resolve(),
            host=os.getenv("PROJECTVAULT_HOST", "127.0.0.1"),
            port=int(os.getenv("PROJECTVAULT_PORT", "8787")),
            public_base_url=os.getenv("PROJECTVAULT_PUBLIC_BASE_URL", "http://127.0.0.1:8787").rstrip("/"),
            auth_mode=os.getenv("PROJECTVAULT_AUTH_MODE", "disabled").lower(),
            static_token=os.getenv("PROJECTVAULT_STATIC_TOKEN"),
            oidc_issuer=os.getenv("PROJECTVAULT_OIDC_ISSUER"),
            oidc_audience=os.getenv("PROJECTVAULT_OIDC_AUDIENCE"),
            oidc_jwks_url=os.getenv("PROJECTVAULT_OIDC_JWKS_URL"),
            required_scope=os.getenv("PROJECTVAULT_REQUIRED_SCOPE", "projects.read"),
            allowed_origins=_csv("PROJECTVAULT_ALLOWED_ORIGINS", "https://chatgpt.com"),
            max_results=max(1, min(100, int(os.getenv("PROJECTVAULT_MAX_RESULTS", "20")))),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.auth_mode not in {"disabled", "static", "oidc", "hybrid"}:
            raise ValueError("PROJECTVAULT_AUTH_MODE must be disabled, static, oidc, or hybrid")
        if self.auth_mode == "disabled" and self.host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("AUTH_MODE=disabled is allowed only on a loopback host")
        if self.auth_mode in {"static", "hybrid"} and not self.static_token:
            raise ValueError("PROJECTVAULT_STATIC_TOKEN is required for static auth")
        if self.auth_mode in {"oidc", "hybrid"}:
            missing = [
                name
                for name, value in {
                    "PROJECTVAULT_OIDC_ISSUER": self.oidc_issuer,
                    "PROJECTVAULT_OIDC_AUDIENCE": self.oidc_audience,
                    "PROJECTVAULT_OIDC_JWKS_URL": self.oidc_jwks_url,
                }.items()
                if not value
            ]
            if missing:
                raise ValueError(f"Missing OIDC settings: {', '.join(missing)}")
            if urlparse(self.public_base_url).scheme != "https":
                raise ValueError("OIDC mode requires an HTTPS PROJECTVAULT_PUBLIC_BASE_URL")
