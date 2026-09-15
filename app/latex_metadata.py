from __future__ import annotations

import re
from collections.abc import Iterable

from app.source_evidence import RepresentationType, SourceRepresentation


_DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b")
_ORCID_RE = re.compile(r"\b\d{4}-\d{4}-\d{4}-\d{3}[\dX]\b", re.IGNORECASE)
_REF_RE = re.compile(
    r"\\(?:input|include)\s*\{([^}]+)\}"
    r"|\\includegraphics\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}"
    r"|\\(?:bibliography|addbibresource)\s*\{([^}]+)\}",
    re.DOTALL,
)


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


def _direct_references(content: str) -> tuple[str, ...]:
    refs: set[str] = set()
    for match in _REF_RE.finditer(content):
        raw = next((group for group in match.groups() if group), "")
        refs.update(part.strip() for part in raw.split(",") if part.strip())
    return tuple(sorted(refs))


def _claimed_identifiers(content: str) -> dict[str, tuple[str, ...]]:
    claims: dict[str, tuple[str, ...]] = {}
    dois = tuple(sorted(set(match.group(0) for match in _DOI_RE.finditer(content))))
    orcids = tuple(sorted(set(match.group(0).upper() for match in _ORCID_RE.finditer(content))))
    if dois:
        claims["doi"] = dois
    if orcids:
        claims["orcid"] = orcids
    return claims


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
    closure_complete: bool | None = None,
    closure_digest: str | None = None,
    closure_files: Iterable[str] | None = None,
    unresolved_references: Iterable[str] | None = None,
    representation_kind: RepresentationType = RepresentationType.LATEX_SOURCE,
    external_timestamp_verified: bool = False,
) -> SourceRepresentation:
    """Create structured evidence from a preserved LaTeX source.

    Identifiers *written inside* TeX are claims, not resolved identifiers. DOI,
    ORCID and similar publication/identity claims require an independent lookup.

    Official-version authority also requires a complete source closure and a
    verified immutable provenance anchor. A lone `.tex` extension is never
    enough to establish officiality.
    """
    if representation_kind not in {RepresentationType.LATEX_SOURCE, RepresentationType.ARXIV_EPRINT_SOURCE}:
        raise ValueError("representation_kind must be LATEX_SOURCE or ARXIV_EPRINT_SOURCE")

    locator_path = locator.split("?", 1)[0].lower()
    if not (locator_path.endswith(".tex") or representation_kind is RepresentationType.ARXIV_EPRINT_SOURCE):
        raise ValueError("LaTeX evidence locator must end with .tex")

    direct_refs = _direct_references(content)
    explicit_unresolved = tuple(sorted(set(unresolved_references or ())))
    if closure_complete is None:
        # A single-file source can be complete when it has no local artifact
        # references. If references exist and no closure scan was supplied, fail
        # closed: the representation is only a fragment until resolved.
        closure_complete = not direct_refs
        if direct_refs and not explicit_unresolved:
            explicit_unresolved = direct_refs

    metadata: dict[str, object] = {
        "source_format": "tex",
        "official_version": official_version,
        "commit_verified": commit_verified,
        "tag_verified": tag_verified,
        "manifest_verified": manifest_verified,
        "canonical_verified": canonical_verified,
        "signature_verified": signature_verified,
        "external_timestamp_verified": external_timestamp_verified,
        "closure_complete": bool(closure_complete),
        "direct_references": direct_refs,
        "unresolved_references": explicit_unresolved,
        "claimed_identifiers": _claimed_identifiers(content),
    }

    title = _command_value(content, "title")
    author = _command_value(content, "author")
    version = _version_value(content)

    optional = {
        "title": title,
        "author": author,
        "version": version,
        "repo": repo,
        "commit_sha": commit_sha,
        "release_tag": release_tag,
        "manifest_sha256": manifest_sha256,
        "canonical_url": canonical_url,
        "closure_digest": closure_digest,
    }
    metadata.update({key: value for key, value in optional.items() if value is not None})
    if closure_files is not None:
        metadata["closure_files"] = tuple(closure_files)

    return SourceRepresentation.from_text(
        kind=representation_kind,
        locator=locator,
        content=content,
        metadata=metadata,
        captured_at=captured_at,
    )


def representation_from_arxiv_latex(
    *,
    content: str,
    locator: str,
    captured_at: str | None = None,
    **kwargs,
) -> SourceRepresentation:
    """Create an arXiv-custodied source representation.

    The adapter still must provide independently verified arXiv/publication
    metadata. Merely naming a file or URL "arXiv" does not create publication
    authority.
    """
    return representation_from_latex(
        content=content,
        locator=locator,
        captured_at=captured_at,
        representation_kind=RepresentationType.ARXIV_EPRINT_SOURCE,
        **kwargs,
    )
