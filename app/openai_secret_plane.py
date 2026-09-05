from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Mapping

import httpx

from .core import Decision, evaluate_hdb, omega_gate
from .openai_runtime import (
    OPENAI_RUNTIME_PROTOCOL,
    OpenAIConfigurationError,
    OpenAIResponsesRuntime,
    OpenAIRuntimeConfig,
)
from .secret_plane import (
    EnvironmentSecretVault,
    KeyAuthority,
    SecretDescriptor,
    SecretPlane,
    SecretPolicy,
    StorageClass,
)

OPENAI_SECRET_PLANE_PROTOCOL = "matverse.provider.openai.secret-plane.v1"
OPENAI_SECRET_ID = "provider.openai.api_key"
OPENAI_SECRET_CAPABILITY = "openai.responses"
OPENAI_SECRET_SCOPE = "provider:openai:invoke"
OPENAI_SECRET_LOCATOR = "OPENAI_API_KEY"
_DEFAULT_TIMEOUT_SECONDS = 120.0
_MAX_TIMEOUT_SECONDS = 600.0


@dataclass(frozen=True)
class OpenAIPublicRuntimeSettings:
    model: str
    timeout_seconds: float
    max_output_tokens: int | None
    secret_version: int

    @classmethod
    def from_env(cls) -> "OpenAIPublicRuntimeSettings":
        model = os.environ.get("OPENAI_MODEL", "").strip()
        if not model:
            raise OpenAIConfigurationError("OPENAI_MODEL is not configured")

        timeout_raw = os.environ.get("OPENAI_TIMEOUT_SECONDS", str(_DEFAULT_TIMEOUT_SECONDS)).strip()
        try:
            timeout_seconds = float(timeout_raw)
        except ValueError as exc:
            raise OpenAIConfigurationError("OPENAI_TIMEOUT_SECONDS must be numeric") from exc
        if not 1.0 <= timeout_seconds <= _MAX_TIMEOUT_SECONDS:
            raise OpenAIConfigurationError(
                f"OPENAI_TIMEOUT_SECONDS must be between 1 and {_MAX_TIMEOUT_SECONDS:g}"
            )

        max_tokens_raw = os.environ.get("OPENAI_MAX_OUTPUT_TOKENS", "").strip()
        max_output_tokens: int | None = None
        if max_tokens_raw:
            try:
                max_output_tokens = int(max_tokens_raw)
            except ValueError as exc:
                raise OpenAIConfigurationError("OPENAI_MAX_OUTPUT_TOKENS must be an integer") from exc
            if max_output_tokens <= 0:
                raise OpenAIConfigurationError("OPENAI_MAX_OUTPUT_TOKENS must be positive")

        version_raw = os.environ.get("OPENAI_SECRET_VERSION", "1").strip()
        try:
            secret_version = int(version_raw)
        except ValueError as exc:
            raise OpenAIConfigurationError("OPENAI_SECRET_VERSION must be an integer") from exc
        if secret_version < 1:
            raise OpenAIConfigurationError("OPENAI_SECRET_VERSION must be >= 1")

        return cls(
            model=model,
            timeout_seconds=timeout_seconds,
            max_output_tokens=max_output_tokens,
            secret_version=secret_version,
        )


def secret_plane_status_from_env() -> dict[str, Any]:
    model = os.environ.get("OPENAI_MODEL", "").strip()
    credential_present = bool(os.environ.get(OPENAI_SECRET_LOCATOR, "").strip())
    version_raw = os.environ.get("OPENAI_SECRET_VERSION", "1").strip()
    try:
        secret_version: int | None = int(version_raw)
        if secret_version < 1:
            secret_version = None
    except ValueError:
        secret_version = None
    return {
        "protocol": OPENAI_RUNTIME_PROTOCOL,
        "secret_plane_protocol": OPENAI_SECRET_PLANE_PROTOCOL,
        "provider": "openai",
        "credential_mode": "secret_plane",
        "storage_class": StorageClass.ENVIRONMENT.value,
        "credential_present": credential_present,
        "model": model or None,
        "secret_version": secret_version,
        "configured": credential_present and bool(model) and secret_version is not None,
        "store": False,
        "secret_exposure": "forbidden",
        "direct_provider_route_reads_api_key": False,
    }


