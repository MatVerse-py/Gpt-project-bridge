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


class Authenticator:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._jwk_client = PyJWKClient(settings.oidc_jwks_url) if settings.auth_mode in {"oidc", "hybrid"} else None

    def challenge(self) -> str:
        metadata = f"{self.settings.public_base_url}/.well-known/oauth-protected-resource"
        return f'Bearer resource_metadata="{metadata}", scope="{self.settings.required_scope}"'

    async def authenticate(self, request: Request, *, optional: bool = False) -> Principal | None:
        if self.settings.auth_mode == "disabled":
            return Principal("local-owner", frozenset({self.settings.required_scope}), {})
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bearer "):
            if optional:
                return None
            raise HTTPException(401, "Bearer token required", headers={"WWW-Authenticate": self.challenge()})
        token = authorization[7:].strip()
        if self.settings.auth_mode in {"static", "hybrid"} and self.settings.static_token:
            if hmac.compare_digest(token, self.settings.static_token):
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
        scopes = frozenset(str(raw_scope).split())
        if self.settings.required_scope not in scopes:
            raise HTTPException(403, "Insufficient scope", headers={"WWW-Authenticate": self.challenge()})
        return Principal(str(claims["sub"]), scopes, claims)
