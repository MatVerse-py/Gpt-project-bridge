from __future__ import annotations

from dataclasses import dataclass
import hmac
from typing import Any

import jwt
from fastapi import HTTPException, Request
from jwt import PyJWKClient

from .config import Settings


@dataclass(frozen=True)
class Principal:
    subject: str
    scopes: frozenset[str]
    claims: dict[str, Any]

    def allows(self, required_scope: str) -> bool:
        if required_scope in self.scopes or "*" in self.scopes or "owner:*" in self.scopes:
            return True
        if "." in required_scope and required_scope.split(".", 1)[0] + ".*" in self.scopes:
            return True
        if ":" in required_scope and required_scope.split(":", 1)[0] + ":*" in self.scopes:
            return True
        return False


class Authenticator:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._jwk_client = PyJWKClient(settings.oidc_jwks_url) if settings.auth_mode in {"oidc", "hybrid"} else None

    def challenge(self, scope: str | None = None) -> str:
        metadata = f"{self.settings.public_base_url}/.well-known/oauth-protected-resource"
        required = scope or self.settings.required_scope
        return f'Bearer resource_metadata="{metadata}", scope="{required}"'

    async def authenticate(self, request: Request, *, optional: bool = False) -> Principal | None:
        if self.settings.auth_mode == "disabled":
            return Principal(
                "local-owner",
                frozenset({self.settings.required_scope, self.settings.write_scope, "*"}),
                {},
            )
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bearer "):
            if optional:
                return None
            raise HTTPException(401, "Bearer token required", headers={"WWW-Authenticate": self.challenge()})
        token = authorization[7:].strip()
        if self.settings.auth_mode in {"static", "hybrid"}:
            if self.settings.static_write_token and hmac.compare_digest(token, self.settings.static_write_token):
                return Principal(
                    "static-owner-write",
                    frozenset({self.settings.required_scope, self.settings.write_scope}),
                    {},
                )
            if self.settings.static_token and hmac.compare_digest(token, self.settings.static_token):
                return Principal("static-owner", frozenset({self.settings.required_scope}), {})
            if self.settings.auth_mode == "static":
                raise HTTPException(401, "Invalid bearer token", headers={"WWW-Authenticate": "Bearer"})

        assert self._jwk_client and self.settings.oidc_issuer and self.settings.oidc_audience
        try:
            signing_key = self._jwk_client.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                signing_key,
                algorithms=["RS256", "ES256"],
                audience=self.settings.oidc_audience,
                issuer=self.settings.oidc_issuer,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except Exception as exc:
            raise HTTPException(401, f"Invalid access token: {exc}", headers={"WWW-Authenticate": self.challenge()}) from exc
        raw_scope = claims.get("scope", "")
        principal = Principal(str(claims["sub"]), frozenset(str(raw_scope).split()), claims)
        if not principal.allows(self.settings.required_scope):
            raise HTTPException(403, "Insufficient scope", headers={"WWW-Authenticate": self.challenge()})
        return principal
