"""Small HTTP surface; local administration stays outside the network API."""
from __future__ import annotations

import json

from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import ValidationError

from .overlay_domain import OverlayDomain
from .overlay_protocol import MAX_WIRE_BYTES


def create_app(domain: OverlayDomain) -> FastAPI:
    app = FastAPI(title="MatVerse Overlay", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    def health():
        return {"status": "ready", "domain_id": domain.domain_id}

    @app.get("/v1/capabilities")
    def capabilities(challenge: str = Query(pattern=r"^[0-9a-f]{32}$")):
        return domain.discovery(challenge)

    @app.post("/v1/execute")
    async def execute(request: Request):
        length = request.headers.get("content-length")
        if length is None:
            raise HTTPException(411, "content_length_required")
        if not length.isdigit() or not 0 < int(length) <= MAX_WIRE_BYTES:
            raise HTTPException(413, "request_size_exceeded")
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise HTTPException(415, "json_required")
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > MAX_WIRE_BYTES:
                raise HTTPException(413, "request_size_exceeded")
        try:
            message = json.loads(raw)
            if not isinstance(message, dict):
                raise ValueError("object_required")
            # Run bounded computation and SQLite work off the event loop.
            from starlette.concurrency import run_in_threadpool
            return await run_in_threadpool(domain.receive, message)
        except PermissionError as exc:
            raise HTTPException(403, "overlay_authorization_rejected") from exc
        except (ValidationError, ValueError, KeyError, TypeError) as exc:
            raise HTTPException(422, "overlay_invalid_request") from exc
        except (RuntimeError, OSError) as exc:
            raise HTTPException(503, "overlay_execution_unavailable") from exc

    return app
