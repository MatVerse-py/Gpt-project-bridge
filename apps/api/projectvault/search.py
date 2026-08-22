from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote

from .db import Database

TOKEN = re.compile(r"[\wÀ-ÿ-]+", re.UNICODE)


def fts_query(user_query: str) -> str:
    terms = [term for term in TOKEN.findall(user_query) if term.strip("-")]
    if not terms:
        raise ValueError("Search query has no searchable terms")
    return " AND ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms[:20])


class KnowledgeService:
    def __init__(self, db: Database, public_base_url: str, max_results: int = 20):
        self.db = db
        self.public_base_url = public_base_url.rstrip("/")
        self.max_results = max_results

    def search(self, query: str, project_id: str | None = None, limit: int | None = None) -> dict[str, Any]:
        effective_limit = max(1, min(self.max_results, limit or self.max_results))
        rows = self.db.search(fts_query(query), effective_limit, project_id)
        return {
            "results": [
                {
                    "id": row["document_id"],
                    "title": f"{row['project_name']} — {row['title']}",
                    "url": f"{self.public_base_url}/documents/{quote(row['document_id'], safe='')}",
                }
                for row in rows
            ]
        }

    def fetch(self, document_id: str) -> dict[str, Any]:
        row = self.db.fetch(document_id)
        if row is None:
            raise KeyError(document_id)
        metadata = json.loads(row["metadata_json"])
        metadata.update({
            "project_id": row["project_id"],
            "project_name": row["project_name"],
            "conversation_id": row["conversation_id"],
            "attribution_basis": row["attribution_basis"],
            "source_hash": row["source_hash"],
            "updated_at_epoch": row["updated_at_epoch"],
        })
        return {
            "id": row["document_id"],
            "title": f"{row['project_name']} — {row['title']}",
            "text": row["body"],
            "url": f"{self.public_base_url}/documents/{quote(row['document_id'], safe='')}",
            "metadata": metadata,
        }
