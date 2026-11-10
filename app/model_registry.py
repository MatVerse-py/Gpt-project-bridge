from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import storage
from .auth import Principal, require_capability
from .core import stable_hash

PROTOCOL_VERSION = "matverse.model-registry.v1"
_ENTRY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._/-]{1,255}$")
_FORBIDDEN_METADATA_KEYS = {
    "apikey", "secret", "secretkey", "token", "accesstoken", "refreshtoken",
    "password", "credential", "chainofthought", "reasoningtrace", "hiddenstate",
    "privatememory", "systemprompt", "developerprompt",
}
_ENTRY_TYPES = {
    "PROVIDER", "MODEL", "RUNTIME", "GATEWAY", "AGENT_SERVICE", "AGENT_ADAPTER", "PLATFORM",
    "TRANSPORT", "CATALOG_SOURCE", "CAPABILITY",
}
router = APIRouter(prefix="/model-registry", tags=["model-registry"])


class ModelSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability: Literal[
        "research", "code", "draft", "review", "redteam", "workflow",
        "curation", "application", "metadata",
    ]
    modality: Literal["text", "image", "video", "audio"] = "text"
    provider_preference: list[str] = Field(default_factory=list, max_length=16)
    require_configured: bool = True
    require_connection_verified: bool = False
    allow_external_handoff: bool = False
    limit: int = Field(default=5, ge=1, le=20)


class RegistryEntryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_id: str = Field(min_length=3, max_length=256)
    entry_type: Literal[
        "PROVIDER", "MODEL", "RUNTIME", "GATEWAY", "AGENT_SERVICE",
        "AGENT_ADAPTER", "PLATFORM", "TRANSPORT", "CATALOG_SOURCE", "CAPABILITY",
    ]
    provider_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=256)
    model_id: str | None = Field(default=None, max_length=256)
    upstream_family: str | None = Field(default=None, max_length=128)
    model_type: str | None = Field(default=None, max_length=64)
    modalities: list[str] = Field(default_factory=list, max_length=16)
    capabilities: list[str] = Field(default_factory=list, max_length=64)
    adapter_id: str | None = Field(default=None, max_length=128)
    integration_state: Literal["REGISTERED", "CATALOG_ONLY"] = "REGISTERED"
    runtime_state: Literal["UNKNOWN", "CONFIGURED_UNVERIFIED"] = "UNKNOWN"
    model_revision: str | None = Field(default=None, max_length=256)
    context_window_tokens: int | None = Field(default=None, ge=1, le=100_000_000)
    max_output_tokens: int | None = Field(default=None, ge=1, le=10_000_000)
    source_id: str = Field(min_length=1, max_length=256)
    source_refs: list[str] = Field(default_factory=list, max_length=32)
    notes: str = Field(default="", max_length=4000)
    metadata: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True

    @field_validator("entry_id")
    @classmethod
    def validate_entry_id(cls, value: str) -> str:
        if _ENTRY_ID.fullmatch(value) is None:
            raise ValueError("entry_id contains unsupported characters")
        return value

    @field_validator("model_id")
    @classmethod
    def validate_model_id(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or any(ord(ch) < 32 for ch in value)):
            raise ValueError("model_id must be a nonempty printable identifier")
        return value

    @field_validator("metadata")
    @classmethod
    def validate_public_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        def walk(item: Any) -> None:
            if isinstance(item, dict):
                for key, nested in item.items():
                    canonical = re.sub(r"[^a-z0-9]", "", str(key).lower())
                    if canonical in _FORBIDDEN_METADATA_KEYS:
                        raise ValueError("metadata cannot contain credentials or private model state")
                    walk(nested)
            elif isinstance(item, list):
                for nested in item:
                    walk(nested)
        walk(value)
        return value


