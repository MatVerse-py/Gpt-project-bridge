"""Deterministic Bridge evidence-catalog sidecar for ARGUS.

This service searches only pre-resolved/indexed evidence. It is deliberately
not a web-search oracle. External acquisition adapters may populate the catalog,
while the query path stays deterministic, inspectable and fail-closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Mapping
import json
import os
import re

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, model_validator

from app.source_exchange import ARGUS_BATCH_SCHEMA


CATALOG_SCHEMA = "matverse.bridge-evidence-catalog.v1"
QUERY_SCHEMA = "matverse.argus-evidence-query.v1"
_TOKEN_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9_.:/-]{3,}")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CLAIM_SCOPED_KEYS = {"claim_relation", "context_status"}
_BINDING_KEYS = {"relation_claim_ref", "relation_claim_sha256"}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")


def _tokens(value: str) -> set[str]:
    return {match.group(0).casefold() for match in _TOKEN_RE.finditer(value)}


def _normalized_claim(value: str) -> str:
    return " ".join(value.split())


def _claim_sha256(value: str) -> str:
    return sha256(_normalized_claim(value).encode("utf-8")).hexdigest()


def _searchable_text(item: Mapping[str, Any]) -> str:
    parts = [
        str(item.get("locator") or ""),
        str(item.get("search_text") or ""),
        str(item.get("observed_text") or ""),
    ]
    metadata = item.get("metadata")
    if isinstance(metadata, Mapping):
        for key, value in metadata.items():
            if str(key) in _CLAIM_SCOPED_KEYS or str(key) in _BINDING_KEYS:
                continue
            if isinstance(value, (str, int, float, bool)):
                parts.append(f"{key} {value}")
    return " ".join(parts)


def _scope_is_bound(
    item: Mapping[str, Any],
    *,
    claim_ref: str,
    claim_text: str,
    claim_sha256: str,
) -> bool:
    bound_ref = str(item.get("relation_claim_ref") or "").strip()
    bound_hash = str(item.get("relation_claim_sha256") or "").strip().lower()
    if bound_ref and bound_ref == claim_ref:
        return True
    effective_hash = claim_sha256 or (_claim_sha256(claim_text) if claim_text else "")
    if bound_hash and effective_hash and bound_hash == effective_hash:
        return True
    return False


def _relation_is_bound(
    item: Mapping[str, Any],
    *,
    claim_ref: str,
    claim_text: str,
    claim_sha256: str,
) -> bool:
    relation = str(item.get("claim_relation") or "").strip()
    return bool(relation) and _scope_is_bound(
        item,
        claim_ref=claim_ref,
        claim_text=claim_text,
        claim_sha256=claim_sha256,
    )


def _public_item(
    item: Mapping[str, Any],
    *,
    claim_ref: str,
    claim_text: str,
    claim_sha256: str,
) -> dict[str, Any]:
    # Search/binding helpers are catalog-only and never emitted.
    result = {
        str(key): value
        for key, value in item.items()
        if str(key) not in {"search_text", *_BINDING_KEYS}
    }

    # Nested metadata is informational only. Claim-scoped controls must never be
    # smuggled through metadata because the consumer would otherwise be unable
    # to prove that they were bound to the current claim.
    metadata = result.get("metadata")
    if isinstance(metadata, Mapping):
        result["metadata"] = {
            str(key): value
            for key, value in metadata.items()
            if str(key) not in _CLAIM_SCOPED_KEYS and str(key) not in _BINDING_KEYS
        }

    bound = _scope_is_bound(
        item,
        claim_ref=claim_ref,
        claim_text=claim_text,
        claim_sha256=claim_sha256,
    )
    if "claim_relation" in result and not bound:
        result.pop("claim_relation", None)
    if "context_status" in result and not bound:
        result.pop("context_status", None)
    return result


@dataclass(frozen=True)
class BridgeEvidenceCatalog:
    items: tuple[Mapping[str, Any], ...]

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "BridgeEvidenceCatalog":
        if payload.get("schema") != CATALOG_SCHEMA:
            raise ValueError(f"unsupported catalog schema: {payload.get('schema')!r}")
        items = payload.get("items")
        if not isinstance(items, list):
            raise ValueError("catalog requires items[]")
        normalized: list[Mapping[str, Any]] = []
        for item in items:
            if not isinstance(item, Mapping):
                raise ValueError("catalog items must be objects")
            if not str(item.get("locator") or "").strip():
                raise ValueError("catalog item locator is required")
            if not str(item.get("representation") or "").strip():
                raise ValueError("catalog item representation is required")
            normalized.append(dict(item))
        return cls(tuple(normalized))

    @classmethod
    def from_file(cls, path: str | Path) -> "BridgeEvidenceCatalog":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("catalog JSON must be an object")
        return cls.from_payload(payload)

    def search(
        self,
        claim_text: str = "",
        *,
        claim_ref: str = "",
        claim_sha256: str = "",
        query_terms: tuple[str, ...] = (),
        max_sources: int = 32,
    ) -> dict[str, Any]:
        query_tokens = {
            str(term).strip().casefold()
            for term in query_terms
            if len(str(term).strip()) >= 3
        }
        if not query_tokens and claim_text:
            query_tokens = _tokens(claim_text)

        scored: list[tuple[int, str, Mapping[str, Any]]] = []
        for item in self.items:
            haystack = _searchable_text(item)
            hay_tokens = _tokens(haystack)
            overlap = len(query_tokens & hay_tokens)
            phrase_bonus = 3 if claim_text.strip() and claim_text.casefold() in haystack.casefold() else 0
            relation_bonus = 1 if _relation_is_bound(
                item,
                claim_ref=claim_ref,
                claim_text=claim_text,
                claim_sha256=claim_sha256,
            ) else 0
            score = overlap + phrase_bonus + relation_bonus
            if score <= 0:
                continue
            scored.append((score, str(item.get("locator")), item))

        scored.sort(key=lambda row: (-row[0], row[1]))
        selected = [
            _public_item(
                item,
                claim_ref=claim_ref,
                claim_text=claim_text,
                claim_sha256=claim_sha256,
            )
            for _, _, item in scored[: max(0, max_sources)]
        ]
        digest = sha256(_canonical(selected)).hexdigest()

        return {
            "schema": ARGUS_BATCH_SCHEMA,
            "evidence_hash": digest,
            "state": "PARTIAL" if selected else "UNAVAILABLE_AFTER_FALLBACK",
            "evidence_tier": "P1" if selected else "P0",
            "authority": {},
            "resolved_url": None,
            "independent_evidence": any(item.get("independent") is True for item in selected),
            "official_version_evidence": False,
            "identifiers": {},
            "claimed_identifiers": {},
            "conflicts": [],
            "items": selected,
            "catalog_match_count": len(selected),
        }


class EvidenceQuery(BaseModel):
    schema: str
    claim_ref: str = Field(min_length=1, max_length=512)
    claim_text: str = Field(default="", max_length=10000)
    claim_sha256: str = Field(default="", max_length=64)
    query_terms: list[str] = Field(default_factory=list, max_length=64)
    max_sources: int = Field(default=32, ge=0, le=256)

    @model_validator(mode="after")
    def validate_query(self) -> "EvidenceQuery":
        digest = self.claim_sha256.strip().lower()
        if digest and not _SHA256_RE.fullmatch(digest):
            raise ValueError("claim_sha256 must be lowercase SHA-256 hex")
        if self.claim_text and digest and _claim_sha256(self.claim_text) != digest:
            raise ValueError("claim_text does not match claim_sha256")
        if not self.claim_text and not self.query_terms and not digest:
            raise ValueError("query requires claim_text, query_terms or claim_sha256")
        self.claim_sha256 = digest
        return self


CatalogProvider = Callable[[], BridgeEvidenceCatalog]


def environment_catalog() -> BridgeEvidenceCatalog:
    path = os.environ.get("MATVERSE_SOURCE_CATALOG", "").strip()
    if not path:
        return BridgeEvidenceCatalog(())
    return BridgeEvidenceCatalog.from_file(path)


def create_app(catalog_provider: CatalogProvider = environment_catalog) -> FastAPI:
    app = FastAPI(title="MatVerse Source Evidence Catalog", version="1.0.0")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "schema": ARGUS_BATCH_SCHEMA}

    @app.post("/evidence/query")
    def evidence_query(req: EvidenceQuery) -> dict[str, Any]:
        if req.schema != QUERY_SCHEMA:
            raise HTTPException(status_code=422, detail="unsupported query schema")
        try:
            catalog = catalog_provider()
            return catalog.search(
                req.claim_text,
                claim_ref=req.claim_ref,
                claim_sha256=req.claim_sha256,
                query_terms=tuple(req.query_terms),
                max_sources=req.max_sources,
            )
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"evidence catalog unavailable: {type(exc).__name__}") from exc

    return app


app = create_app()
