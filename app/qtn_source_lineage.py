"""Source work/version separation for QTN evidence triage.

This is a *pointer and metadata* layer. It never downloads, authenticates, hashes
or verifies scientific article bodies. Work-level deduplication is not evidence
that two revisions make the same claims.
"""
from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import json
import re
from urllib.parse import urlsplit


def pointer_identity(url: str) -> dict:
    identity = dict(work_id=None, version_id=None, version_tag=None,
                    version_pointer=None, pointer_kind="UNRECOGNIZED")
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            return identity
        if parsed.port is not None or parsed.query or parsed.fragment:
            return identity
    except (ValueError, TypeError):
        return identity
    host = parsed.hostname.lower()
    path = parsed.path.lower()
    if host in ("arxiv.org", "www.arxiv.org"):
        m = re.fullmatch(r"/(?:abs|pdf)/(\d{4}\.\d{4,5})(v[1-9]\d*)?(?:\.pdf)?/?", path)
        if m:
            work, version = m.groups()
            identity.update(
                work_id=f"arxiv:{work}",
                version_id=f"arxiv:{work}{version}" if version else None,
                version_tag=version,
                version_pointer=f"https://arxiv.org/abs/{work}{version}" if version else None,
                pointer_kind="ARXIV_PREPRINT")
    elif host == "datatracker.ietf.org":
        m = re.fullmatch(
            r"/doc/(?:html/)?(draft-[a-z0-9-]+?)(?:-(\d{2})|/(\d{2}))?/?", path)
        if m:
            draft, trailing, separate = m.groups()
            version = trailing or separate
            identity.update(
                work_id=f"ietf-draft:{draft}",
                version_id=f"ietf-draft:{draft}-{version}" if version else None,
                version_tag=version,
                version_pointer=(f"https://datatracker.ietf.org/doc/{draft}/{version}/"
                                 if version else None),
                pointer_kind="IETF_INTERNET_DRAFT")
    elif host in ("rfc-editor.org", "www.rfc-editor.org"):
        m = re.fullmatch(r"/rfc/rfc(\d+)(?:\.html|\.txt)?/?", path)
        if m:
            number=m.group(1)
            identity.update(
                work_id=f"rfc:{number}", version_id=f"rfc:{number}",
                version_tag=f"rfc{number}",
                version_pointer=f"https://www.rfc-editor.org/rfc/rfc{number}",
                pointer_kind="RFC_PUBLICATION")
    return identity


def metadata_fingerprint(source: object) -> str:
    """Fingerprint of *supplied metadata*, not verified article body."""
    payload = {
        "source": getattr(source, "source", None),
        "title": getattr(source, "title", None),
        "url": getattr(source, "url", None),
        "summary": getattr(source, "summary", None),
    }
    return sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":")).encode("utf-8")).hexdigest()


def version_inventory(source_items: list[object]) -> dict[str, list[dict]]:
    """All observed versions, even if an upstream alert deduplicates by work."""
    records: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for source in source_items:
        pointer = pointer_identity(getattr(source, "url", None))
        work = pointer["work_id"]
        if work is not None:
            version = pointer["version_id"] or "UNPINNED_LATEST"
            records[work][version].add(metadata_fingerprint(source))
    return {
        work: [{"version_id": version,
                "metadata_fingerprints": sorted(fingerprints),
                "metadata_conflict": len(fingerprints) > 1}
               for version, fingerprints in sorted(groups.items())]
        for work, groups in records.items()
    }
