from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Mapping

import httpx

from .core import Decision, evaluate_hdb, omega_gate, stable_hash

OPENAI_BASE_URL = "https://api.openai.com/v1"
OPENAI_RUNTIME_PROTOCOL = "matverse.provider.openai.v1"
_DEFAULT_TIMEOUT_SECONDS = 120.0
_MAX_TIMEOUT_SECONDS = 600.0
_MAX_METADATA_ITEMS = 15  # one slot is reserved for matverse_request_hash
_MAX_METADATA_KEY_LENGTH = 64
_MAX_METADATA_VALUE_LENGTH = 512


class OpenAIConfigurationError(RuntimeError):
    """Raised when the OpenAI runtime is not safely configured."""


class OpenAIProviderError(RuntimeError):
    """Sanitized provider failure; never includes credentials or request payloads."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        request_id: str | None = None,
        provider_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.request_id = request_id
        self.provider_code = provider_code


@dataclass(frozen=True)
class OpenAIRuntimeConfig:
    api_key: str = field(repr=False)
    model: str
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS
    max_output_tokens: int | None = None

    @classmethod
    def from_env(cls) -> "OpenAIRuntimeConfig":
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        model = os.environ.get("OPENAI_MODEL", "").strip()
        if not api_key:
            raise OpenAIConfigurationError("OPENAI_API_KEY is not configured")
        if any(ch.isspace() for ch in api_key):
            raise OpenAIConfigurationError("OPENAI_API_KEY contains whitespace")
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

        return cls(
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
            max_output_tokens=max_output_tokens,
        )


@dataclass(frozen=True)
class OpenAIResponseResult:
    response_id: str
    model: str
    output_text: str
    usage: dict[str, Any]
    request_hash: str
    response_hash: str
    provider_request_id: str | None

    def public_dict(self) -> dict[str, Any]:
        return {
            "protocol": OPENAI_RUNTIME_PROTOCOL,
            "response_id": self.response_id,
            "model": self.model,
            "output_text": self.output_text,
            "usage": self.usage,
            "request_hash": self.request_hash,
            "response_hash": self.response_hash,
            "provider_request_id": self.provider_request_id,
        }


def runtime_status_from_env() -> dict[str, Any]:
    api_key_present = bool(os.environ.get("OPENAI_API_KEY", "").strip())
    model = os.environ.get("OPENAI_MODEL", "").strip()
    return {
        "protocol": OPENAI_RUNTIME_PROTOCOL,
        "provider": "openai",
        "base_url": OPENAI_BASE_URL,
        "api_key_present": api_key_present,
        "model": model or None,
        "configured": api_key_present and bool(model),
        "store": False,
        "secret_exposure": "forbidden",
    }


def _validate_metadata(metadata: Mapping[str, str] | None) -> dict[str, str]:
    if metadata is None:
        return {}
    if len(metadata) > _MAX_METADATA_ITEMS:
        raise ValueError(f"metadata supports at most {_MAX_METADATA_ITEMS} user entries")
    validated: dict[str, str] = {}
    for key, value in metadata.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("metadata keys and values must be strings")
        if not key or len(key) > _MAX_METADATA_KEY_LENGTH:
            raise ValueError(f"metadata keys must be 1..{_MAX_METADATA_KEY_LENGTH} characters")
        if len(value) > _MAX_METADATA_VALUE_LENGTH:
            raise ValueError(f"metadata values must be <= {_MAX_METADATA_VALUE_LENGTH} characters")
        if key == "matverse_request_hash":
            raise ValueError("matverse_request_hash is reserved")
        validated[key] = value
    return validated


def _extract_output_text(payload: Mapping[str, Any]) -> str:
    parts: list[str] = []
    output = payload.get("output", [])
    if not isinstance(output, list):
        raise OpenAIProviderError("OpenAI response output has an invalid shape")
    for item in output:
        if not isinstance(item, Mapping) or item.get("type") != "message":
            continue
        content = item.get("content", [])
        if not isinstance(content, list):
            continue
        for content_item in content:
            if not isinstance(content_item, Mapping) or content_item.get("type") != "output_text":
                continue
            text = content_item.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


class OpenAIResponsesRuntime:
    """OpenAI Responses API adapter with a MatVerse governance boundary.

    The API key is read only from the process environment. It is never persisted,
    returned, hashed into receipts, added to metadata, or transferred through the
    Model Bridge.
    """

    def __init__(
        self,
        config: OpenAIRuntimeConfig,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config
        self._transport = transport

    @classmethod
    def from_env(cls) -> "OpenAIResponsesRuntime":
        return cls(OpenAIRuntimeConfig.from_env())

    def _build_request(
        self,
        *,
        input_text: str,
        instructions: str | None,
        metadata: Mapping[str, str] | None,
    ) -> tuple[dict[str, Any], str]:
        if not isinstance(input_text, str) or not input_text.strip():
            raise ValueError("input_text must be non-empty")
        if instructions is not None and not isinstance(instructions, str):
            raise ValueError("instructions must be a string or null")

        user_metadata = _validate_metadata(metadata)
        request_descriptor = {
            "protocol": OPENAI_RUNTIME_PROTOCOL,
            "model": self.config.model,
            "input": input_text,
            "instructions": instructions,
            "metadata": user_metadata,
            "store": False,
            "max_output_tokens": self.config.max_output_tokens,
        }
        request_hash = stable_hash(request_descriptor)

        body: dict[str, Any] = {
            "model": self.config.model,
            "input": input_text,
            "store": False,
            "metadata": {**user_metadata, "matverse_request_hash": request_hash},
        }
        if instructions:
            body["instructions"] = instructions
        if self.config.max_output_tokens is not None:
            body["max_output_tokens"] = self.config.max_output_tokens
        return body, request_hash

    def invoke(
        self,
        *,
        input_text: str,
        instructions: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> OpenAIResponseResult:
        body, request_hash = self._build_request(
            input_text=input_text,
            instructions=instructions,
            metadata=metadata,
        )
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "MatVerse-OpenAI-Runtime/1.0",
        }
        try:
            with httpx.Client(
                base_url=OPENAI_BASE_URL,
                headers=headers,
                timeout=self.config.timeout_seconds,
                transport=self._transport,
                follow_redirects=False,
            ) as client:
                response = client.post("/responses", json=body)
        except httpx.HTTPError as exc:
            raise OpenAIProviderError("OpenAI request failed before a valid response was received") from exc

        provider_request_id = response.headers.get("x-request-id") or response.headers.get("request-id")
        if response.status_code < 200 or response.status_code >= 300:
            provider_code: str | None = None
            try:
                error_payload = response.json()
                error_obj = error_payload.get("error") if isinstance(error_payload, Mapping) else None
                if isinstance(error_obj, Mapping) and isinstance(error_obj.get("code"), str):
                    provider_code = error_obj["code"]
            except ValueError:
                provider_code = None
            raise OpenAIProviderError(
                "OpenAI returned a non-success status",
                status_code=response.status_code,
                request_id=provider_request_id,
                provider_code=provider_code,
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise OpenAIProviderError(
                "OpenAI returned non-JSON content",
                status_code=response.status_code,
                request_id=provider_request_id,
            ) from exc
        if not isinstance(payload, Mapping):
            raise OpenAIProviderError("OpenAI response must be a JSON object", request_id=provider_request_id)

        response_id = payload.get("id")
        model = payload.get("model")
        if not isinstance(response_id, str) or not response_id:
            raise OpenAIProviderError("OpenAI response is missing an id", request_id=provider_request_id)
        if not isinstance(model, str) or not model:
            raise OpenAIProviderError("OpenAI response is missing a model", request_id=provider_request_id)

        output_text = _extract_output_text(payload)
        usage_raw = payload.get("usage")
        usage = dict(usage_raw) if isinstance(usage_raw, Mapping) else {}
        response_hash = stable_hash(
            {
                "protocol": OPENAI_RUNTIME_PROTOCOL,
                "response_id": response_id,
                "model": model,
                "output_text": output_text,
                "usage": usage,
            }
        )
        return OpenAIResponseResult(
            response_id=response_id,
            model=model,
            output_text=output_text,
            usage=usage,
            request_hash=request_hash,
            response_hash=response_hash,
            provider_request_id=provider_request_id,
        )

    def governed_invoke(
        self,
        *,
        input_text: str,
        instructions: str | None = None,
        metadata: Mapping[str, str] | None = None,
        human: dict[str, Any] | None = None,
        ontology_ok: bool = True,
        signature_valid: bool = True,
        transition_valid: bool = True,
    ) -> dict[str, Any]:
        # Compute the descriptor hash before the network boundary so blocked
        # attempts are still auditable without exposing the prompt itself.
        _, request_hash = self._build_request(
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
        if decision is not Decision.PASS:
            return {
                "protocol": OPENAI_RUNTIME_PROTOCOL,
                "decision": decision.value,
                "reason": reason,
                "executed": False,
                "provider": "openai",
                "model": self.config.model,
                "request_hash": request_hash,
            }

        result = self.invoke(
            input_text=input_text,
            instructions=instructions,
            metadata=metadata,
        )
        return {
            "decision": Decision.PASS.value,
            "reason": reason,
            "executed": True,
            "provider": "openai",
            **result.public_dict(),
        }
