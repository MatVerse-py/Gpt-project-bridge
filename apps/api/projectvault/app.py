from __future__ import annotations

import html
import json
import shutil
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import unquote

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response

from . import __version__
from .auth import Authenticator, Principal
from .config import Settings
from .db import Database
from .files import ingest_directory
from .ingest import ingest_export
from .mcp import MCPHandler
from .search import KnowledgeService

MAX_UPLOAD_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_FILES = 50_000
MAX_ARCHIVE_EXPANDED_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_MEMBER_BYTES = 100 * 1024 * 1024


def _safe_destination(root: Path, member_name: str) -> Path:
    normalised = member_name.replace("\\", "/")
    if not normalised or normalised.startswith("/") or "\x00" in normalised:
        raise ValueError("unsafe_archive_path")
    parts = [part for part in normalised.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise ValueError("unsafe_archive_path")
    target = (root / Path(*parts)).resolve()
    root_resolved = root.resolve()
    if target != root_resolved and root_resolved not in target.parents:
        raise ValueError("unsafe_archive_path")
    return target


def _safe_extract_zip(source: Path, destination: Path) -> None:
    total = 0
    count = 0
    with zipfile.ZipFile(source) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            count += 1
            total += info.file_size
            if count > MAX_ARCHIVE_FILES:
                raise ValueError("archive_file_count_limit")
            if info.file_size > MAX_ARCHIVE_MEMBER_BYTES:
                raise ValueError("archive_member_size_limit")
            if total > MAX_ARCHIVE_EXPANDED_BYTES:
                raise ValueError("archive_expanded_size_limit")
            unix_mode = (info.external_attr >> 16) & 0xFFFF
            if unix_mode and (unix_mode & 0o170000) == 0o120000:
                raise ValueError("archive_symlink_rejected")
            target = _safe_destination(destination, info.filename)
            target.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            with archive.open(info) as src, target.open("wb") as dst:
                while chunk := src.read(1024 * 1024):
                    written += len(chunk)
                    if written > MAX_ARCHIVE_MEMBER_BYTES:
                        raise ValueError("archive_member_size_limit")
                    dst.write(chunk)


async def _save_upload(upload: UploadFile, destination: Path) -> int:
    size = 0
    with destination.open("wb") as handle:
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                raise HTTPException(413, "upload_too_large")
            handle.write(chunk)
    return size


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    db = Database(settings.database_path)
    auth = Authenticator(settings)
    mcp = MCPHandler(settings, db, auth)
    knowledge = KnowledgeService(db, settings.public_base_url, settings.max_results)

    app = FastAPI(
        title="GPT Project Bridge",
        version=__version__,
        description="Owner-controlled project knowledge vault with REST and MCP read access.",
    )
    app.state.settings = settings
    app.state.db = db

    @app.middleware("http")
    async def security_guard(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and origin not in settings.allowed_origins:
            return JSONResponse({"error": "origin_not_allowed"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
        return response

    async def principal(request: Request) -> Principal:
        result = await auth.authenticate(request)
        assert result is not None
        return result

    async def optional_mcp_principal(request: Request) -> Principal | None:
        return await auth.authenticate(request, optional=settings.auth_mode in {"oidc", "hybrid"})

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {"status": "ok", "version": __version__, "stats": db.stats()}

    @app.get("/.well-known/oauth-protected-resource")
    async def protected_resource_metadata() -> dict[str, object]:
        if settings.auth_mode not in {"oidc", "hybrid"}:
            return {
                "resource": settings.public_base_url,
                "authorization_servers": [],
                "scopes_supported": [],
                "resource_documentation": f"{settings.public_base_url}/",
            }
        return {
            "resource": settings.public_base_url,
            "authorization_servers": [settings.oidc_issuer],
            "scopes_supported": [settings.required_scope],
            "resource_documentation": f"{settings.public_base_url}/",
        }

    @app.get("/mcp")
    async def mcp_get() -> Response:
        return Response(status_code=405, headers={"Allow": "POST"})

    @app.delete("/mcp")
    async def mcp_delete() -> Response:
        return Response(status_code=405, headers={"Allow": "POST"})

    @app.post("/mcp")
    async def mcp_post(
        request: Request,
        user: Principal | None = Depends(optional_mcp_principal),
    ) -> Response:
        return await mcp.handle(request, user)

    @app.get("/api/stats")
    async def api_stats(user: Principal = Depends(principal)) -> dict[str, object]:
        stats = db.stats()
        db.audit(user.subject, "api_stats", None, None, {})
        return {"stats": stats}

    @app.get("/api/projects")
    async def api_projects(user: Principal = Depends(principal)) -> dict[str, object]:
        rows = [dict(row) for row in db.list_projects()]
        db.audit(user.subject, "api_projects", None, None, {"result_count": len(rows)})
        return {"projects": rows}

    @app.get("/api/ingestions")
    async def api_ingestions(limit: int = 100, user: Principal = Depends(principal)) -> dict[str, object]:
        rows: list[dict[str, object]] = []
        for row in db.list_ingestions(limit):
            item = dict(row)
            item["metadata"] = json.loads(str(item.pop("metadata_json") or "{}"))
            rows.append(item)
        db.audit(user.subject, "api_ingestions", None, None, {"result_count": len(rows)})
        return {"ingestions": rows}

    @app.get("/api/unassigned")
    async def api_unassigned(limit: int = 200, user: Principal = Depends(principal)) -> dict[str, object]:
        rows: list[dict[str, object]] = []
        for row in db.list_unassigned(limit):
            item = dict(row)
            item["metadata"] = json.loads(str(item.pop("metadata_json") or "{}"))
            rows.append(item)
        db.audit(user.subject, "api_unassigned", None, None, {"result_count": len(rows)})
        return {"documents": rows}

    @app.get("/api/search")
    async def api_search(
        q: str,
        project_id: str | None = None,
        user: Principal = Depends(principal),
    ) -> dict[str, object]:
        try:
            result = knowledge.search(q, project_id=project_id)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        db.audit(
            user.subject,
            "api_search",
            None,
            None,
            {"query": q, "project_id": project_id, "result_count": len(result["results"])},
        )
        return result

    @app.get("/api/documents/{document_id:path}")
    async def api_document(document_id: str, user: Principal = Depends(principal)) -> dict[str, object]:
        try:
            item = knowledge.fetch(unquote(document_id))
        except KeyError as exc:
            raise HTTPException(404, "document_not_found") from exc
        db.audit(user.subject, "api_document", item["id"], None, {})
        return item

    @app.post("/api/assignments")
    async def api_assignments(payload: dict[str, str], user: Principal = Depends(principal)) -> dict[str, object]:
        document_id = str(payload.get("document_id") or "").strip()
        project_id = str(payload.get("project_id") or "").strip()
        if not document_id or not project_id:
            raise HTTPException(422, "document_id_and_project_id_required")
        try:
            db.assign_document(document_id, project_id, user.subject)
        except KeyError as exc:
            raise HTTPException(404, f"not_found:{exc.args[0]}") from exc
        return {"status": "assigned", "document_id": document_id, "project_id": project_id}

    @app.post("/api/ingest/export")
    async def api_ingest_export(
        export: UploadFile = File(...),
        owner_map: UploadFile | None = File(None),
        user: Principal = Depends(principal),
    ) -> dict[str, object]:
        filename = Path(export.filename or "chatgpt-export.zip").name
        if not filename.lower().endswith(".zip"):
            raise HTTPException(422, "export_must_be_zip")
        with tempfile.TemporaryDirectory(prefix="gpb-export-") as temp_dir:
            root = Path(temp_dir)
            export_path = root / filename
            await _save_upload(export, export_path)
            owner_map_path: Path | None = None
            if owner_map is not None and owner_map.filename:
                owner_map_path = root / "owner-map.json"
                await _save_upload(owner_map, owner_map_path)
            try:
                result = ingest_export(db, export_path, owner_map_path)
            except (ValueError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
                raise HTTPException(422, f"ingest_failed:{exc}") from exc
        db.audit(user.subject, "api_ingest_export", result["run_id"], None, {"source_name": filename})
        return {"status": "completed", "run": result, "stats": db.stats()}

    @app.post("/api/ingest/project-files")
    async def api_ingest_project_files(
        archive: UploadFile = File(...),
        project_id: str = Form(...),
        project_name: str = Form(...),
        user: Principal = Depends(principal),
    ) -> dict[str, object]:
        filename = Path(archive.filename or "project-files.zip").name
        if not filename.lower().endswith(".zip"):
            raise HTTPException(422, "archive_must_be_zip")
        with tempfile.TemporaryDirectory(prefix="gpb-files-") as temp_dir:
            root = Path(temp_dir)
            archive_path = root / filename
            extraction_path = root / "content"
            extraction_path.mkdir()
            await _save_upload(archive, archive_path)
            try:
                _safe_extract_zip(archive_path, extraction_path)
                result = ingest_directory(db, extraction_path, project_id, project_name)
            except (ValueError, zipfile.BadZipFile) as exc:
                raise HTTPException(422, f"ingest_failed:{exc}") from exc
        db.audit(user.subject, "api_ingest_project_files", result["run_id"], None, {"source_name": filename})
        return {"status": "completed", "run": result, "stats": db.stats()}

    @app.get("/documents/{document_id:path}")
    async def document(document_id: str, user: Principal = Depends(principal)) -> HTMLResponse:
        try:
            item = knowledge.fetch(unquote(document_id))
        except KeyError as exc:
            raise HTTPException(404, "Document not found") from exc
        db.audit(user.subject, "document_read", item["id"], None, {})
        body = html.escape(item["text"])
        metadata = html.escape(json.dumps(item["metadata"], ensure_ascii=False, indent=2))
        return HTMLResponse(
            f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(item['title'])}</title>
<style>body{{font:16px/1.55 system-ui;max-width:1000px;margin:40px auto;padding:0 20px;background:#f7f7f7;color:#171717}}article{{background:white;padding:28px;border-radius:14px;box-shadow:0 4px 24px #0001}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}details{{margin-top:24px}}</style></head>
<body><article><h1>{html.escape(item['title'])}</h1><pre>{body}</pre><details><summary>Metadados e proveniência</summary><pre>{metadata}</pre></details></article></body></html>"""
        )

    @app.get("/", response_class=HTMLResponse)
    async def home() -> HTMLResponse:
        stats = db.stats()
        return HTMLResponse(
            f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>GPT Project Bridge API</title></head>
<body><h1>GPT Project Bridge API</h1><p>Version {__version__}</p><pre>{html.escape(json.dumps(stats, indent=2))}</pre>
<p>Use the web application or <code>POST /mcp</code>.</p></body></html>"""
        )

    return app


app = create_app()
