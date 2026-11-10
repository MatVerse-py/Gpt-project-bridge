"""Read-only retrieval from the integrated external memory; no automatic execution."""
from __future__ import annotations
import os
import re
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .auth import Principal, require_capability

router = APIRouter(prefix="/memory",tags=["memory"])


class MemoryQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1,max_length=512)
    provider: str | None = Field(default=None,pattern=r"^(Claude|GPT|DeepSeek)$")
    limit: int = Field(default=8,ge=1,le=20)
    include_text: bool = False
    chars: int = Field(default=1600,ge=100,le=6000)


def _redact(text: str) -> str:
    text = re.sub("n"+"ey"+"sec","[redacted]",text,flags=re.I)
    text = re.sub(r"\b(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]{12,}","[redacted credential]",text)
    text = re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9_.-]{16,}",r"\1[redacted credential]",text)
    text = re.sub(r"""(?ix)((?:api[_ -]?key|access[_ -]?token|password|secret[_ -]?key)\s*[:=]\s*["']?)[^\s"'\},]{8,}""",
                  r"\1[redacted credential]",text)
    return text


def search_memory(req: MemoryQuery) -> list[dict]:
    configured = os.environ.get("MATVERSE_MEMORY_DB","")
    if not configured: raise RuntimeError("integrated memory database is not configured")
    path = Path(configured).resolve()
    if not path.is_file(): raise RuntimeError("integrated memory database is not available")
    words = re.findall(r"[^\W_]+",req.query,flags=re.UNICODE)
    if not words: raise ValueError("query must include searchable terms")
    # User input is literal terms, never SQL or raw FTS operators.
    match = " AND ".join('"'+w+'"' for w in words[:24])
    conn = sqlite3.connect(path.as_uri()+"?mode=ro",uri=True,timeout=3)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    try:
        params = [match]
        origin = ""
        if req.provider:
            origin = " AND r.provider=?";params.append(req.provider)
        rows = conn.execute("""SELECT r.id,r.provider,r.kind,r.role,r.title,r.timestamp,
            r.conversation_uuid,r.message_uuid,r.locator,r.body,bm25(search) score
            FROM search s JOIN search_chunks ch ON ch.id=s.rowid JOIN records r ON r.id=ch.record_id
            WHERE search MATCH ? AND r.kind!='content_block_thinking'
            AND r.role NOT IN ('tool_metadata','thinking')
            """+origin+" ORDER BY score LIMIT 1000",params)
        result, seen = [], set()
        for row in rows:
            if row["id"] in seen: continue
            seen.add(row["id"])
            item = {k:_redact(row[k]) if isinstance(row[k],str) else row[k]
                    for k in ("id","provider","kind","role","title","timestamp","conversation_uuid","message_uuid","locator")}
            source_id = os.environ.get("MATVERSE_MEMORY_SOURCE_ID", "integrated-export")
            item["source_ref"] = f"corpus:{source_id}:record:{row['id']}"
            item["nature"] = "archived_reference_not_current_instruction"
            if req.include_text:
                body = _redact(row["body"])
                term = words[0].casefold()
                at = body.casefold().find(term)
                lo = max(0,at-300) if at>=0 else 0
                item["excerpt"] = body[lo:lo+req.chars]
                item["truncated"] = lo>0 or len(body)>lo+req.chars
            result.append(item)
            if len(result)>=req.limit:break
        return result
    finally:
        conn.close()


@router.post("/search")
def memory_search(req: MemoryQuery, principal: Principal = Depends(require_capability("memory:read"))):
    if req.include_text and not principal.allows("memory:content"):
        raise HTTPException(403,detail="memory text requires memory:content")
    try:
        hits = search_memory(req)
    except (RuntimeError,sqlite3.Error):
        raise HTTPException(503,detail="integrated memory database is unavailable or incompatible") from None
    except ValueError as exc:
        raise HTTPException(422,detail=str(exc)) from None
    return {"results":hits,"read_by":principal.principal_id,"mode":"read_only",
            "automatic_provider_exposure":False}
