from __future__ import annotations

import re

from app.source_evidence import RepresentationType, SourceRepresentation


_DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b")
_ORCID_RE = re.compile(r"\b\d{4}-\d{4}-\d{4}-\d{3}[\dX]\b", re.IGNORECASE)


def _command_value(content: str, command: str) -> str | None:
    match = re.search(rf"\\{re.escape(command)}\s*\{{([^{{}}]*)\}}", content, re.DOTALL)
    if not match:
        return None
    value = " ".join(match.group(1).split())
    return value or None


def _version_value(content: str) -> str | None:
    direct = _command_value(content, "version")
    if direct:
        return direct

    patterns = (
        r"\\newcommand\s*\{\\version\}\s*\{([^{}]+)\}",
        r"\\renewcommand\s*\{\\version\}\s*\{([^{}]+)\}",
        r"\\def\s*\\version\s*\{([^{}]+)\}",
    )
    for pattern in patterns:
        match = re.search(pattern, content, re.DOTALL)
        if match:
            value = " ".join(match.group(1).split())
            if value:
                return value
    return None


def representation_from_latex(
    *,
    content: str,
    locator: str,
    captured_at: str | None = None,
    official_version: bool = False,
    repo: str | None = None,
    commit_sha: str | None = None,
    commit_verified: bool = False,
    release_tag: str | None = None,
    tag_verified: bool = False,
    manifest_sha256: str | None = None,
    manifest_verified: bool = False,
    canonical_url: str | None = None,
    canonical_verified: bool = False,
    signature_verified: bool = False,
) -> SourceRepresentation:
    """Create structured evidence from a preserved LaTeX source.

    A `.tex` source is independent structured evidence. It is promoted to strong
    official-version evidence only when `official_version=True` and at least one
    immutable provenance anchor is explicitly verified by its adapter/caller.
    """
    locator_path = locator.split("?", 1)[0].lower()
    if not locator_path.endswith(".tex"):
        raise ValueError("LaTeX evidence locator must end with .tex")

    metadata: dict[str, object] = {
        "source_format": "tex",
        "official_version": official_version,
        "commit_verified": commit_verified,
        "tag_verified": tag_verified,
        "manifest_verified": manifest_verified,
        "canonical_verified": canonical_verified,
        "signature_verified": signature_verified,
    }

    title = _command_value(content, "title")
    author = _command_value(content, "author")
    version = _version_value(content)
    doi_match = _DOI_RE.search(content)
    orcid_match = _ORCID_RE.search(content)

    optional = {
        "title": title,
        "author": author,
        "version": version,
        "doi": doi_match.group(0) if doi_match else None,
        "orcid": orcid_match.group(0).upper() if orcid_match else None,
        "repo": repo,
        "commit_sha": commit_sha,
        "release_tag": release_tag,
        "manifest_sha256": manifest_sha256,
        "canonical_url": canonical_url,
    }
    metadata.update({key: value for key, value in optional.items() if value is not None})

    return SourceRepresentation.from_text(
        kind=RepresentationType.LATEX_SOURCE,
        locator=locator,
        content=content,
        metadata=metadata,
        captured_at=captured_at,
    )
