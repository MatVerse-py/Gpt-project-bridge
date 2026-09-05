from __future__ import annotations

import json

import httpx

from app.openai_runtime import OpenAIRuntimeConfig, OpenAIResponsesRuntime
from app.secret_plane import (
    InMemorySecretVault,
    SecretDescriptor,
    SecretPlane,
    SecretPolicy,
    StorageClass,
)


def test_secret_plane_leases_openai_credential_without_persisting_it():
    raw_secret = "sk-test-secret-plane-never-persist-this-value"
    vault = InMemorySecretVault({"openai-slot": raw_secret})
    plane = SecretPlane(
        vault=vault,
        lease_signing_key=b"L" * 32,
        clock=lambda: 1_000.0,
        nonce_factory=lambda: "0123456789abcdef0123456789abcdef",
    )
    descriptor = SecretDescriptor(
        secret_id="openai-test",
        kind="provider_api_key",
        owner="matverse",
        purpose="governed OpenAI test invocation",
        provider="openai",
        storage_class=StorageClass.TEST_MEMORY,
        version=1,
        created_at=900,
    )
    policy = SecretPolicy(
        allowed_actors=("executor-a",),
        allowed_capabilities=("openai.responses",),
        allowed_scopes=("secret-plane-openai-test",),
        max_ttl_seconds=60,
        max_uses=1,
    )
    plane.register_secret(descriptor, policy=policy, locator="openai-slot")
    lease = plane.issue_lease(
        secret_id="openai-test",
        actor="executor-a",
        capability="openai.responses",
        scope="secret-plane-openai-test",
        ttl_seconds=30,
        max_uses=1,
    )

    observed_authorization = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed_authorization["value"] = request.headers.get("Authorization")
        return httpx.Response(
            200,
            headers={"x-request-id": "req_secret_plane_test"},
            json={
                "id": "resp_secret_plane_test",
                "model": "gpt-test",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "MATVERSE_SECRET_PLANE_PASS"}],
                    }
                ],
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            },
        )

    def invoke(secret_view: memoryview):
        config = OpenAIRuntimeConfig(
            api_key=bytes(secret_view).decode("utf-8"),
            model="gpt-test",
            timeout_seconds=5,
            max_output_tokens=8,
        )
        runtime = OpenAIResponsesRuntime(config, transport=httpx.MockTransport(handler))
        return runtime.governed_invoke(
            input_text="Return exactly MATVERSE_SECRET_PLANE_PASS",
        )

    result = plane.execute_with_secret(lease, invoke)
    assert result["executed"] is True
    assert result["output_text"] == "MATVERSE_SECRET_PLANE_PASS"
    assert observed_authorization["value"] == f"Bearer {raw_secret}"

    public_evidence = json.dumps(
        {"result": result, "descriptors": plane.descriptors(), "audit": plane.audit_events()},
        sort_keys=True,
    )
    assert raw_secret not in public_evidence
    assert "openai-slot" not in public_evidence
