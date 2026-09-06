from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from app.latex_metadata import representation_from_latex
from app.source_evidence import SourceRepresentation


_COMMENT = re.compile(r"(?<!\\)%.*$", re.MULTILINE)
_REF_PATTERNS: tuple[tuple[re.Pattern[str], tuple[str, ...], bool], ...] = (
    (re.compile(r"\\(?:input|include)\s*\{([^}]+)\}"), (".tex",), True),
    (
        re.compile(r"\\includegraphics\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}"),
        (".pdf", ".png", ".jpg", ".jpeg", ".eps", ".svg"),
        True,
    ),
    (re.compile(r"\\(?:bibliography|addbibresource)\s*\{([^}]+)\}"), (".bib",), True),
    (re.compile(r"\\usepackage\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}"), (".sty",), False),
    (re.compile(r"\\documentclass\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}"), (".cls",), False),
)


@dataclass(frozen=True)
class LatexClosure:
    entry: Path
    root: Path
    files: tuple[Path, ...]
    unresolved: tuple[str, ...]
    digest: str

    @property
    def complete(self) -> bool:
        return not self.unresolved

    @property
    def relative_files(self) -> tuple[str, ...]:
        return tuple(path.relative_to(self.root).as_posix() for path in self.files)


def _inside_root(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolve_target(base_dir: Path, root: Path, target: str, exts: tuple[str, ...]) -> Path | None:
    raw = target.strip()
    if not raw:
        return None

    for base in (base_dir, root):
        candidate = (base / raw).resolve()
        candidates = [candidate]
        if not candidate.suffix:
            candidates.extend(Path(str(candidate) + ext) for ext in exts)
        for item in candidates:
            if not _inside_root(item, root):
                continue
            if item.is_file():
                return item
    return None


def _looks_explicitly_local(target: str) -> bool:
    target = target.strip()
    return (
        "/" in target
        or "\\" in target
        or target.startswith(".")
        or target.endswith(".sty")
        or target.endswith(".cls")
    )


def closure_digest(files: Iterable[Path], root: Path) -> str:
    root = root.resolve()
    h = hashlib.sha256()
    for path in sorted((p.resolve() for p in files), key=lambda p: p.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        content_digest = hashlib.sha256(path.read_bytes()).digest()
        h.update(len(relative).to_bytes(4, "big"))
        h.update(relative)
        h.update(content_digest)
    return h.hexdigest()


def latex_closure(entry: Path, *, root: Path | None = None) -> LatexClosure:
    """Compute the transitive local artifact closure for a LaTeX entry point.

    Mandatory local references (`input`, `include`, graphics and bibliography)
    are unresolved when absent. Bare class/package names are treated as toolchain
    dependencies unless they resolve locally or are explicitly path-like.

    Paths that escape `root` are never followed; they remain unresolved.
    """
    entry = entry.resolve()
    root = (root or entry.parent).resolve()
    if not entry.is_file():
        raise FileNotFoundError(entry)
    if not _inside_root(entry, root):
        raise ValueError("LaTeX entry must be inside closure root")

    seen: set[Path] = set()
    stack = [entry]
    unresolved: set[str] = set()

    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)

        if current.suffix.lower() not in {".tex", ".sty", ".cls"}:
            continue

        body = _COMMENT.sub("", current.read_text(encoding="utf-8", errors="replace"))
        for pattern, exts, mandatory in _REF_PATTERNS:
            for match in pattern.finditer(body):
                for target in match.group(1).split(","):
                    target = target.strip()
                    if not target:
                        continue
                    hit = _resolve_target(current.parent, root, target, exts)
                    if hit is not None:
                        stack.append(hit)
                        continue
                    if mandatory or _looks_explicitly_local(target):
                        unresolved.add(target)

    ordered = tuple(sorted(seen, key=lambda path: path.relative_to(root).as_posix()))
    return LatexClosure(
        entry=entry,
        root=root,
        files=ordered,
        unresolved=tuple(sorted(unresolved)),
        digest=closure_digest(ordered, root),
    )


def representation_from_latex_file(
    *,
    entry: Path,
    root: Path | None = None,
    locator: str | None = None,
    **kwargs,
) -> SourceRepresentation:
    """Build a closure-aware `LATEX_SOURCE` representation from disk."""
    closure = latex_closure(entry, root=root)
    content = closure.entry.read_text(encoding="utf-8", errors="replace")
    return representation_from_latex(
        content=content,
        locator=locator or closure.entry.as_uri(),
        closure_complete=closure.complete,
        closure_digest=closure.digest,
        closure_files=closure.relative_files,
        unresolved_references=closure.unresolved,
        **kwargs,
    )