def _schema(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS model_registry_entries (
            entry_id TEXT PRIMARY KEY,
            provider_id TEXT NOT NULL,
            entry_type TEXT NOT NULL,
            integration_state TEXT NOT NULL,
            runtime_state TEXT NOT NULL,
            model_type TEXT,
            enabled INTEGER NOT NULL,
            entry_json TEXT NOT NULL,
            entry_hash TEXT NOT NULL,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_model_registry_provider ON model_registry_entries(provider_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_model_registry_state ON model_registry_entries(integration_state, runtime_state)"
    )
    conn.commit()


def _canonical_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return json.loads(storage._canonical_json(entry))


def _insert_entry_tx(conn, entry: dict[str, Any], actor: str) -> bool:
    normalized = _canonical_entry(entry)
    entry_hash = stable_hash(normalized)
    existing = conn.execute(
        "SELECT entry_hash FROM model_registry_entries WHERE entry_id=?",
        (normalized["entry_id"],),
    ).fetchone()
    if existing is not None:
        if existing["entry_hash"] != entry_hash:
            raise ValueError(f"immutable model registry identity conflict: {normalized['entry_id']}")
        return False

    created_at = storage._now()
    conn.execute(
        """
        INSERT INTO model_registry_entries(
            entry_id, provider_id, entry_type, integration_state, runtime_state,
            model_type, enabled, entry_json, entry_hash, created_by, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            normalized["entry_id"],
            normalized["provider_id"],
            normalized["entry_type"],
            normalized["integration_state"],
            normalized["runtime_state"],
            normalized.get("model_type"),
            1 if normalized.get("enabled", True) else 0,
            storage._canonical_json(normalized),
            entry_hash,
            actor,
            created_at,
        ),
    )
    storage._append_ledger_tx(
        conn,
        {
            "event_type": "MODEL_REGISTRY_ENTRY_REGISTERED",
            "entry_id": normalized["entry_id"],
            "entry_type": normalized["entry_type"],
            "provider_id": normalized["provider_id"],
            "model_id": normalized.get("model_id"),
            "integration_state": normalized["integration_state"],
            "source_id": normalized["source_id"],
            "entry_hash": entry_hash,
            "created_by": actor,
        },
        "PASS",
    )
    return True


def _row_to_entry(row) -> dict[str, Any]:
    entry = json.loads(row["entry_json"])
    entry["entry_hash"] = row["entry_hash"]
    entry["created_by"] = row["created_by"]
    entry["created_at"] = row["created_at"]
    return entry


def _venice_catalog_entries() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    snapshot_path = Path(__file__).resolve().parent.parent / "data" / "venice_catalog_snapshot_2026-11-10.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    if snapshot.get("schema") != "matverse.venice-catalog-snapshot.v1":
        raise ValueError("unsupported Venice catalog snapshot schema")
    models = snapshot.get("entries")
    if not isinstance(models, list):
        raise ValueError("Venice catalog snapshot entries are missing")

    snapshot_date = str(snapshot.get("snapshot_date") or "undated")
    entries: list[dict[str, Any]] = []
    for item in models:
        model_id = item.get("model_id")
        if not isinstance(model_id, str) or not model_id:
            continue
        modalities = item.get("modalities", ["text"])
        capabilities = []
        if "text" in modalities:
            capabilities.append("text_generation")
        if "image" in modalities:
            capabilities.append("image_generation_or_editing")
        if "video" in modalities:
            capabilities.append("video_generation_or_editing")
        if "audio" in modalities:
            capabilities.append("audio_generation")
        if "multimodal" in modalities:
            capabilities.append("multimodal_input")
        entries.append(
            {
                "entry_id": f"venice:model:{model_id}:snapshot:{snapshot_date}",
                "entry_type": "MODEL",
                "provider_id": "venice",
                "display_name": item.get("display_name") or model_id,
                "model_id": model_id,
                "upstream_family": item.get("upstream_family"),
                "model_type": item.get("model_type"),
                "modalities": modalities,
                "capabilities": capabilities,
                "adapter_id": None,
                "integration_state": "CATALOG_ONLY",
                "runtime_state": "UNKNOWN",
                "model_revision": None,
                "context_window_tokens": item.get("context_window_tokens"),
                "max_output_tokens": None,
                "source_id": f"venice-catalog-{snapshot_date}",
                "source_refs": [
                    "corpus:API Models - Venice Uncensored AI.mhtml",
                    "corpus:Texto colado(20261110-055652).txt",
                    "github:data/venice_catalog_snapshot_2026-11-10.json",
                ],
                "notes": "Catálogo do agregador Venice. Registro não implica adaptador, disponibilidade, contrato comercial ou execução verificada.",
                "metadata": {
                    "description": item.get("description", ""),
                    "source_line": item.get("source_line"),
                    "source_locator": item.get("source_locator"),
                    "context_window_source": item.get("context_window_source"),
                    "catalog_provider": "venice",
                    "snapshot_date": snapshot_date,
                },
                "enabled": True,
            }
        )
    return entries, {
        "source_claimed_count": snapshot.get("source_claimed_count"),
        "extracted_unique_model_ids": snapshot.get("extracted_unique_model_ids"),
        "unresolved_count": snapshot.get("unresolved_count"),
        "context_labels_extracted": snapshot.get("context_labels_extracted"),
        "reconciliation_status": snapshot.get("reconciliation_status"),
        "snapshot_date": snapshot.get("snapshot_date"),
    }


def _corpus_seed() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    base = [
        {
            "entry_id": "provider:openai", "entry_type": "PROVIDER", "provider_id": "openai",
            "display_name": "OpenAI", "model_id": None, "upstream_family": "openai",
            "model_type": None, "modalities": ["text", "image"], "capabilities": ["responses_api"],
            "adapter_id": "openai.responses", "integration_state": "ADAPTER_IMPLEMENTED_MAIN",
            "runtime_state": "UNKNOWN", "model_revision": None, "context_window_tokens": None,
            "max_output_tokens": None, "source_id": "github-main", "source_refs": [
                "github:app/openai_runtime.py", "github:app/openai_provider.py", "github:app/openai_secret_plane.py"
            ], "notes": "Adaptador Responses presente na main. Testes simulados não comprovam disponibilidade live.",
            "metadata": {}, "enabled": True,
        },
        {
            "entry_id": "provider:anthropic", "entry_type": "PROVIDER", "provider_id": "anthropic",
            "display_name": "Anthropic", "model_id": None, "upstream_family": "anthropic",
            "model_type": None, "modalities": ["text", "image"], "capabilities": ["messages_api"],
            "adapter_id": "agent.anthropic.messages", "integration_state": "ADAPTER_IMPLEMENTED_REVIEW",
            "runtime_state": "UNKNOWN", "model_revision": None, "context_window_tokens": None,
            "max_output_tokens": None, "source_id": "github-pr-89-draft", "source_refs": [
                "github:pull/89", "github:app/agent_runtime.py", "github:tests/test_agent_bridge.py"
            ], "notes": "Implementação em PR draft; testes de contrato com transporte simulado, sem conexão live promovida.",
            "metadata": {}, "enabled": True,
        },
        {
            "entry_id": "provider:venice", "entry_type": "PROVIDER", "provider_id": "venice",
            "display_name": "Venice API", "model_id": None, "upstream_family": "multi-provider-aggregator",
            "model_type": None, "modalities": ["text", "image", "video", "audio"], "capabilities": ["openai_compatible_chat_completions"],
            "adapter_id": "agent.venice.chat_completions", "integration_state": "ADAPTER_IMPLEMENTED_REVIEW",
            "runtime_state": "UNKNOWN", "model_revision": None, "context_window_tokens": None,
            "max_output_tokens": None, "source_id": "github-pr-89-draft", "source_refs": [
                "corpus:Texto colado(20261110-055652).txt", "github:app/agent_runtime.py", "github:pull/89"
            ], "notes": "Adaptador implementado nesta branch de revisão; chamada live e catálogo completo ainda não verificados.",
            "metadata": {"base_url": "https://api.venice.ai/api/v1"}, "enabled": True,
        },
        {
            "entry_id": "provider:google", "entry_type": "PROVIDER", "provider_id": "google",
            "display_name": "Google", "model_id": None, "upstream_family": "google",
            "model_type": None, "modalities": ["text", "image", "video", "audio"], "capabilities": [],
            "adapter_id": None, "integration_state": "CATALOG_ONLY", "runtime_state": "UNKNOWN",
            "model_revision": None, "context_window_tokens": None, "max_output_tokens": None,
            "source_id": "venice-catalog-2026-11-10", "source_refs": ["corpus:Texto colado(20261110-055652).txt"],
            "notes": "Família encontrada no catálogo; adaptador próprio não localizado na main nem na branch revisada.",
            "metadata": {}, "enabled": True,
        },
        {
            "entry_id": "provider:xai", "entry_type": "PROVIDER", "provider_id": "xai",
            "display_name": "xAI", "model_id": None, "upstream_family": "xai",
            "model_type": None, "modalities": ["text", "image", "video"], "capabilities": [],
            "adapter_id": None, "integration_state": "CATALOG_ONLY", "runtime_state": "UNKNOWN",
            "model_revision": None, "context_window_tokens": None, "max_output_tokens": None,
            "source_id": "venice-catalog-2026-11-10", "source_refs": ["corpus:Texto colado(20261110-055652).txt"],
            "notes": "Grok aparece no catálogo; não confundir presença de modelos no agregador com adaptador xAI direto.",
            "metadata": {}, "enabled": True,
        },
        {
            "entry_id": "provider:deepseek", "entry_type": "PROVIDER", "provider_id": "deepseek",
            "display_name": "DeepSeek", "model_id": None, "upstream_family": "deepseek",
            "model_type": None, "modalities": ["text", "image"], "capabilities": [],
            "adapter_id": None, "integration_state": "CATALOG_ONLY", "runtime_state": "UNKNOWN",
            "model_revision": None, "context_window_tokens": None, "max_output_tokens": None,
            "source_id": "venice-catalog-2026-11-10", "source_refs": ["corpus:Texto colado(20261110-055652).txt"],
            "notes": "Modelos DeepSeek aparecem no catálogo Venice; adaptador direto não localizado.",
            "metadata": {}, "enabled": True,
        },
        {
            "entry_id": "provider:alibaba", "entry_type": "PROVIDER", "provider_id": "alibaba",
            "display_name": "Alibaba / Qwen", "model_id": None, "upstream_family": "alibaba",
            "model_type": None, "modalities": ["text", "image", "video"], "capabilities": ["local_transformers_candidate"],
            "adapter_id": "transformers.pytorch", "integration_state": "CATALOG_ONLY",
            "runtime_state": "UNKNOWN", "model_revision": None, "context_window_tokens": None,
            "max_output_tokens": None, "source_id": "venice-catalog-2026-11-10", "source_refs": [
                "corpus:Texto colado(20261110-055652).txt"
            ], "notes": "Família upstream Qwen identificada no catálogo; a reprodução local é registrada separadamente no modelo e no runtime Transformers.",
            "metadata": {}, "enabled": True,
        },
        {
            "entry_id": "provider:minimax", "entry_type": "PROVIDER", "provider_id": "minimax",
            "display_name": "MiniMax", "model_id": None, "upstream_family": "minimax",
            "model_type": None, "modalities": ["text", "image", "video", "audio"], "capabilities": ["publication_review", "redteam", "curation", "metadata"],
            "adapter_id": None, "integration_state": "CAPABILITY_DESCRIPTOR_ONLY", "runtime_state": "UNKNOWN",
            "model_revision": None, "context_window_tokens": None, "max_output_tokens": None,
            "source_id": "llm-publication-bridge", "source_refs": ["github:app/llm_publication_bridge.py"],
            "notes": "Capability descriptor de publicação; não é adaptador de inferência.",
            "metadata": {}, "enabled": True,
        },
        {
            "entry_id": "provider:moonshot", "entry_type": "PROVIDER", "provider_id": "moonshot",
            "display_name": "Moonshot / Kimi", "model_id": None, "upstream_family": "moonshot",
            "model_type": None, "modalities": ["text", "image"], "capabilities": [],
            "adapter_id": None, "integration_state": "CATALOG_ONLY", "runtime_state": "UNKNOWN",
            "model_revision": None, "context_window_tokens": None, "max_output_tokens": None,
            "source_id": "venice-catalog-2026-11-10", "source_refs": ["corpus:Texto colado(20261110-055652).txt"],
            "notes": "Família Kimi identificada no catálogo; sem adapter próprio registrado.",
            "metadata": {}, "enabled": True,
        },
        {
            "entry_id": "provider:z-ai", "entry_type": "PROVIDER", "provider_id": "z-ai",
            "display_name": "Z.ai / GLM", "model_id": None, "upstream_family": "z-ai",
            "model_type": None, "modalities": ["text", "image"], "capabilities": [],
            "adapter_id": None, "integration_state": "CATALOG_ONLY", "runtime_state": "UNKNOWN",
            "model_revision": None, "context_window_tokens": None, "max_output_tokens": None,
            "source_id": "venice-catalog-2026-11-10", "source_refs": ["corpus:Texto colado(20261110-055652).txt"],
            "notes": "Família GLM identificada no catálogo; sem adapter próprio registrado.",
            "metadata": {}, "enabled": True,
        },
        {
            "entry_id": "provider:nvidia", "entry_type": "PROVIDER", "provider_id": "nvidia",
            "display_name": "NVIDIA", "model_id": None, "upstream_family": "nvidia",
            "model_type": None, "modalities": ["text", "image", "video"], "capabilities": [],
            "adapter_id": None, "integration_state": "CATALOG_ONLY", "runtime_state": "UNKNOWN",
            "model_revision": None, "context_window_tokens": None, "max_output_tokens": None,
            "source_id": "venice-catalog-2026-11-10", "source_refs": ["corpus:Texto colado(20261110-055652).txt"],
            "notes": "Família Nemotron identificada no catálogo; sem adapter próprio registrado.",
            "metadata": {}, "enabled": True,
        },
        {
            "entry_id": "provider:meta", "entry_type": "PROVIDER", "provider_id": "meta",
            "display_name": "Meta", "model_id": None, "upstream_family": "meta",
            "model_type": None, "modalities": ["text", "image"], "capabilities": [],
            "adapter_id": None, "integration_state": "CATALOG_ONLY", "runtime_state": "UNKNOWN",
            "model_revision": None, "context_window_tokens": None, "max_output_tokens": None,
            "source_id": "venice-catalog-2026-11-10", "source_refs": ["corpus:Texto colado(20261110-055652).txt"],
            "notes": "Família Muse/Llama identificada no catálogo; sem adapter próprio registrado.",
            "metadata": {}, "enabled": True,
        },
        {
            "entry_id": "runtime:ollama", "entry_type": "RUNTIME", "provider_id": "local",
            "display_name": "Ollama", "model_id": None, "upstream_family": "ollama",
            "model_type": None, "modalities": ["text", "image"], "capabilities": ["chat", "generate", "embeddings", "model_inventory"],
            "adapter_id": "runtime-discovery.ollama", "integration_state": "DISCOVERY_IMPLEMENTED_MAIN",
            "runtime_state": "UNKNOWN", "model_revision": None, "context_window_tokens": None,
            "max_output_tokens": None, "source_id": "github-main", "source_refs": [
                "github:app/runtime_discovery.py", "github:tests/test_runtime_discovery.py"
            ], "notes": "Descoberta local implementada; descoberta não significa modelo instalado ou inferência executada.",
            "metadata": {"default_endpoint": "http://127.0.0.1:11434"}, "enabled": True,
        },
        {
            "entry_id": "runtime:llama-cpp", "entry_type": "RUNTIME", "provider_id": "local",
            "display_name": "llama.cpp server", "model_id": None, "upstream_family": "llama.cpp",
            "model_type": None, "modalities": ["text"], "capabilities": ["chat", "openai_compatible_server"],
            "adapter_id": "runtime-discovery.llama_cpp", "integration_state": "DISCOVERY_IMPLEMENTED_MAIN",
            "runtime_state": "UNKNOWN", "model_revision": None, "context_window_tokens": None,
            "max_output_tokens": None, "source_id": "github-main", "source_refs": [
                "github:app/runtime_discovery.py", "github:tests/test_runtime_discovery.py"
            ], "notes": "Descoberta local implementada; digest imutável de modelo é exigido pelo runtime binding.",
            "metadata": {"default_endpoint": "http://127.0.0.1:8080"}, "enabled": True,
        },
        {
            "entry_id": "runtime:huggingface-transformers", "entry_type": "RUNTIME", "provider_id": "huggingface",
            "display_name": "Hugging Face Transformers / PyTorch", "model_id": None, "upstream_family": "huggingface",
            "model_type": None, "modalities": ["text"], "capabilities": ["transformers_inference", "pytorch_cpu"],
            "adapter_id": "transformers.pytorch", "integration_state": "RUNTIME_REPRODUCED",
            "runtime_state": "UNKNOWN", "model_revision": None, "context_window_tokens": None,
            "max_output_tokens": None, "source_id": "cross-runtime-evidence-pack", "source_refs": [
                "github:evidence/reproduction-v1/EVIDENCE_PACK.json"
            ], "notes": "Runtime Transformers/PyTorch executou Qwen2.5-0.5B-Instruct e SmolLM2-360M-Instruct em runners separados; não é um endpoint always-on.",
            "metadata": {"execution_scope": "github-actions-cpu-runners", "runtime_availability": "not_probed"}, "enabled": True,
        },
        {
            "entry_id": "gateway:kilo", "entry_type": "GATEWAY", "provider_id": "kilo",
            "display_name": "Kilo Gateway", "model_id": None, "upstream_family": "kilo",
            "model_type": None, "modalities": ["text"], "capabilities": ["chat_completions", "coding"],
            "adapter_id": "agent.kilo.gateway", "integration_state": "ADAPTER_IMPLEMENTED_REVIEW",
            "runtime_state": "UNKNOWN", "model_revision": None, "context_window_tokens": None,
            "max_output_tokens": None, "source_id": "github-pr-89-draft", "source_refs": ["github:pull/89", "github:app/agent_runtime.py"],
            "notes": "Gateway de modelos, não modelo fundacional. Modelo subjacente precisa ser fixado na inscrição.",
            "metadata": {}, "enabled": True,
        },
        {
            "entry_id": "service:manus", "entry_type": "AGENT_SERVICE", "provider_id": "manus",
            "display_name": "Manus Agent", "model_id": None, "upstream_family": "manus",
            "model_type": None, "modalities": ["text"], "capabilities": ["task_create", "task_reconcile"],
            "adapter_id": "agent.manus.tasks_v2", "integration_state": "ADAPTER_IMPLEMENTED_REVIEW",
            "runtime_state": "UNKNOWN", "model_revision": None, "context_window_tokens": None,
            "max_output_tokens": None, "source_id": "github-pr-89-draft", "source_refs": ["github:pull/89", "github:app/agent_runtime.py"],
            "notes": "Serviço de agente. Perfis lite/standard/max não são IDs de modelo fundacional.",
            "metadata": {}, "enabled": True,
        },
        {
            "entry_id": "platform:base44", "entry_type": "PLATFORM", "provider_id": "base44",
            "display_name": "Base44 MCP", "model_id": None, "upstream_family": "base44",
            "model_type": None, "modalities": ["text"], "capabilities": ["mcp_tools", "application_workflow"],
            "adapter_id": "agent.base44.mcp", "integration_state": "ADAPTER_IMPLEMENTED_REVIEW",
            "runtime_state": "UNKNOWN", "model_revision": None, "context_window_tokens": None,
            "max_output_tokens": None, "source_id": "github-pr-89-draft", "source_refs": ["github:pull/89", "github:app/agent_runtime.py"],
            "notes": "Plataforma MCP, não identidade de modelo.",
            "metadata": {}, "enabled": True,
        },
        {
            "entry_id": "transport:claude-code", "entry_type": "TRANSPORT", "provider_id": "anthropic",
            "display_name": "Claude Code external handoff", "model_id": None, "upstream_family": "anthropic",
            "model_type": None, "modalities": ["text"], "capabilities": ["external_handoff"],
            "adapter_id": "agent.external_handoff", "integration_state": "EXTERNAL_HANDOFF_REVIEW",
            "runtime_state": "UNKNOWN", "model_revision": None, "context_window_tokens": None,
            "max_output_tokens": None, "source_id": "github-pr-89-draft", "source_refs": ["github:pull/89", "github:AGENT_BRIDGE_V1.md"],
            "notes": "Transporte externo autenticado; não executa nem autentica o cliente Claude Code.",
            "metadata": {}, "enabled": True,
        },
        {
            "entry_id": "transport:claude-web", "entry_type": "TRANSPORT", "provider_id": "anthropic",
            "display_name": "Claude Web manual relay", "model_id": None, "upstream_family": "anthropic",
            "model_type": None, "modalities": ["text"], "capabilities": ["external_handoff"],
            "adapter_id": "agent.external_handoff", "integration_state": "EXTERNAL_HANDOFF_REVIEW",
            "runtime_state": "UNKNOWN", "model_revision": None, "context_window_tokens": None,
            "max_output_tokens": None, "source_id": "github-pr-89-draft", "source_refs": ["github:pull/89", "github:AGENT_BRIDGE_V1.md"],
            "notes": "Relay manual de navegador; não representa conexão contínua com a interface web.",
            "metadata": {}, "enabled": True,
        },
        {
            "entry_id": "transport:kilo-code", "entry_type": "TRANSPORT", "provider_id": "kilo",
            "display_name": "Kilo Code external handoff", "model_id": None, "upstream_family": "kilo",
            "model_type": None, "modalities": ["text"], "capabilities": ["external_handoff"],
            "adapter_id": "agent.external_handoff", "integration_state": "EXTERNAL_HANDOFF_REVIEW",
            "runtime_state": "UNKNOWN", "model_revision": None, "context_window_tokens": None,
            "max_output_tokens": None, "source_id": "github-pr-89-draft", "source_refs": ["github:pull/89", "github:AGENT_BRIDGE_V1.md"],
            "notes": "Relay autenticado para cliente externo; não é modelo nem gateway de inferência.",
            "metadata": {}, "enabled": True,
        },
        {
            "entry_id": "model:openai:gpt-6-astra", "entry_type": "MODEL", "provider_id": "openai",
            "display_name": "GPT-6 Astra", "model_id": "gpt-6-astra", "upstream_family": "openai",
            "model_type": "language", "modalities": ["text", "image"], "capabilities": ["reasoning", "coding", "tool_use"],
            "adapter_id": "openai.responses", "integration_state": "ADAPTER_IMPLEMENTED_MAIN",
            "runtime_state": "UNKNOWN", "model_revision": None, "context_window_tokens": None,
            "max_output_tokens": None, "source_id": "github-main-env-example", "source_refs": [
                "github:.env.example", "github:app/openai_runtime.py", "github:app/openai_provider.py"
            ], "notes": "Modelo configurado como default do adaptador. Conexão live depende de credencial e execução no ambiente-alvo.",
            "metadata": {}, "enabled": True,
        },
        {
            "entry_id": "model:openai:gpt-5.6-sol", "entry_type": "MODEL", "provider_id": "openai",
            "display_name": "GPT-5.6 Sol", "model_id": "gpt-5.6-sol", "upstream_family": "openai",
            "model_type": "language", "modalities": ["text", "image"], "capabilities": ["reasoning", "coding"],
            "adapter_id": "openai.responses", "integration_state": "CONFIGURED_UNVERIFIED",
            "runtime_state": "UNKNOWN", "model_revision": None, "context_window_tokens": None,
            "max_output_tokens": None, "source_id": "github-executor-substitution-workflow", "source_refs": [
                "github:.github/workflows/executor-substitution-v1.yml", "github:LIVE_EXECUTOR_SUBSTITUTION_RUNBOOK.md"
            ], "notes": "Modelo A do workflow de substituição; o workflow live exige acionamento explícito e OPENAI_API_KEY.",
            "metadata": {}, "enabled": True,
        },
        {
            "entry_id": "model:huggingface:qwen2.5-0.5b-instruct", "entry_type": "MODEL", "provider_id": "huggingface",
            "display_name": "Qwen2.5-0.5B-Instruct", "model_id": "Qwen/Qwen2.5-0.5B-Instruct", "upstream_family": "alibaba-qwen",
            "model_type": "language", "modalities": ["text"], "capabilities": ["text_generation", "runtime_reproduction"],
            "adapter_id": "transformers.pytorch", "integration_state": "RUNTIME_REPRODUCED",
            "runtime_state": "UNKNOWN", "model_revision": "7ae557604adf67be50417f59c2c2f167def9a775",
            "context_window_tokens": None, "max_output_tokens": None, "source_id": "cross-runtime-evidence-pack",
            "source_refs": ["github:evidence/reproduction-v1/EVIDENCE_PACK.json", "github:commit/46fa0a3af799fd2ba1b4440e7f93f426f29570bb"],
            "notes": "Executado em runner GitHub Actions com Transformers/PyTorch sob contrato congelado; não é validação externa independente.",
            "metadata": {"runner": "GitHub Actions 1000000695", "engine": "transformers-pytorch"}, "enabled": True,
        },
        {
            "entry_id": "model:huggingface:smollm2-360m-instruct", "entry_type": "MODEL", "provider_id": "huggingface",
            "display_name": "SmolLM2-360M-Instruct", "model_id": "HuggingFaceTB/SmolLM2-360M-Instruct", "upstream_family": "huggingface-smollm",
            "model_type": "language", "modalities": ["text"], "capabilities": ["text_generation", "runtime_reproduction"],
            "adapter_id": "transformers.pytorch", "integration_state": "RUNTIME_REPRODUCED",
            "runtime_state": "UNKNOWN", "model_revision": "a10cc1512eabd3dde888204e902eca88bddb4951",
            "context_window_tokens": None, "max_output_tokens": None, "source_id": "cross-runtime-evidence-pack",
            "source_refs": ["github:evidence/reproduction-v1/EVIDENCE_PACK.json", "github:commit/46fa0a3af799fd2ba1b4440e7f93f426f29570bb"],
            "notes": "Executado em runner GitHub Actions com Transformers/PyTorch sob contrato congelado; não é validação externa independente.",
            "metadata": {"runner": "GitHub Actions 1000000694", "engine": "transformers-pytorch"}, "enabled": True,
        },
        {
            "entry_id": "model:huggingface:smollm2-135m-pilot", "entry_type": "MODEL", "provider_id": "huggingface",
            "display_name": "SmolLM2-135M (pilot; exact repository id not preserved in evidence pack)", "model_id": None,
            "upstream_family": "huggingface-smollm", "model_type": "language", "modalities": ["text"],
            "capabilities": ["text_generation"], "adapter_id": "transformers.pytorch", "integration_state": "PILOT_FAILED",
            "runtime_state": "UNKNOWN", "model_revision": None, "context_window_tokens": None, "max_output_tokens": None,
            "source_id": "cross-runtime-negative-pilot", "source_refs": [
                "github:experiments/cross_runtime/PILOT_NEGATIVE_RESULT_001.md",
                "github:evidence/reproduction-v1/EVIDENCE_PACK.json"
            ], "notes": "O corpus registra SmolLM2-135M como piloto negativo, mas não preserva o ID completo do repositório. Não inferir HuggingFaceTB/SmolLM2-135M-Instruct.",
            "metadata": {"identity_completeness": "INCOMPLETE"}, "enabled": True,
        },
        {
            "entry_id": "catalog-source:venice:2026-11-10", "entry_type": "CATALOG_SOURCE", "provider_id": "venice",
            "display_name": "Venice API catalog snapshot — partial extraction", "model_id": None,
            "upstream_family": "venice", "model_type": None, "modalities": [], "capabilities": [],
            "adapter_id": None, "integration_state": "CATALOG_ONLY", "runtime_state": "UNKNOWN",
            "model_revision": None, "context_window_tokens": None, "max_output_tokens": None,
            "source_id": "venice-catalog-2026-11-10", "source_refs": [
                "corpus:API Models - Venice Uncensored AI.mhtml", "corpus:Texto colado(20261110-055652).txt",
                "github:data/venice_catalog_snapshot_2026-11-10.json"
            ], "notes": "A interface fonte declara 392 modelos; os 392 IDs únicos foram reconciliados usando o DOM do MHTML salvo. Nenhum ID permanece sem resolução.",
            "metadata": {"source_claimed_count": 392, "extracted_unique_model_ids": 392, "unresolved_count": 0, "context_labels_extracted": 139, "reconciliation_status": "RECONCILED_FROM_SAVED_MHTML"},
            "enabled": True,
        },
    ]
    agent_adapters = [
        {
            "entry_id": "agent-adapter:gpt", "entry_type": "AGENT_ADAPTER", "provider_id": "openai",
            "display_name": "GPT / OpenAI Responses agent adapter", "model_id": None, "upstream_family": "openai",
            "model_type": None, "modalities": ["text", "image"], "capabilities": ["research", "draft", "review"],
            "adapter_id": "openai.responses", "integration_state": "ADAPTER_IMPLEMENTED_MAIN",
            "runtime_state": "UNKNOWN", "model_revision": None, "context_window_tokens": None,
            "max_output_tokens": None, "source_id": "github-main", "source_refs": ["github:app/agent_runtime.py", "github:app/openai_runtime.py"],
            "notes": "Agent adapter GPT ligado ao broker OpenAI existente; disponibilidade live permanece dependente do ambiente.",
            "metadata": {"agent_id": "gpt", "transport": "openai_responses"}, "enabled": True,
        },
        {
            "entry_id": "agent-adapter:claude", "entry_type": "AGENT_ADAPTER", "provider_id": "anthropic",
            "display_name": "Claude / Anthropic Messages agent adapter", "model_id": None, "upstream_family": "anthropic",
            "model_type": None, "modalities": ["text", "image"], "capabilities": ["research", "code", "draft", "review"],
            "adapter_id": "agent.anthropic.messages", "integration_state": "ADAPTER_IMPLEMENTED_REVIEW",
            "runtime_state": "UNKNOWN", "model_revision": None, "context_window_tokens": None,
            "max_output_tokens": None, "source_id": "github-pr-89-draft", "source_refs": ["github:pull/89", "github:app/agent_runtime.py"],
            "notes": "Adapter no branch de revisão; testes simulados não comprovam conexão live.",
            "metadata": {"agent_id": "claude", "transport": "anthropic_messages"}, "enabled": True,
        },
        {
            "entry_id": "agent-adapter:kilo", "entry_type": "AGENT_ADAPTER", "provider_id": "kilo",
            "display_name": "Kilo Gateway agent adapter", "model_id": None, "upstream_family": "kilo",
            "model_type": None, "modalities": ["text"], "capabilities": ["code", "review", "redteam"],
            "adapter_id": "agent.kilo.gateway", "integration_state": "ADAPTER_IMPLEMENTED_REVIEW",
            "runtime_state": "UNKNOWN", "model_revision": None, "context_window_tokens": None,
            "max_output_tokens": None, "source_id": "github-pr-89-draft", "source_refs": ["github:pull/89", "github:app/agent_runtime.py"],
            "notes": "Gateway; a identidade do modelo subjacente é fixada no contrato da sessão.",
            "metadata": {"agent_id": "kilo", "transport": "kilo_gateway"}, "enabled": True,
        },
        {
            "entry_id": "agent-adapter:venice", "entry_type": "AGENT_ADAPTER", "provider_id": "venice",
            "display_name": "Venice OpenAI-compatible agent adapter", "model_id": None, "upstream_family": "venice",
            "model_type": None, "modalities": ["text"], "capabilities": ["research", "code", "draft", "review", "redteam"],
            "adapter_id": "agent.venice.chat_completions", "integration_state": "ADAPTER_IMPLEMENTED_REVIEW",
            "runtime_state": "UNKNOWN", "model_revision": None, "context_window_tokens": None,
            "max_output_tokens": None, "source_id": "github-pr-89-draft", "source_refs": ["github:pull/89", "corpus:Texto colado(20261110-055652).txt"],
            "notes": "Chat Completions only; adapter code does not imply support for all 392 catalog modalities.",
            "metadata": {"agent_id": "venice", "transport": "openai_compatible_chat"}, "enabled": True,
        },
        {
            "entry_id": "agent-adapter:manus", "entry_type": "AGENT_ADAPTER", "provider_id": "manus",
            "display_name": "Manus Tasks agent adapter", "model_id": None, "upstream_family": "manus",
            "model_type": None, "modalities": ["text"], "capabilities": ["workflow", "research", "curation"],
            "adapter_id": "agent.manus.tasks_v2", "integration_state": "ADAPTER_IMPLEMENTED_REVIEW",
            "runtime_state": "UNKNOWN", "model_revision": None, "context_window_tokens": None,
            "max_output_tokens": None, "source_id": "github-pr-89-draft", "source_refs": ["github:pull/89", "github:app/agent_runtime.py"],
            "notes": "Serviço assíncrono; perfis lite/standard/max não são IDs de modelos fundacionais.",
            "metadata": {"agent_id": "manus", "transport": "manus_tasks_v2"}, "enabled": True,
        },
        {
            "entry_id": "agent-adapter:base44", "entry_type": "AGENT_ADAPTER", "provider_id": "base44",
            "display_name": "Base44 MCP agent adapter", "model_id": None, "upstream_family": "base44",
            "model_type": None, "modalities": ["text"], "capabilities": ["application", "workflow"],
            "adapter_id": "agent.base44.mcp", "integration_state": "ADAPTER_IMPLEMENTED_REVIEW",
            "runtime_state": "UNKNOWN", "model_revision": None, "context_window_tokens": None,
            "max_output_tokens": None, "source_id": "github-pr-89-draft", "source_refs": ["github:pull/89", "github:app/agent_runtime.py"],
            "notes": "Plataforma e superfície MCP; não é identidade de modelo fundacional.",
            "metadata": {"agent_id": "base44", "transport": "base44_mcp"}, "enabled": True,
        },
        {
            "entry_id": "agent-adapter:claude-code", "entry_type": "AGENT_ADAPTER", "provider_id": "anthropic",
            "display_name": "Claude Code external handoff adapter", "model_id": None, "upstream_family": "anthropic",
            "model_type": None, "modalities": ["text"], "capabilities": ["code", "review"],
            "adapter_id": "agent.external_handoff", "integration_state": "EXTERNAL_HANDOFF_REVIEW",
            "runtime_state": "UNKNOWN", "model_revision": None, "context_window_tokens": None,
            "max_output_tokens": None, "source_id": "github-pr-89-draft", "source_refs": ["github:pull/89", "github:AGENT_BRIDGE_V1.md"],
            "notes": "Relay externo autenticado; o Bridge não executa nem autentica o cliente Claude Code.",
            "metadata": {"agent_id": "claude-code", "transport": "external_handoff"}, "enabled": True,
        },
        {
            "entry_id": "agent-adapter:claude-web", "entry_type": "AGENT_ADAPTER", "provider_id": "anthropic",
            "display_name": "Claude Web external handoff adapter", "model_id": None, "upstream_family": "anthropic",
            "model_type": None, "modalities": ["text"], "capabilities": ["research", "draft", "review"],
            "adapter_id": "agent.external_handoff", "integration_state": "EXTERNAL_HANDOFF_REVIEW",
            "runtime_state": "UNKNOWN", "model_revision": None, "context_window_tokens": None,
            "max_output_tokens": None, "source_id": "github-pr-89-draft", "source_refs": ["github:pull/89", "github:AGENT_BRIDGE_V1.md"],
            "notes": "Relay manual; não é uma conexão contínua com a interface web.",
            "metadata": {"agent_id": "claude-web", "transport": "external_handoff"}, "enabled": True,
        },
        {
            "entry_id": "agent-adapter:kilo-code", "entry_type": "AGENT_ADAPTER", "provider_id": "kilo",
            "display_name": "Kilo Code external handoff adapter", "model_id": None, "upstream_family": "kilo",
            "model_type": None, "modalities": ["text"], "capabilities": ["code", "review", "redteam"],
            "adapter_id": "agent.external_handoff", "integration_state": "EXTERNAL_HANDOFF_REVIEW",
            "runtime_state": "UNKNOWN", "model_revision": None, "context_window_tokens": None,
            "max_output_tokens": None, "source_id": "github-pr-89-draft", "source_refs": ["github:pull/89", "github:AGENT_BRIDGE_V1.md"],
            "notes": "Relay externo autenticado; não é inferência própria do Bridge.",
            "metadata": {"agent_id": "kilo-code", "transport": "external_handoff"}, "enabled": True,
        },
    ]
    venice_models, reconciliation = _venice_catalog_entries()
    return base + agent_adapters + venice_models, reconciliation


def seed_corpus_catalog(actor: str) -> dict[str, Any]:
    entries, reconciliation = _corpus_seed()
    seed_hash = stable_hash(entries)
    conn = storage._connect()
    try:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        inserted = 0
        existing = 0
        for entry in entries:
            if _insert_entry_tx(conn, entry, actor):
                inserted += 1
            else:
                existing += 1
        if inserted:
            storage._append_ledger_tx(
                conn,
                {
                    "event_type": "MODEL_REGISTRY_CORPUS_SEEDED",
                    "seed_hash": seed_hash,
                    "entries_total": len(entries),
                    "entries_inserted": inserted,
                    "entries_existing": existing,
                    "venice_catalog": reconciliation,
                    "created_by": actor,
                },
                "PASS",
            )
        conn.commit()
        return {
            "protocol_version": PROTOCOL_VERSION,
            "decision": "PASS",
            "seed_hash": seed_hash,
            "entries_total": len(entries),
            "entries_inserted": inserted,
            "entries_existing": existing,
            "idempotent": inserted == 0,
            "venice_catalog": reconciliation,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def register_entry(entry: dict[str, Any], actor: str) -> dict[str, Any]:
    conn = storage._connect()
    try:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        inserted = _insert_entry_tx(conn, entry, actor)
        conn.commit()
        return {"entry": entry, "idempotent": not inserted}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_entries(
    *,
    provider_id: str | None = None,
    entry_type: str | None = None,
    integration_state: str | None = None,
    runtime_state: str | None = None,
    model_type: str | None = None,
    modality: str | None = None,
    query: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    conn = storage._connect()
    try:
        _schema(conn)
        clauses = []
        params: list[Any] = []
        for column, value in (
            ("provider_id", provider_id),
            ("entry_type", entry_type),
            ("integration_state", integration_state),
            ("runtime_state", runtime_state),
            ("model_type", model_type),
        ):
            if value:
                clauses.append(f"{column}=?")
                params.append(value)
        if modality:
            clauses.append("LOWER(entry_json) LIKE ?")
            params.append(f'%"{modality.lower()}"%')
        if query:
            clauses.append("(LOWER(entry_id) LIKE ? OR LOWER(entry_json) LIKE ?)")
            needle = f"%{query.lower()}%"
            params.extend([needle, needle])
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM model_registry_entries{where} ORDER BY provider_id, entry_type, entry_id LIMIT ?",
            (*params, 5000),
        ).fetchall()
        entries = [_row_to_entry(row) for row in rows]
        if modality:
            entries = [entry for entry in entries if modality.lower() in {
                str(value).lower() for value in entry.get("modalities", [])
            }]
        return entries[:max(1, min(limit, 500))]
    finally:
        conn.close()


def registry_summary() -> dict[str, Any]:
    conn = storage._connect()
    try:
        _schema(conn)
        def grouped(column: str) -> dict[str, int]:
            rows = conn.execute(
                f"SELECT {column}, COUNT(*) AS n FROM model_registry_entries WHERE enabled=1 GROUP BY {column}"
            ).fetchall()
            return {str(row[column] or "UNKNOWN"): int(row["n"]) for row in rows}
        source = conn.execute(
            "SELECT entry_json FROM model_registry_entries WHERE entry_id='catalog-source:venice:2026-11-10'"
        ).fetchone()
        reconciliation = json.loads(source["entry_json"])["metadata"] if source else None
        total = conn.execute("SELECT COUNT(*) AS n FROM model_registry_entries WHERE enabled=1").fetchone()["n"]
        return {
            "protocol_version": PROTOCOL_VERSION,
            "entries_total": int(total),
            "by_provider": grouped("provider_id"),
            "by_entry_type": grouped("entry_type"),
            "by_integration_state": grouped("integration_state"),
            "by_runtime_state": grouped("runtime_state"),
            "venice_catalog_reconciliation": reconciliation,
        }
    finally:
        conn.close()


@router.get("/protocol")
def model_registry_protocol() -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "identity": "provider + entry_type + immutable entry_id + optional exact model_id",
        "separation": ["provider", "model", "runtime", "gateway", "agent_service", "agent_adapter", "platform", "transport", "capability"],
        "availability_rule": "catalog presence or adapter code never implies runtime availability",
        "promotion_rule": "runtime availability must come from a runtime probe or execution, not caller-supplied metadata",
        "catalog_rule": "historical and aggregator catalogs remain CATALOG_ONLY until separately bound and tested",
    }


@router.post("/seed-corpus")
def seed_model_registry(principal: Principal = Depends(require_capability("model-registry:write"))) -> dict[str, Any]:
    try:
        return seed_corpus_catalog(principal.principal_id)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/summary")
def read_model_registry_summary(
    principal: Principal = Depends(require_capability("model-registry:read")),
) -> dict[str, Any]:
    return {**registry_summary(), "read_by": principal.principal_id}


@router.post("/select")
def select_model_adapter(
    req: ModelSelectionRequest,
    principal: Principal = Depends(require_capability("model-registry:read")),
) -> dict[str, Any]:
    from .agent_runtime import agent_catalog

    registered = {
        item.get("metadata", {}).get("agent_id"): item
        for item in list_entries(entry_type="AGENT_ADAPTER", limit=500)
        if item.get("metadata", {}).get("agent_id")
    }
    runtime_agents = agent_catalog()
    candidates = []
    for agent in runtime_agents:
        agent_id = agent.get("agent_id")
        entry = registered.get(agent_id)
        if entry is None:
            continue
        if req.capability not in agent.get("roles", []):
            continue
        if req.modality not in entry.get("modalities", []):
            continue
        transport = agent.get("transport")
        if transport == "external_handoff" and not req.allow_external_handoff:
            continue
        configured = bool(agent.get("configured", False))
        connection_verified = bool(agent.get("connection_verified", False))
        if req.require_configured and not configured:
            continue
        if req.require_connection_verified and not connection_verified:
            continue
        candidates.append(
            {
                "agent_id": agent_id,
                "provider": agent.get("provider"),
                "model": agent.get("model"),
                "transport": transport,
                "roles": agent.get("roles", []),
                "configured": configured,
                "connection_verified": connection_verified,
                "selection_state": (
                    "LIVE_VERIFIED" if connection_verified else
                    "CONFIGURED_UNVERIFIED" if configured else
                    "BLOCKED_CONFIGURATION"
                ),
                "registry_entry_id": entry["entry_id"],
                "integration_state": entry["integration_state"],
                "adapter_id": entry.get("adapter_id"),
            }
        )
    preference = {provider: index for index, provider in enumerate(req.provider_preference)}
    maturity_rank = {
        "ADAPTER_IMPLEMENTED_MAIN": 0,
        "ADAPTER_IMPLEMENTED_REVIEW": 1,
        "EXTERNAL_HANDOFF_REVIEW": 2,
    }
    candidates.sort(
        key=lambda item: (
            preference.get(item["provider"], len(preference)),
            maturity_rank.get(item["integration_state"], 9),
            not item["configured"],
            item["agent_id"],
        )
    )
    candidates = candidates[:req.limit]
    verified = sum(1 for item in candidates if item["connection_verified"])
    configured = sum(1 for item in candidates if item["configured"])
    decision = "PASS" if candidates else "HOLD"
    reason = "candidate_configuration_matches" if candidates else "no_candidate_matches_capability_and_constraints"
    if candidates and req.require_connection_verified and verified == 0:
        decision, reason = "HOLD", "no_connection_verified_candidate"
    return {
        "protocol_version": PROTOCOL_VERSION,
        "decision": decision,
        "reason": reason,
        "executed": False,
        "selection_policy": "capability and configuration shortlist only; no provider invocation or live availability claim",
        "capability": req.capability,
        "modality": req.modality,
        "candidates_total": len(candidates),
        "configured_candidates": configured,
        "connection_verified_candidates": verified,
        "candidates": candidates,
        "read_by": principal.principal_id,
    }


@router.get("/adapter-snapshot")
def read_model_registry_adapter_snapshot(
    principal: Principal = Depends(require_capability("model-registry:read")),
) -> dict[str, Any]:
    from .agent_runtime import agent_catalog

    registered = {
        item.get("metadata", {}).get("agent_id"): item
        for item in list_entries(entry_type="AGENT_ADAPTER", limit=500)
        if item.get("metadata", {}).get("agent_id")
    }
    agents = agent_catalog()
    aligned = []
    for agent in agents:
        entry = registered.get(agent["agent_id"])
        aligned.append(
            {
                "agent_id": agent["agent_id"],
                "provider": agent.get("provider"),
                "model": agent.get("model"),
                "transport": agent.get("transport"),
                "roles": agent.get("roles", []),
                "configured": bool(agent.get("configured", False)),
                "connection_verified": bool(agent.get("connection_verified", False)),
                "status": agent.get("status", "UNKNOWN"),
                "registry_entry_id": entry.get("entry_id") if entry else None,
                "registry_integration_state": entry.get("integration_state") if entry else None,
                "alignment": "MATCHED" if entry else "MISSING_REGISTRY_ENTRY",
            }
        )
    return {
        "protocol_version": PROTOCOL_VERSION,
        "adapters_total": len(aligned),
        "configured_total": sum(1 for item in aligned if item["configured"]),
        "connection_verified_total": sum(1 for item in aligned if item["connection_verified"]),
        "registry_mismatch_total": sum(1 for item in aligned if item["alignment"] != "MATCHED"),
        "adapters": aligned,
        "read_by": principal.principal_id,
    }


@router.get("/runtime-snapshot")
def read_model_registry_runtime_snapshot(
    principal: Principal = Depends(require_capability("model-registry:read")),
) -> dict[str, Any]:
    from .runtime_discovery import discover_runtime_capabilities

    snapshot = discover_runtime_capabilities()
    registered = {
        item.get("model_id"): item
        for item in list_entries(entry_type="MODEL", limit=500)
        if item.get("model_id")
    }
    discovered = []
    for runtime in snapshot.get("capabilities", []):
        for model in runtime.get("models", []):
            model_id = model.get("name")
            if not isinstance(model_id, str) or not model_id:
                continue
            entry = registered.get(model_id)
            discovered.append(
                {
                    "runtime_id": runtime.get("runtime_id"),
                    "runtime_state": runtime.get("state", "UNKNOWN"),
                    "model_id": model_id,
                    "digest": model.get("digest"),
                    "size": model.get("size"),
                    "registry_entry_id": entry.get("entry_id") if entry else None,
                    "registry_match": entry is not None,
                }
            )
    return {
        "protocol_version": PROTOCOL_VERSION,
        "discovery_report_hash": snapshot.get("report_hash"),
        "selector": snapshot.get("selector"),
        "policy": snapshot.get("policy"),
        "runtimes": snapshot.get("capabilities", []),
        "discovered_model_count": len(discovered),
        "models": discovered,
        "read_by": principal.principal_id,
    }


@router.get("/entries")
def read_model_registry_entries(
    provider_id: str | None = None,
    entry_type: str | None = None,
    integration_state: str | None = None,
    runtime_state: str | None = None,
    model_type: str | None = None,
    modality: str | None = Query(default=None, max_length=32),
    query: str | None = Query(default=None, max_length=256),
    limit: int = Query(default=500, ge=1, le=500),
    principal: Principal = Depends(require_capability("model-registry:read")),
) -> dict[str, Any]:
    if entry_type and entry_type not in _ENTRY_TYPES:
        raise HTTPException(status_code=422, detail="unsupported entry_type filter")
    return {
        "protocol_version": PROTOCOL_VERSION,
        "entries": list_entries(
            provider_id=provider_id,
            entry_type=entry_type,
            integration_state=integration_state,
            runtime_state=runtime_state,
            model_type=model_type,
            modality=modality,
            query=query,
            limit=limit,
        ),
        "read_by": principal.principal_id,
    }


@router.post("/entries")
def create_model_registry_entry(
    req: RegistryEntryCreate,
    principal: Principal = Depends(require_capability("model-registry:write")),
) -> dict[str, Any]:
    if req.entry_type == "MODEL" and not req.model_id:
        raise HTTPException(status_code=422, detail="model_id is required for a MODEL entry")
    if req.entry_type != "MODEL" and req.model_id is not None:
        raise HTTPException(status_code=422, detail="model_id is reserved for MODEL entries")
    entry = req.model_dump(mode="json")
    try:
        result = register_entry(entry, principal.principal_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"protocol_version": PROTOCOL_VERSION, "decision": "PASS", **result}
