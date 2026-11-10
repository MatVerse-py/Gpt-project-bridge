from __future__ import annotations

import hashlib
import time
import uuid

from app import storage
from app.auth import sign_request
from conftest import PRINCIPALS, auth_request


def admin_get(client, path: str, params: dict[str, str]):
    body = b""
    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex
    content_hash = hashlib.sha256(body).hexdigest()
    signature = sign_request(PRINCIPALS["admin"]["secret"], "GET", path, timestamp, nonce, content_hash)
    headers = {
        "X-MatVerse-Principal": "admin",
        "X-MatVerse-Timestamp": timestamp,
        "X-MatVerse-Nonce": nonce,
        "X-MatVerse-Content-SHA256": content_hash,
        "X-MatVerse-Signature": signature,
    }
    return client.get(path, params=params, headers=headers)


def test_corpus_seed_is_idempotent_and_reconciles_venice_catalog(client):
    first = auth_request(client, "admin", "POST", "/model-registry/seed-corpus", {})
    assert first.status_code == 200, first.text
    seeded = first.json()
    assert seeded["decision"] == "PASS"
    assert seeded["entries_total"] == 428
    assert seeded["entries_inserted"] == 428
    assert seeded["venice_catalog"] == {
        "source_claimed_count": 392,
        "extracted_unique_model_ids": 392,
        "unresolved_count": 0,
        "context_labels_extracted": 139,
        "reconciliation_status": "RECONCILED_FROM_SAVED_MHTML",
        "snapshot_date": "2026-11-10",
    }

    second = auth_request(client, "admin", "POST", "/model-registry/seed-corpus", {})
    assert second.status_code == 200, second.text
    assert second.json()["idempotent"] is True
    assert second.json()["entries_inserted"] == 0
    assert second.json()["entries_existing"] == 428

    summary = auth_request(client, "admin", "GET", "/model-registry/summary")
    assert summary.status_code == 200
    assert summary.json()["entries_total"] == 428
    assert summary.json()["venice_catalog_reconciliation"]["unresolved_count"] == 0
    assert storage.verify_chain()["ok"] is True


def test_venice_catalog_entries_are_not_promoted_to_live_models(client):
    seeded = auth_request(client, "admin", "POST", "/model-registry/seed-corpus", {})
    assert seeded.status_code == 200

    response = admin_get(
        client,
        "/model-registry/entries",
        {"provider_id": "venice", "entry_type": "MODEL"},
    )
    assert response.status_code == 200
    entries = response.json()["entries"]
    assert len(entries) == 392

    claude = next(item for item in entries if item["model_id"] == "claude-fable-5-2")
    assert claude["integration_state"] == "CATALOG_ONLY"
    assert claude["runtime_state"] == "UNKNOWN"
    assert claude["adapter_id"] is None
    assert claude["metadata"]["catalog_provider"] == "venice"


def test_models_runtimes_gateways_services_and_platforms_are_distinct(client):
    auth_request(client, "admin", "POST", "/model-registry/seed-corpus", {})

    for entry_type, entry_id in (
        ("GATEWAY", "gateway:kilo"),
        ("AGENT_SERVICE", "service:manus"),
        ("PLATFORM", "platform:base44"),
        ("RUNTIME", "runtime:ollama"),
        ("RUNTIME", "runtime:huggingface-transformers"),
        ("TRANSPORT", "transport:claude-web"),
        ("AGENT_ADAPTER", "agent-adapter:venice"),
    ):
        response = admin_get(
            client, "/model-registry/entries", {"entry_type": entry_type}
        )
        assert response.status_code == 200
        assert entry_id in {item["entry_id"] for item in response.json()["entries"]}

    models = admin_get(client, "/model-registry/entries", {"entry_type": "MODEL"})
    assert models.status_code == 200
    assert "gateway:kilo" not in {item["entry_id"] for item in models.json()["entries"]}


def test_cross_runtime_models_keep_exact_revisions_and_scoped_maturity(client):
    auth_request(client, "admin", "POST", "/model-registry/seed-corpus", {})
    response = admin_get(
        client,
        "/model-registry/entries",
        {"integration_state": "RUNTIME_REPRODUCED"},
    )
    assert response.status_code == 200
    entries = {item["model_id"]: item for item in response.json()["entries"]}
    assert entries["Qwen/Qwen2.5-0.5B-Instruct"]["model_revision"] == "7ae557604adf67be50417f59c2c2f167def9a775"
    assert entries["HuggingFaceTB/SmolLM2-360M-Instruct"]["model_revision"] == "a10cc1512eabd3dde888204e902eca88bddb4951"
    assert all(item["runtime_state"] == "UNKNOWN" for item in entries.values())


