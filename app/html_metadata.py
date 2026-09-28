from __future__ import annotations

from html.parser import HTMLParser
from typing import Any
import re

from app.source_evidence import RepresentationType, SourceRepresentation


_ORCID_RE = re.compile(r"\b\d{4}-\d{4}-\d{4}-\d{3}[\dX]\b", re.IGNORECASE)


class _MetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.metadata: dict[str, Any] = {}
        self._title_parts: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = {k: v for k, v in attrs if v is not None}
        if tag == "meta":
            name = data.get("name") or data.get("property")
            content = data.get("content")
            if not name or content is None:
                return
            if name == "citation_doi":
                self.metadata["doi"] = content
            elif name == "citation_author":
                self.metadata["author"] = content
            elif name == "citation_title":
                self.metadata["title"] = content
            elif name in {"description", "og:description"}:
                self.metadata.setdefault("description", content)
                match = _ORCID_RE.search(content)
                if match:
                    self.metadata.setdefault("orcid", match.group(0))
            elif name == "citation_pdf_url":
                self.metadata["pdf_url"] = content
            elif name == "citation_abstract_html_url":
                self.metadata.setdefault("canonical_url", content)
        elif tag == "link":
            rel = data.get("rel", "")
            href = data.get("href")
            if href and rel == "canonical":
                self.metadata["canonical_url"] = href
            elif href and data.get("type") == "application/pdf":
                self.metadata.setdefault("pdf_url", href)
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)

    def finalize(self) -> dict[str, Any]:
        if "title" not in self.metadata:
            title = " ".join(part.strip() for part in self._title_parts if part.strip())
            if title:
                self.metadata["title"] = title
        return self.metadata


def extract_html_metadata(html: str) -> dict[str, Any]:
    parser = _MetadataParser()
    parser.feed(html)
    parser.close()
    return parser.finalize()


def representation_from_html(
    *,
    html: str,
    locator: str,
    kind: RepresentationType = RepresentationType.SAVED_HTML,
    captured_at: str | None = None,
) -> SourceRepresentation:
    if kind not in {RepresentationType.LIVE_HTML, RepresentationType.SAVED_HTML}:
        raise ValueError("HTML representation kind must be LIVE_HTML or SAVED_HTML")
    return SourceRepresentation.from_text(
        kind=kind,
        locator=locator,
        content=html,
        metadata=extract_html_metadata(html),
        captured_at=captured_at,
    )