class OpenAISecretPlaneBroker:
    """Binds one OpenAI provider invocation to a one-use Secret Plane lease.

    The public provider route never reads the API-key value. The environment is
    treated only as a vault adapter/secret injection surface. Disclosure happens
    after MatVerse provider-exposure governance passes and is bounded to the
    requesting principal, capability, scope, TTL and one use.
    """

    def __init__(
        self,
        *,
        settings: OpenAIPublicRuntimeSettings,
        lease_signing_key: bytes,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self._transport = transport
        self._plane = SecretPlane(
            vault=EnvironmentSecretVault(),
            lease_signing_key=lease_signing_key,
        )
        descriptor = SecretDescriptor(
            secret_id=OPENAI_SECRET_ID,
            kind="provider_api_key",
            owner="matverse",
            purpose="governed OpenAI Responses invocation",
            provider="openai",
            storage_class=StorageClass.ENVIRONMENT,
            version=settings.secret_version,
            created_at=int(time.time()),
        )
        policy = SecretPolicy(
            allowed_actors=("*",),
            allowed_capabilities=(OPENAI_SECRET_CAPABILITY,),
            allowed_scopes=(OPENAI_SECRET_SCOPE,),
            max_ttl_seconds=30,
            max_uses=1,
        )
        self._plane.register_secret(descriptor, policy=policy, locator=OPENAI_SECRET_LOCATOR)

    @classmethod
    def from_env(
        cls,
        *,
        lease_signing_key: bytes | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> "OpenAISecretPlaneBroker":
        settings = OpenAIPublicRuntimeSettings.from_env()
        return cls(
            settings=settings,
            lease_signing_key=lease_signing_key or KeyAuthority.generate(),
            transport=transport,
        )

    def _preflight(
        self,
        *,
        input_text: str,
        instructions: str | None,
        metadata: Mapping[str, str] | None,
        human: dict[str, Any] | None,
        ontology_ok: bool,
        signature_valid: bool,
        transition_valid: bool,
    ) -> tuple[str, Decision, str]:
        # Build the canonical request descriptor without touching the credential.
        probe = OpenAIResponsesRuntime(
            OpenAIRuntimeConfig(
                api_key="not-a-secret-probe",
                model=self.settings.model,
                timeout_seconds=self.settings.timeout_seconds,
                max_output_tokens=self.settings.max_output_tokens,
            )
        )
        _, request_hash = probe._build_request(
            input_text=input_text,
            instructions=instructions,
            metadata=metadata,
        )
        hdb = evaluate_hdb(human)
        decision, reason = omega_gate(
            hdb=hdb,
            action="PROVIDER_EXPOSURE",
            ontology_ok=ontology_ok,
            signature_valid=signature_valid,
            transition_valid=transition_valid,
        )
        return request_hash, decision, reason

    def governed_invoke(
        self,
        *,
        actor: str,
        input_text: str,
        instructions: str | None = None,
        metadata: Mapping[str, str] | None = None,
        human: dict[str, Any] | None = None,
        ontology_ok: bool = True,
        signature_valid: bool = True,
        transition_valid: bool = True,
    ) -> dict[str, Any]:
        request_hash, decision, reason = self._preflight(
            input_text=input_text,
            instructions=instructions,
            metadata=metadata,
            human=human,
            ontology_ok=ontology_ok,
            signature_valid=signature_valid,
            transition_valid=transition_valid,
        )
        base = {
            "protocol": OPENAI_RUNTIME_PROTOCOL,
            "secret_plane_protocol": OPENAI_SECRET_PLANE_PROTOCOL,
            "credential_mode": "secret_plane",
            "provider": "openai",
            "model": self.settings.model,
            "request_hash": request_hash,
            "secret_id": OPENAI_SECRET_ID,
            "secret_version": self.settings.secret_version,
        }
        if decision is not Decision.PASS:
            return {
                **base,
                "decision": decision.value,
                "reason": reason,
                "executed": False,
                "secret_disclosed": False,
            }

        lease = self._plane.issue_lease(
            secret_id=OPENAI_SECRET_ID,
            actor=actor,
            capability=OPENAI_SECRET_CAPABILITY,
            scope=OPENAI_SECRET_SCOPE,
            ttl_seconds=30,
            max_uses=1,
        )

        def invoke(secret_view: memoryview) -> dict[str, Any]:
            api_key = bytes(secret_view).decode("utf-8")
            if not api_key.strip() or any(ch.isspace() for ch in api_key):
                raise OpenAIConfigurationError("OpenAI credential is malformed")
            runtime = OpenAIResponsesRuntime(
                OpenAIRuntimeConfig(
                    api_key=api_key,
                    model=self.settings.model,
                    timeout_seconds=self.settings.timeout_seconds,
                    max_output_tokens=self.settings.max_output_tokens,
                ),
                transport=self._transport,
            )
            result = runtime.invoke(
                input_text=input_text,
                instructions=instructions,
                metadata=metadata,
            )
            return result.public_dict()

        provider_result = self._plane.execute_with_secret(lease, invoke)
        return {
            **base,
            "decision": Decision.PASS.value,
            "reason": reason,
            "executed": True,
            "secret_disclosed": True,
            "lease_id": lease.lease_id,
            **provider_result,
        }

    def public_audit(self) -> tuple[dict[str, Any], ...]:
        return self._plane.audit_events()