def test_negative_pilot_is_retained_without_inventing_full_model_id(client):
    auth_request(client, "admin", "POST", "/model-registry/seed-corpus", {})
    response = admin_get(
        client,
        "/model-registry/entries",
        {"query": "SmolLM2-135M"},
    )
    assert response.status_code == 200
    item = next(x for x in response.json()["entries"] if x["entry_id"] == "model:huggingface:smollm2-135m-pilot")
    assert item["integration_state"] == "PILOT_FAILED"
    assert item["model_id"] is None
    assert item["metadata"]["identity_completeness"] == "INCOMPLETE"


def test_registry_rejects_self_asserted_live_availability(client):
    payload = {
        "entry_id": "model:test:unverified",
        "entry_type": "MODEL",
        "provider_id": "test",
        "display_name": "Unverified test model",
        "model_id": "test-model",
        "integration_state": "REGISTERED",
        "runtime_state": "AVAILABLE",
        "source_id": "unit-test",
    }
    response = auth_request(client, "admin", "POST", "/model-registry/entries", payload)
    assert response.status_code == 422


def test_registry_metadata_rejects_credentials_and_private_state(client):
    payload = {
        "entry_id": "model:test:secret",
        "entry_type": "MODEL",
        "provider_id": "test",
        "display_name": "Secret-bearing model record",
        "model_id": "test-model",
        "source_id": "unit-test",
        "metadata": {"api_key": "must-not-enter-registry"},
    }
    response = auth_request(client, "admin", "POST", "/model-registry/entries", payload)
    assert response.status_code == 422


def test_manual_registry_registration_is_immutable_and_idempotent(client):
    payload = {
        "entry_id": "model:test:example",
        "entry_type": "MODEL",
        "provider_id": "test",
        "display_name": "Example model",
        "model_id": "example-model-v1",
        "integration_state": "REGISTERED",
        "runtime_state": "UNKNOWN",
        "source_id": "unit-test",
        "modalities": ["text"],
        "capabilities": ["text_generation"],
    }
    first = auth_request(client, "admin", "POST", "/model-registry/entries", payload)
    assert first.status_code == 200, first.text
    assert first.json()["idempotent"] is False

    second = auth_request(client, "admin", "POST", "/model-registry/entries", payload)
    assert second.status_code == 200, second.text
    assert second.json()["idempotent"] is True

    changed = {**payload, "model_id": "example-model-v2"}
    conflict = auth_request(client, "admin", "POST", "/model-registry/entries", changed)
    assert conflict.status_code == 409


def test_model_registry_protocol_exposes_promotion_boundary(client):
    response = client.get("/model-registry/protocol")
    assert response.status_code == 200
    body = response.json()
    assert body["protocol_version"] == "matverse.model-registry.v1"
    assert "catalog presence or adapter code never implies runtime availability" == body["availability_rule"]


def test_registry_agent_adapters_match_runtime_agent_specs(client):
    from app.agent_runtime import AGENTS

    auth_request(client, "admin", "POST", "/model-registry/seed-corpus", {})
    response = admin_get(client, "/model-registry/entries", {"entry_type": "AGENT_ADAPTER"})
    assert response.status_code == 200
    entries = response.json()["entries"]
    registered = {item["metadata"]["agent_id"]: item for item in entries}
    assert set(registered) == set(AGENTS)
    for agent_id, spec in AGENTS.items():
        assert registered[agent_id]["provider_id"] == spec.provider
        assert registered[agent_id]["metadata"]["transport"] == spec.transport


def test_registry_modality_filter_matches_structured_modalities_only(client):
    auth_request(client, "admin", "POST", "/model-registry/seed-corpus", {})
    response = admin_get(client, "/model-registry/entries", {"provider_id": "venice", "modality": "video"})
    assert response.status_code == 200
    entries = response.json()["entries"]
    assert entries
    assert all("video" in item["modalities"] for item in entries)
    assert all(item["provider_id"] == "venice" for item in entries)


def test_context_windows_come_from_explicit_mhtml_labels(client):
    auth_request(client, "admin", "POST", "/model-registry/seed-corpus", {})
    response = admin_get(
        client,
        "/model-registry/entries",
        {"provider_id": "venice", "query": "grok-4-20"},
    )
    assert response.status_code == 200
    item = next(x for x in response.json()["entries"] if x["model_id"] == "grok-4-20")
    assert item["context_window_tokens"] == 2_000_000
    assert item["metadata"]["context_window_source"] == "saved_mhtml_context_label"


