from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


def _csv(name: str, default: str = "") -> tuple[str, ...]:
    raw = os.getenv(name, default)
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _positive_int(name: str, default: int, *, maximum: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not 1 <= value <= maximum:
        raise ValueError(f"{name} must be between 1 and {maximum}")
    return value


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
    max_upload_bytes: int = 6 * 1024 * 1024 * 1024
    max_archive_files: int = 50_000
    max_archive_expanded_bytes: int = 32 * 1024 * 1024 * 1024
    max_archive_member_bytes: int = 1024 * 1024 * 1024
    max_indexable_file_bytes: int = 20 * 1024 * 1024
    min_free_bytes: int = 1024 * 1024 * 1024
    staging_path: Path | None = None

    @property
    def staging_dir(self) -> Path:
        return (self.staging_path or self.database_path.parent / "staging").resolve()

    @classmethod
    def from_env(cls) -> "Settings":
        database_path = Path(os.getenv("PROJECTVAULT_DB", "data/projectvault.db")).resolve()
        settings = cls(
            database_path=database_path,
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
            max_upload_bytes=_positive_int(
                "PROJECTVAULT_MAX_UPLOAD_BYTES", 6 * 1024 * 1024 * 1024, maximum=64 * 1024 * 1024 * 1024
            ),
            max_archive_files=_positive_int("PROJECTVAULT_MAX_ARCHIVE_FILES", 50_000, maximum=1_000_000),
            max_archive_expanded_bytes=_positive_int(
                "PROJECTVAULT_MAX_ARCHIVE_EXPANDED_BYTES", 32 * 1024 * 1024 * 1024, maximum=512 * 1024 * 1024 * 1024
            ),
            max_archive_member_bytes=_positive_int(
                "PROJECTVAULT_MAX_ARCHIVE_MEMBER_BYTES", 1024 * 1024 * 1024, maximum=64 * 1024 * 1024 * 1024
            ),
            max_indexable_file_bytes=_positive_int(
                "PROJECTVAULT_MAX_INDEXABLE_FILE_BYTES", 20 * 1024 * 1024, maximum=4 * 1024 * 1024 * 1024
            ),
            min_free_bytes=_positive_int(
                "PROJECTVAULT_MIN_FREE_BYTES", 1024 * 1024 * 1024, maximum=64 * 1024 * 1024 * 1024
            ),
            staging_path=Path(os.getenv("PROJECTVAULT_STAGING_DIR", str(database_path.parent / "staging"))),
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
        if self.max_archive_member_bytes > self.max_archive_expanded_bytes:
            raise ValueError("PROJECTVAULT_MAX_ARCHIVE_MEMBER_BYTES cannot exceed PROJECTVAULT_MAX_ARCHIVE_EXPANDED_BYTES")
        if self.max_indexable_file_bytes > self.max_archive_member_bytes:
            raise ValueError("PROJECTVAULT_MAX_INDEXABLE_FILE_BYTES cannot exceed PROJECTVAULT_MAX_ARCHIVE_MEMBER_BYTES")
        if self.staging_dir.exists() and not self.staging_dir.is_dir():
            raise ValueError("PROJECTVAULT_STAGING_DIR must be a directory")
