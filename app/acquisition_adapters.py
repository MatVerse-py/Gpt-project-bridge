from __future__ import annotations

"""Explicit external acquisition adapters for the Bridge evidence catalog.

Adapters acquire structured observations from named public services and convert
those responses into ``SourceRepresentation`` objects. They do not adjudicate
truth, do not assign claim relations, and do not turn network success into
publication/scientific validity.

Network transport is injectable so CI remains deterministic and offline.
"""

from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Callable, Mapping, Protocol
from urllib import parse, request
import json
import re

from app.source_evidence import RepresentationType, SourceRepresentation, canonical_json, normalize_identifier


JsonTransport = Callable[[str, Mapping[str, str], float], tuple[bytes, Mapping[str, str]]]


class AcquisitionError(RuntimeError):
    pass


def _default_json_transport(url: str, headers: Mapping[str, str], timeout: float) -> tuple[bytes, Mapping[str, str]]:
    req = request.Request(url, headers=dict(headers), method="GET")
    try:
        with request.urlopen(req, timeout=timeout) as response:  # nosec - adapter URL is fixed by provider
            return response.read(), dict(response.headers.items())
    except Exception as exc:
        raise AcquisitionError(f"acquisition failed: {type(exc).__name__}: {exc}") from exc


def _decode_json(raw: bytes) -> Mapping[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise AcquisitionError(f"invalid JSON response: {type(exc).__name__}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise AcquisitionError("provider response must be a JSON object")
    return payload


def _first(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, str) and item.strip():
                return item.strip()
    return None


def _names(rows: Any) -> list[str]:
    out: list[str] = []
    if not isinstance(rows, list):
        return out
    for row in rows:
        if isinstance(row, str) and row.strip():
            out.append(row.strip())
            continue
        if not isinstance(row, Mapping):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            given = str(row.get("given") or row.get("given-names") or "").strip()
            family = str(row.get("family") or row.get("family-name") or "").strip()
            name = " ".join(part for part in (given, family) if part)
        if name:
            out.append(name)
    return out


def _date_parts(value: Any) -> str | None:
    if isinstance(value, Mapping):
        value = value.get("date-parts")
    if not isinstance(value, list) or not value:
        return None
    row = value[0]
    if not isinstance(row, list) or not row:
        return None
    try:
        parts = [int(item) for item in row[:3]]
    except (TypeError, ValueError):
        return None
    if len(parts) == 1:
        return f"{parts[0]:04d}"
    if len(parts) == 2:
        return f"{parts[0]:04d}-{parts[1]:02d}"
    return f"{parts[0]:04d}-{parts[1]:02d}-{parts[2]:02d}"


def _clean_doi(value: str) -> str:
    return normalize_identifier("doi", value)


def _clean_orcid(value: str) -> str:
    return normalize_identifier("orcid", value)


def _root_id(provider: str, identifier: str, content_hash: str) -> str:
    material = f"{provider.casefold()}\n{identifier}\n{content_hash}".encode("utf-8")
    return "ext:" + sha256(material).hexdigest()


@dataclass(frozen=True)
class AcquisitionRequest:
    provider: str
    identifier: str
    resource_type: str | None = None
    ref: str | None = None


@dataclass(frozen=True)
class AcquisitionObservation:
    provider: str
    identifier: str
    representation: SourceRepresentation
    evidence_root_id: str
    search_text: str
    response_headers: Mapping[str, str] = field(default_factory=dict)

    def catalog_item(self) -> dict[str, Any]:
        rep = self.representation
        metadata = dict(rep.metadata)
        title = str(metadata.get("title") or "")
        authors = metadata.get("author")
        author_text = " ".join(authors) if isinstance(authors, list) else str(authors or "")
        return {
            "locator": rep.locator,
            "representation": rep.kind.value,
            "source_content_hash": rep.content_hash,
            "evidence_root_id": self.evidence_root_id,
            "independent": True,
            "search_text": " ".join(part for part in (self.search_text, title, author_text) if part).strip(),
            "metadata": metadata,
        }


class AcquisitionAdapter(Protocol):
    provider: str

    def acquire(self, req: AcquisitionRequest) -> AcquisitionObservation: ...


@dataclass
class _BaseAdapter:
    transport: JsonTransport = _default_json_transport
    timeout: float = 15.0
    user_agent: str = "MatVerse-Bridge/1.0 (evidence acquisition)"

    def _get(self, url: str, *, accept: str = "application/json", extra_headers: Mapping[str, str] | None = None) -> tuple[bytes, Mapping[str, str]]:
        headers = {"Accept": accept, "User-Agent": self.user_agent, **dict(extra_headers or {})}
        return self.transport(url, headers, self.timeout)

    def _observation(
        self,
        *,
        provider: str,
        identifier: str,
        locator: str,
        kind: RepresentationType,
        raw: bytes,
        metadata: Mapping[str, Any],
        response_headers: Mapping[str, str],
        search_text: str,
    ) -> AcquisitionObservation:
        rep = SourceRepresentation.from_bytes(
            kind=kind,
            locator=locator,
            content=raw,
            metadata={
                **dict(metadata),
                "provider": provider,
                "api_observed": True,
                "response_content_type": response_headers.get("content-type") or response_headers.get("Content-Type"),
                "etag": response_headers.get("etag") or response_headers.get("ETag"),
                "last_modified": response_headers.get("last-modified") or response_headers.get("Last-Modified"),
            },
        )
        return AcquisitionObservation(
            provider=provider,
            identifier=identifier,
            representation=rep,
            evidence_root_id=_root_id(provider, identifier, rep.content_hash),
            search_text=search_text,
            response_headers=dict(response_headers),
        )


@dataclass
class CrossrefAdapter(_BaseAdapter):
    provider: str = "crossref"

    def acquire(self, req: AcquisitionRequest) -> AcquisitionObservation:
        doi = _clean_doi(req.identifier)
        url = "https://api.crossref.org/works/" + parse.quote(doi, safe="")
        raw, headers = self._get(url)
        payload = _decode_json(raw)
        message = payload.get("message")
        if not isinstance(message, Mapping):
            raise AcquisitionError("Crossref response missing message object")
        title = _first(message.get("title"))
        authors = _names(message.get("author"))
        published = _date_parts(message.get("published") or message.get("published-print") or message.get("published-online"))
        metadata = {
            "doi": _clean_doi(str(message.get("DOI") or doi)),
            "title": title,
            "author": authors,
            "publisher": message.get("publisher"),
            "published_at": published,
            "canonical_url": message.get("URL") or f"https://doi.org/{doi}",
            "container_title": _first(message.get("container-title")),
            "type": message.get("type"),
        }
        return self._observation(
            provider=self.provider,
            identifier=doi,
            locator=url,
            kind=RepresentationType.DOI_METADATA,
            raw=raw,
            metadata={k: v for k, v in metadata.items() if v not in (None, "", [])},
            response_headers=headers,
            search_text=" ".join([doi, title or "", *authors]),
        )


@dataclass
class DataCiteAdapter(_BaseAdapter):
    provider: str = "datacite"

    def acquire(self, req: AcquisitionRequest) -> AcquisitionObservation:
        doi = _clean_doi(req.identifier)
        url = "https://api.datacite.org/dois/" + parse.quote(doi, safe="")
        raw, headers = self._get(url)
        payload = _decode_json(raw)
        data = payload.get("data")
        attrs = data.get("attributes") if isinstance(data, Mapping) else None
        if not isinstance(attrs, Mapping):
            raise AcquisitionError("DataCite response missing data.attributes")
        title_rows = attrs.get("titles")
        title = None
        if isinstance(title_rows, list) and title_rows and isinstance(title_rows[0], Mapping):
            title = _first(title_rows[0].get("title"))
        creators = _names(attrs.get("creators"))
        publisher = attrs.get("publisher")
        metadata = {
            "doi": _clean_doi(str(attrs.get("doi") or doi)),
            "title": title,
            "author": creators,
            "publisher": publisher,
            "published_at": attrs.get("published") or attrs.get("publicationYear"),
            "canonical_url": attrs.get("url") or f"https://doi.org/{doi}",
            "version": attrs.get("version"),
            "type": ((attrs.get("types") or {}).get("resourceTypeGeneral") if isinstance(attrs.get("types"), Mapping) else None),
        }
        return self._observation(
            provider=self.provider,
            identifier=doi,
            locator=url,
            kind=RepresentationType.DOI_METADATA,
            raw=raw,
            metadata={k: v for k, v in metadata.items() if v not in (None, "", [])},
            response_headers=headers,
            search_text=" ".join([doi, title or "", *creators]),
        )


_ZENODO_ID_RE = re.compile(r"(?:zenodo[./]|records?/)(\d+)(?:\D|$)", re.IGNORECASE)


@dataclass
class ZenodoAdapter(_BaseAdapter):
    provider: str = "zenodo"

    @staticmethod
    def record_id(identifier: str) -> str:
        value = identifier.strip()
        if value.isdigit():
            return value
        match = _ZENODO_ID_RE.search(value)
        if match:
            return match.group(1)
        raise AcquisitionError("Zenodo identifier must contain a record id or 10.5281/zenodo.<id> DOI")

    def acquire(self, req: AcquisitionRequest) -> AcquisitionObservation:
        record_id = self.record_id(req.identifier)
        url = f"https://zenodo.org/api/records/{record_id}"
        raw, headers = self._get(url)
        payload = _decode_json(raw)
        metadata_block = payload.get("metadata")
        if not isinstance(metadata_block, Mapping):
            raise AcquisitionError("Zenodo response missing metadata")
        creators = _names(metadata_block.get("creators"))
        doi = str(payload.get("doi") or metadata_block.get("doi") or "").strip()
        links = payload.get("links") if isinstance(payload.get("links"), Mapping) else {}
        title = _first(metadata_block.get("title"))
        metadata = {
            "doi": _clean_doi(doi) if doi else None,
            "title": title,
            "author": creators,
            "published_at": metadata_block.get("publication_date") or payload.get("created"),
            "version": metadata_block.get("version"),
            "canonical_url": links.get("html") or links.get("self_html") or (f"https://zenodo.org/records/{record_id}"),
            "record_id": record_id,
            "resource_type": ((metadata_block.get("resource_type") or {}).get("type") if isinstance(metadata_block.get("resource_type"), Mapping) else None),
        }
        return self._observation(
            provider=self.provider,
            identifier=record_id,
            locator=url,
            kind=RepresentationType.API_METADATA,
            raw=raw,
            metadata={k: v for k, v in metadata.items() if v not in (None, "", [])},
            response_headers=headers,
            search_text=" ".join([record_id, doi, title or "", *creators]),
        )


@dataclass
class OrcidAdapter(_BaseAdapter):
    provider: str = "orcid"

    def acquire(self, req: AcquisitionRequest) -> AcquisitionObservation:
        orcid = _clean_orcid(req.identifier)
        url = f"https://pub.orcid.org/v3.0/{parse.quote(orcid, safe='')}/record"
        raw, headers = self._get(url, accept="application/json")
        payload = _decode_json(raw)
        person = payload.get("person") if isinstance(payload.get("person"), Mapping) else {}
        name_block = person.get("name") if isinstance(person.get("name"), Mapping) else {}
        given = ((name_block.get("given-names") or {}).get("value") if isinstance(name_block.get("given-names"), Mapping) else None)
        family = ((name_block.get("family-name") or {}).get("value") if isinstance(name_block.get("family-name"), Mapping) else None)
        author = " ".join(str(part).strip() for part in (given, family) if part)
        metadata = {
            "orcid": orcid,
            "author": author or None,
            "canonical_url": f"https://orcid.org/{orcid}",
        }
        return self._observation(
            provider=self.provider,
            identifier=orcid,
            locator=url,
            kind=RepresentationType.ORCID_SNAPSHOT,
            raw=raw,
            metadata={k: v for k, v in metadata.items() if v not in (None, "", [])},
            response_headers=headers,
            search_text=f"{orcid} {author}".strip(),
        )


_GITHUB_REPO_RE = re.compile(r"(?:https?://github\.com/)?([^/\s]+/[^/\s#?]+)")


@dataclass
class GitHubAdapter(_BaseAdapter):
    provider: str = "github"
    token: str | None = None

    @staticmethod
    def repo_name(identifier: str) -> str:
        value = identifier.strip().removesuffix(".git")
        match = _GITHUB_REPO_RE.match(value)
        if not match:
            raise AcquisitionError("GitHub identifier must be owner/repo or a github.com repo URL")
        return match.group(1).removesuffix(".git")

    def acquire(self, req: AcquisitionRequest) -> AcquisitionObservation:
        repo = self.repo_name(req.identifier)
        headers = {"X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if req.ref:
            ref = req.ref.strip()
            url = f"https://api.github.com/repos/{repo}/commits/{parse.quote(ref, safe='')}"
            raw, response_headers = self._get(url, extra_headers=headers)
            payload = _decode_json(raw)
            commit = payload.get("commit") if isinstance(payload.get("commit"), Mapping) else {}
            author = commit.get("author") if isinstance(commit.get("author"), Mapping) else {}
            sha = str(payload.get("sha") or ref)
            metadata = {
                "repo": f"https://github.com/{repo}",
                "commit_sha": sha,
                "author": author.get("name"),
                "timestamp": author.get("date"),
                "title": _first(str(commit.get("message") or "").splitlines()),
                "canonical_url": payload.get("html_url"),
            }
            return self._observation(
                provider=self.provider,
                identifier=f"{repo}@{sha}",
                locator=url,
                kind=RepresentationType.GIT_COMMIT,
                raw=raw,
                metadata={k: v for k, v in metadata.items() if v not in (None, "", [])},
                response_headers=response_headers,
                search_text=f"{repo} {sha} {metadata.get('title') or ''}",
            )

        url = f"https://api.github.com/repos/{repo}"
        raw, response_headers = self._get(url, extra_headers=headers)
        payload = _decode_json(raw)
        metadata = {
            "repo": str(payload.get("html_url") or f"https://github.com/{repo}"),
            "title": payload.get("name"),
            "author": ((payload.get("owner") or {}).get("login") if isinstance(payload.get("owner"), Mapping) else None),
            "canonical_url": payload.get("html_url"),
            "version": payload.get("default_branch"),
            "timestamp": payload.get("pushed_at") or payload.get("updated_at"),
        }
        return self._observation(
            provider=self.provider,
            identifier=repo,
            locator=url,
            kind=RepresentationType.API_METADATA,
            raw=raw,
            metadata={k: v for k, v in metadata.items() if v not in (None, "", [])},
            response_headers=response_headers,
            search_text=f"{repo} {payload.get('description') or ''}",
        )


@dataclass
class HuggingFaceAdapter(_BaseAdapter):
    provider: str = "huggingface"
    token: str | None = None

    def acquire(self, req: AcquisitionRequest) -> AcquisitionObservation:
        repo_id = req.identifier.strip().removeprefix("https://huggingface.co/").strip("/")
        if not repo_id or "/" not in repo_id:
            raise AcquisitionError("Hugging Face identifier must be namespace/name or a huggingface.co URL")
        kind = (req.resource_type or "model").strip().casefold()
        endpoint_key = {"model": "models", "dataset": "datasets", "space": "spaces"}.get(kind)
        if endpoint_key is None:
            raise AcquisitionError("Hugging Face resource_type must be model, dataset or space")
        url = f"https://huggingface.co/api/{endpoint_key}/{repo_id}"
        headers: dict[str, str] = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        raw, response_headers = self._get(url, extra_headers=headers)
        payload = _decode_json(raw)
        author = payload.get("author")
        card = payload.get("cardData") if isinstance(payload.get("cardData"), Mapping) else {}
        metadata = {
            "repo": f"https://huggingface.co/{'datasets/' if kind == 'dataset' else 'spaces/' if kind == 'space' else ''}{repo_id}",
            "title": card.get("title") or repo_id.split("/", 1)[-1],
            "author": author or repo_id.split("/", 1)[0],
            "version": payload.get("sha"),
            "timestamp": payload.get("lastModified") or payload.get("last_modified"),
            "canonical_url": f"https://huggingface.co/{'datasets/' if kind == 'dataset' else 'spaces/' if kind == 'space' else ''}{repo_id}",
            "resource_type": kind,
        }
        return self._observation(
            provider=self.provider,
            identifier=f"{kind}:{repo_id}",
            locator=url,
            kind=RepresentationType.HF_SNAPSHOT,
            raw=raw,
            metadata={k: v for k, v in metadata.items() if v not in (None, "", [])},
            response_headers=response_headers,
            search_text=f"{repo_id} {card.get('title') or ''} {author or ''}",
        )


class AcquisitionRegistry:
    def __init__(self, adapters: Mapping[str, AcquisitionAdapter] | None = None) -> None:
        self._adapters: dict[str, AcquisitionAdapter] = {}
        for key, adapter in dict(adapters or {}).items():
            self.register(key, adapter)

    @classmethod
    def default(cls, *, transport: JsonTransport = _default_json_transport, timeout: float = 15.0) -> "AcquisitionRegistry":
        common = {"transport": transport, "timeout": timeout}
        return cls(
            {
                "crossref": CrossrefAdapter(**common),
                "datacite": DataCiteAdapter(**common),
                "zenodo": ZenodoAdapter(**common),
                "orcid": OrcidAdapter(**common),
                "github": GitHubAdapter(**common),
                "huggingface": HuggingFaceAdapter(**common),
                "hf": HuggingFaceAdapter(**common),
            }
        )

    def register(self, name: str, adapter: AcquisitionAdapter) -> None:
        key = name.strip().casefold()
        if not key:
            raise ValueError("adapter name is required")
        self._adapters[key] = adapter

    def acquire(self, req: AcquisitionRequest) -> AcquisitionObservation:
        adapter = self._adapters.get(req.provider.strip().casefold())
        if adapter is None:
            raise AcquisitionError(f"unknown acquisition provider: {req.provider!r}")
        return adapter.acquire(req)

    def providers(self) -> tuple[str, ...]:
        return tuple(sorted(self._adapters))


def append_observations_to_catalog(
    catalog_payload: Mapping[str, Any],
    observations: list[AcquisitionObservation],
) -> dict[str, Any]:
    if catalog_payload.get("schema") != "matverse.bridge-evidence-catalog.v1":
        raise AcquisitionError("unsupported catalog schema")
    existing = catalog_payload.get("items")
    if not isinstance(existing, list):
        raise AcquisitionError("catalog requires items[]")

    # Deduplicate exact provider observations by evidence root. A repeated fetch
    # of identical bytes cannot inflate the catalog into independent evidence.
    items = [dict(item) for item in existing if isinstance(item, Mapping)]
    seen = {str(item.get("evidence_root_id") or "") for item in items}
    for observation in observations:
        item = observation.catalog_item()
        root = str(item["evidence_root_id"])
        if root in seen:
            continue
        seen.add(root)
        items.append(item)
    return {"schema": "matverse.bridge-evidence-catalog.v1", "items": items}