def test_adapter_snapshot_reconciles_registry_and_runtime_configuration(client):
    auth_request(client, "admin", "POST", "/model-registry/seed-corpus", {})
    response = auth_request(client, "admin", "GET", "/model-registry/adapter-snapshot")
    assert response.status_code == 200
    body = response.json()
    assert body["adapters_total"] == 9
    assert body["registry_mismatch_total"] == 0
    assert body["connection_verified_total"] == 0
    assert {item["agent_id"] for item in body["adapters"]} == {
        "gpt", "claude", "kilo", "venice", "manus", "base44", "claude-code", "claude-web", "kilo-code"
    }
    assert all(item["alignment"] == "MATCHED" for item in body["adapters"])


def test_runtime_snapshot_joins_local_discovery_without_promoting_registry_state(client, monkeypatch):
    from app import runtime_discovery

    auth_request(client, "admin", "POST", "/model-registry/seed-corpus", {})
    fake_snapshot = {
        "protocol_version": "matverse.runtime-discovery.v1",
        "report_hash": "a" * 64,
        "selector": {"decision": "PASS", "runtime_id": "ollama", "reason": "api_ready"},
        "policy": {"discovery_only": True, "auto_install": False, "remote_endpoints_allowed": False},
        "capabilities": [
            {
                "runtime_id": "ollama",
                "state": "AVAILABLE",
                "models": [{"name": "Qwen/Qwen2.5-0.5B-Instruct", "digest": "sha256:local-qwen", "size": 123}],
            },
            {
                "runtime_id": "llama_cpp",
                "state": "ABSENT",
                "models": [],
            },
        ],
    }
    monkeypatch.setattr(runtime_discovery, "discover_runtime_capabilities", lambda: fake_snapshot)
    response = auth_request(client, "admin", "GET", "/model-registry/runtime-snapshot")
    assert response.status_code == 200
    body = response.json()
    assert body["discovered_model_count"] == 1
    discovered = body["models"][0]
    assert discovered["registry_match"] is True
    assert discovered["registry_entry_id"] == "model:huggingface:qwen2.5-0.5b-instruct"
    assert discovered["runtime_state"] == "AVAILABLE"

    registry_entry = admin_get(
        client,
        "/model-registry/entries",
        {"query": "Qwen/Qwen2.5-0.5B-Instruct"},
    ).json()["entries"][0]
    assert registry_entry["runtime_state"] == "UNKNOWN"


def test_capability_selector_uses_registered_adapters_and_provider_preference(client, monkeypatch):
    from app import agent_runtime

    auth_request(client, "admin", "POST", "/model-registry/seed-corpus", {})
    monkeypatch.setattr(
        agent_runtime,
        "agent_catalog",
        lambda: [
            {
                "agent_id": "gpt", "provider": "openai", "model": "gpt-6-astra",
                "transport": "openai_responses", "roles": ["research"],
                "configured": True, "connection_verified": False, "status": "CONFIGURED_UNVERIFIED",
            },
            {
                "agent_id": "venice", "provider": "venice", "model": "venice-uncensored",
                "transport": "openai_compatible_chat", "roles": ["research"],
                "configured": True, "connection_verified": False, "status": "CONFIGURED_UNVERIFIED",
            },
            {
                "agent_id": "claude-web", "provider": "anthropic", "model": "claude",
                "transport": "external_handoff", "roles": ["research"],
                "configured": True, "connection_verified": False, "status": "CONFIGURED_UNVERIFIED",
            },
        ],
    )
    response = auth_request(
        client,
        "admin",
        "POST",
        "/model-registry/select",
        {
            "capability": "research",
            "modality": "text",
            "provider_preference": ["venice", "openai"],
            "require_configured": True,
            "allow_external_handoff": False,
            "limit": 5,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["decision"] == "PASS"
    assert body["executed"] is False
    assert [item["agent_id"] for item in body["candidates"]] == ["venice", "gpt"]
    assert all(item["connection_verified"] is False for item in body["candidates"])
    assert all(item["selection_state"] == "CONFIGURED_UNVERIFIED" for item in body["candidates"])


def test_capability_selector_holds_when_live_connection_is_required(client, monkeypatch):
    from app import agent_runtime

    auth_request(client, "admin", "POST", "/model-registry/seed-corpus", {})
    monkeypatch.setattr(
        agent_runtime,
        "agent_catalog",
        lambda: [
            {
                "agent_id": "venice", "provider": "venice", "model": "venice-uncensored",
                "transport": "openai_compatible_chat", "roles": ["research"],
                "configured": True, "connection_verified": False, "status": "CONFIGURED_UNVERIFIED",
            },
        ],
    )
    response = auth_request(
        client,
        "admin",
        "POST",
        "/model-registry/select",
        {
            "capability": "research",
            "modality": "text",
            "require_configured": True,
            "require_connection_verified": True,
        },
    )
    assert response.status_code == 200
    assert response.json()["decision"] == "HOLD"
    assert response.json()["reason"] == "no_candidate_matches_capability_and_constraints"
    assert response.json()["candidates"] == []
