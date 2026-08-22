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
from .files import ArchiveLimits, ingest_archive
from .ingest import ingest_export
from .mcp import MCPHandler
from .search import KnowledgeService


INGEST_UPLOAD_PATHS = {
    "/api/ingest/export",
    "/api/ingest/project-files",
    "/api/ingest/manus-backup",
}
SINGLE_ARCHIVE_UPLOAD_PATHS = {
    "/api/ingest/project-files",
    "/api/ingest/manus-backup",
}
MULTIPART_OVERHEAD_BYTES = 2 * 1024 * 1024


async def _save_upload(
    upload: UploadFile,
    destination: Path,
    *,
    max_upload_bytes: int,
    min_free_bytes: int,
) -> int:
    size = 0
    with destination.open("wb") as handle:
        while chunk := await upload.read(1024 * 1024):
            if size + len(chunk) > max_upload_bytes:
                raise HTTPException(413, "upload_too_large")
            if shutil.disk_usage(destination.parent).free < len(chunk) + min_free_bytes:
                raise HTTPException(507, "insufficient_storage")
            handle.write(chunk)
            size += len(chunk)
    return size


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.staging_dir.mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = str(settings.staging_dir)
    db = Database(settings.database_path)
    auth = Authenticator(settings)
    mcp = MCPHandler(settings, db, auth)
    knowledge = KnowledgeService(db, settings.public_base_url, settings.max_results)
    archive_limits = ArchiveLimits.from_settings(settings)

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
        if request.method == "POST" and request.url.path in INGEST_UPLOAD_PATHS:
            content_length = request.headers.get("content-length")
            if content_length is None:
                return JSONResponse({"detail": "content_length_required_for_archive_upload"}, status_code=411)
            try:
                body_bytes = int(content_length)
            except ValueError:
                return JSONResponse({"detail": "invalid_content_length"}, status_code=400)
            if body_bytes < 0:
                return JSONResponse({"detail": "invalid_content_length"}, status_code=400)
            if (
                request.url.path in SINGLE_ARCHIVE_UPLOAD_PATHS
                and body_bytes > settings.max_upload_bytes + MULTIPART_OVERHEAD_BYTES
            ):
                return JSONResponse({"detail": "upload_too_large"}, status_code=413)
            # Multipart parsing first spools the request, then _save_upload copies
            # the selected part into a controlled temporary archive. Reserve both.
            required_bytes = body_bytes * 2 + settings.min_free_bytes
            if shutil.disk_usage(settings.staging_dir).free < required_bytes:
                return JSONResponse({"detail": "insufficient_storage"}, status_code=507)
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
        with tempfile.TemporaryDirectory(prefix="gpb-export-", dir=settings.staging_dir) as temp_dir:
            root = Path(temp_dir)
            export_path = root / filename
            await _save_upload(
                export,
                export_path,
                max_upload_bytes=settings.max_upload_bytes,
                min_free_bytes=settings.min_free_bytes,
            )
            owner_map_path: Path | None = None
            if owner_map is not None and owner_map.filename:
                owner_map_path = root / "owner-map.json"
                await _save_upload(
                    owner_map,
                    owner_map_path,
                    max_upload_bytes=settings.max_upload_bytes,
                    min_free_bytes=settings.min_free_bytes,
                )
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
        with tempfile.TemporaryDirectory(prefix="gpb-files-", dir=settings.staging_dir) as temp_dir:
            root = Path(temp_dir)
            archive_path = root / filename
            await _save_upload(
                archive,
                archive_path,
                max_upload_bytes=settings.max_upload_bytes,
                min_free_bytes=settings.min_free_bytes,
            )
            try:
                result = ingest_archive(
                    db,
                    archive_path,
                    source_type="owner_project_archive",
                    limits=archive_limits,
                    staging_dir=settings.staging_dir,
                    project_id=project_id,
                    project_name=project_name,
                )
            except (ValueError, zipfile.BadZipFile) as exc:
                if str(exc) == "insufficient_storage":
                    raise HTTPException(507, "insufficient_storage") from exc
                raise HTTPException(422, f"ingest_failed:{exc}") from exc
        db.audit(user.subject, "api_ingest_project_files", result["run_id"], None, {"source_name": filename})
        return {"status": "completed", "run": result, "stats": db.stats()}

    @app.post("/api/ingest/manus-backup")
    async def api_ingest_manus_backup(
        backup: UploadFile = File(...),
        project_id: str | None = Form(None),
        project_name: str | None = Form(None),
        user: Principal = Depends(principal),
    ) -> dict[str, object]:
        filename = Path(backup.filename or "tasks-data.manustask").name
        if not filename.lower().endswith(".manustask"):
            raise HTTPException(422, "backup_must_be_manustask")
        if bool((project_id or "").strip()) != bool((project_name or "").strip()):
            raise HTTPException(422, "project_id_and_project_name_must_be_supplied_together")
        with tempfile.TemporaryDirectory(prefix="gpb-manus-", dir=settings.staging_dir) as temp_dir:
            root = Path(temp_dir)
            backup_path = root / filename
            await _save_upload(
                backup,
                backup_path,
                max_upload_bytes=settings.max_upload_bytes,
                min_free_bytes=settings.min_free_bytes,
            )
            try:
                result = ingest_archive(
                    db,
                    backup_path,
                    source_type="manus_task_backup",
                    limits=archive_limits,
                    staging_dir=settings.staging_dir,
                    project_id=project_id,
                    project_name=project_name,
                )
            except (ValueError, zipfile.BadZipFile) as exc:
                if str(exc) == "insufficient_storage":
                    raise HTTPException(507, "insufficient_storage") from exc
                raise HTTPException(422, f"ingest_failed:{exc}") from exc
        db.audit(user.subject, "api_ingest_manus_backup", result["run_id"], None, {"source_name": filename})
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
