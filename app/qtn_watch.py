from __future__ import annotations

import hashlib
import html
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Iterable
from urllib.parse import urljoin
from xml.etree import ElementTree

import httpx

USER_AGENT = "MatVerse-QTN-Watch/1.0 (+https://github.com/MatVerse-py/Gpt-project-bridge)"
DEFAULT_THRESHOLD = 0.68
DEFAULT_TIMEOUT = 20.0
DEFAULT_MAX_AGE_DAYS = 60

ARXIV_QUERY = " OR ".join(
    [
        'all:"quantum network"',
        'all:"quantum internet"',
        'all:"quantum repeater"',
        'all:"quantum memory"',
        'all:"quantum teleportation"',
        'all:"quantum key distribution"',
        'all:"quantum error correction"',
        'all:"quantum tomography"',
        'all:"quantum compiler"',
        'all:"quantum random number"',
        'all:"hybrid quantum classical"',
        'all:"post-quantum cryptography"',
    ]
)

QTN_RULES: dict[str, tuple[str, ...]] = {
    "QTN-001": ("quantum network", "deployed fiber", "commercial fiber", "hybrid network", "network interoperability", "testbed"),
    "QTN-002": ("routing", "route selection", "path selection", "multipath", "network path"),
    "QTN-003": ("composite index", "network index", "quality index", "performance index"),
    "QTN-004": ("full-stack", "quantum network platform", "prototype network", "quantum internet prototype", "network testbed"),
    "QTN-005": ("legal record", "electronic record", "digital signature", "post-quantum signature", "ml-dsa"),
    "QTN-006": ("proof protocol", "verification protocol", "formal verification", "proof-of"),
    "QTN-007": ("semantic enforcement", "policy enforcement", "post-quantum", "pqc", "ml-kem", "ml-dsa"),
    "QTN-008": ("error correction", "qec", "logical qubit", "logical error", "fault tolerant", "fault-tolerant"),
    "QTN-009": ("channel simulator", "network simulator", "quantum simulation", "noise model", "channel model"),
    "QTN-010": ("teleportation", "teleport", "teleported"),
    "QTN-011": ("post-quantum", "pqc", "ml-kem", "ml-dsa", "slh-dsa", "kyber", "dilithium"),
    "QTN-012": ("fidelity", "state verification", "verification engine", "bell violation", "bell inequality"),
    "QTN-013": ("entanglement distribution", "distributed entanglement", "entanglement swapping", "entangled photon", "entanglement"),
    "QTN-014": ("network optimization", "network optimizer", "routing optimization", "path optimization"),
    "QTN-015": ("quantum memory", "memory lifetime", "memory coherence", "memory node"),
    "QTN-016": ("tomography", "state reconstruction", "quantum state learning"),
    "QTN-017": ("hybrid quantum-classical", "hybrid quantum classical", "variational", "quantum-classical optimization"),
    "QTN-018": ("benchmark", "benchmarking", "performance evaluation", "conformance test"),
    "QTN-019": ("topology", "network graph", "network architecture", "multiplane architecture"),
    "QTN-020": ("compiler", "compilation", "transpiler", "circuit mapping", "circuit optimization"),
    "QTN-021": ("repeater", "quantum repeater", "repeater node"),
    "QTN-022": ("monitoring", "telemetry", "observability", "network measurement", "dashboard"),
    "QTN-023": ("qrng", "quantum random", "random number generator", "randomness generation"),
    "QTN-024": ("qkd", "quantum key distribution", "bb84", "e91"),
    "QTN-025": ("simulation benchmark", "simulation framework", "network simulation", "simulator benchmark"),
    "QTN-026": ("synchronization", "synchronisation", "key sync", "clock sync", "timing synchronization", "time transfer"),
    "QTN-027": ("resource allocation", "resource manager", "resource scheduling", "scheduling", "resource orchestration"),
    "QTN-028": ("quantum internet", "protocol stack", "quantum protocol", "control plane", "data plane", "quantum network architecture", "rfc 9340"),
}

SCOPE_TERMS = tuple(sorted({term for values in QTN_RULES.values() for term in values} | {"quantum", "post-quantum", "pqc"}))

STANDARDIZATION_TERMS = (
    "standardization",
    "standardisation",
    "standardized",
    "standardised",
    "technical standard",
    "industry standard",
    "interoperability standard",
    "standards framework",
)

HIGH_IMPACT_TERMS = (
    "rfc ",
    *STANDARDIZATION_TERMS,
    "demonstrat",
    "deploy",
    "field trial",
    "commercial fiber",
    "prototype",
    "interoperab",
    "breakthrough",
    "record-breaking",
    "world record",
    "fault-tolerant",
    "logical qubit",
    "final award",
)

SOURCE_AUTHORITY = {
    "arXiv": 0.26,
    "NIST": 0.42,
    "IETF": 0.46,
    "QIA": 0.36,
}


@dataclass(frozen=True)
class SourceItem:
    source: str
    title: str
    url: str
    published_at: str | None = None
    summary: str = ""


@dataclass(frozen=True)
class Finding:
    source: str
    title: str
    url: str
    published_at: str | None
    qtn_ids: tuple[str, ...]
    score: float
    impact_type: str
    recommendation: str
    validation_class: str
    digest: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class _LinkParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self._href: str | None = None
        self._parts: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self._href = urljoin(self.base_url, href)
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            text = " ".join(data.split())
            if text:
                self._parts.append(text)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._href is None:
            return
        title = " ".join(self._parts).strip()
        if title:
            self.links.append((title, self._href))
        self._href = None
        self._parts = []


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _digest(item: SourceItem) -> str:
    payload = json.dumps(
        {"source": item.source, "title": _normalized(item.title).lower(), "url": item.url},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _infer_published_at(title: str, url: str) -> str | None:
    exact_url = re.search(r"/(20\d{2})/(0[1-9]|1[0-2])/(0[1-9]|[12]\d|3[01])/", url)
    if exact_url:
        year, month, day = map(int, exact_url.groups())
        try:
            return datetime(year, month, day, tzinfo=timezone.utc).isoformat()
        except ValueError:
            return None

    title_dmy = re.match(r"\s*(0?[1-9]|[12]\d|3[01])[.\-/](0?[1-9]|1[0-2])[.\-/](20\d{2})\b", title)
    if title_dmy:
        day, month, year = map(int, title_dmy.groups())
        try:
            return datetime(year, month, day, tzinfo=timezone.utc).isoformat()
        except ValueError:
            return None

    title_ymd = re.match(r"\s*(20\d{2})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b", title)
    if title_ymd:
        year, month, day = map(int, title_ymd.groups())
        try:
            return datetime(year, month, day, tzinfo=timezone.utc).isoformat()
        except ValueError:
            return None

    month_url = re.search(r"/(20\d{2})/(0[1-9]|1[0-2])(?:/|$)", url)
    if month_url:
        year, month = map(int, month_url.groups())
        return datetime(year, month, 1, tzinfo=timezone.utc).isoformat()
    return None


def _within_age(published_at: str | None, *, now: datetime | None = None) -> bool:
    if not published_at:
        return False
    try:
        published = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    reference = now or datetime.now(timezone.utc)
    max_age_days = max(1, int(os.environ.get("QTN_WATCH_MAX_AGE_DAYS", str(DEFAULT_MAX_AGE_DAYS))))
    age_seconds = (reference - published.astimezone(timezone.utc)).total_seconds()
    return -86400 <= age_seconds <= max_age_days * 86400


def classify_qtn(item: SourceItem) -> tuple[str, ...]:
    haystack = f"{item.title} {item.summary} {item.url}".lower()
    matches = [qtn_id for qtn_id, terms in QTN_RULES.items() if any(term in haystack for term in terms)]
    return tuple(matches)


def impact_type(item: SourceItem) -> str:
    text = f"{item.title} {item.summary} {item.url}".lower()
    if item.source == "IETF" or "rfc " in text or any(term in text for term in STANDARDIZATION_TERMS):
        return "STANDARDIZATION"
    if any(term in text for term in ("demonstrat", "field trial", "deployed", "commercial fiber", "prototype network", "experimental realization")):
        return "EXTERNAL_DEMONSTRATION"
    if any(term in text for term in ("award", "funding", "manufactur", "infrastructure", "testbed deployment")):
        return "INFRASTRUCTURE"
    if any(term in text for term in ("post-quantum", "pqc", "ml-kem", "ml-dsa", "qkd")):
        return "SECURITY_MIGRATION"
    return "RESEARCH_ADVANCE"


def recommendation_for(item: SourceItem) -> str:
    impact = impact_type(item)
    if impact == "STANDARDIZATION":
        return "REBASE_AGAINST_EXTERNAL_STANDARD"
    if impact == "EXTERNAL_DEMONSTRATION":
        return "ADD_EXTERNAL_BASELINE_AND_RETEST"
    if impact == "INFRASTRUCTURE":
        return "REVIEW_ENGINEERING_READINESS"
    if impact == "SECURITY_MIGRATION":
        return "UPDATE_CRYPTOGRAPHIC_BASELINE"
    return "REVIEW_FOR_DELTA_AND_BENCHMARK"


def score_item(item: SourceItem, qtn_ids: tuple[str, ...]) -> float:
    if not qtn_ids:
        return 0.0
    text = f"{item.title} {item.summary} {item.url}".lower()
    authority = SOURCE_AUTHORITY.get(item.source, 0.20)
    coverage = min(0.30, 0.08 * len(qtn_ids))
    impact = 0.24 if any(term in text for term in HIGH_IMPACT_TERMS) else 0.08
    specificity = 0.12 if any(
        term in text
        for term in (
            "quantum network",
            "quantum internet",
            "repeater",
            "qkd",
            "ml-kem",
            "ml-dsa",
            "teleport",
            "error correction",
            "quantum memory",
            "quantum compiler",
            "quantum random",
        )
    ) else 0.04
    return round(min(1.0, authority + coverage + impact + specificity), 3)


def evaluate(items: Iterable[SourceItem], threshold: float = DEFAULT_THRESHOLD) -> list[Finding]:
    findings: list[Finding] = []
    seen_urls: set[str] = set()
    for item in items:
        if item.url in seen_urls:
            continue
        seen_urls.add(item.url)
        qtn_ids = classify_qtn(item)
        score = score_item(item, qtn_ids)
        if score < threshold:
            continue
        findings.append(
            Finding(
                source=item.source,
                title=_normalized(item.title),
                url=item.url,
                published_at=item.published_at,
                qtn_ids=qtn_ids,
                score=score,
                impact_type=impact_type(item),
                recommendation=recommendation_for(item),
                validation_class="EXTERNAL_RELEVANCE_NOT_MATVERSE_VALIDATION",
                digest=_digest(item),
            )
        )
    return sorted(findings, key=lambda f: (-f.score, f.source, f.title.lower()))


def _client() -> httpx.Client:
    timeout = float(os.environ.get("QTN_WATCH_TIMEOUT", str(DEFAULT_TIMEOUT)))
    return httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})


def fetch_arxiv(client: httpx.Client) -> list[SourceItem]:
    response = client.get(
        "https://export.arxiv.org/api/query",
        params={
            "search_query": ARXIV_QUERY,
            "start": "0",
            "max_results": os.environ.get("QTN_WATCH_ARXIV_MAX", "40"),
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        },
    )
    response.raise_for_status()
    root = ElementTree.fromstring(response.text)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    items: list[SourceItem] = []
    for entry in root.findall("a:entry", ns):
        title = _normalized(entry.findtext("a:title", default="", namespaces=ns))
        summary = _normalized(entry.findtext("a:summary", default="", namespaces=ns))
        published = entry.findtext("a:published", default=None, namespaces=ns)
        url = ""
        for link in entry.findall("a:link", ns):
            if link.attrib.get("rel") == "alternate" and link.attrib.get("href"):
                url = link.attrib["href"]
                break
        if title and url and _within_age(published):
            items.append(SourceItem("arXiv", title, url, published, summary))
    return items


def _fetch_html_source(client: httpx.Client, *, source: str, url: str, max_links: int = 80) -> list[SourceItem]:
    response = client.get(url)
    response.raise_for_status()
    parser = _LinkParser(str(response.url))
    parser.feed(response.text)
    items: list[SourceItem] = []
    seen: set[str] = set()
    for title, link in parser.links:
        title = _normalized(title)
        if link in seen or not (8 <= len(title) <= 260):
            continue
        seen.add(link)
        scope_text = f"{title} {link}".lower()
        if not any(term in scope_text for term in SCOPE_TERMS):
            continue
        items.append(SourceItem(source, title, link, _infer_published_at(title, link)))
        if len(items) >= max_links:
            break
    return items


def fetch_nist(client: httpx.Client) -> list[SourceItem]:
    items = _fetch_html_source(
        client,
        source="NIST",
        url="https://www.nist.gov/news-events/news-updates/topic/249281",
        max_links=50,
    )
    return [item for item in items if _within_age(item.published_at)]


def fetch_ietf(client: httpx.Client) -> list[SourceItem]:
    items = _fetch_html_source(
        client,
        source="IETF",
        url="https://datatracker.ietf.org/doc/search?activedrafts=on&by=group&group=&name=quantum&sort=-date",
        max_links=80,
    )
    return [item for item in items if "/doc/draft-" in item.url]


def fetch_qia(client: httpx.Client) -> list[SourceItem]:
    items = _fetch_html_source(
        client,
        source="QIA",
        url="https://quantuminternetalliance.org/news/",
        max_links=50,
    )
    return [item for item in items if _within_age(item.published_at)]


FETCHERS = {
    "arXiv": fetch_arxiv,
    "NIST": fetch_nist,
    "IETF": fetch_ietf,
    "QIA": fetch_qia,
}


def collect() -> tuple[list[SourceItem], dict[str, str]]:
    items: list[SourceItem] = []
    errors: dict[str, str] = {}
    with _client() as client:
        for source, fetcher in FETCHERS.items():
            try:
                items.extend(fetcher(client))
            except Exception as exc:  # source isolation is deliberate; quorum is enforced by caller
                errors[source] = f"{type(exc).__name__}: {exc}"
    return items, errors


def render_markdown(findings: Iterable[Finding], errors: dict[str, str] | None = None) -> str:
    findings = list(findings)
    now = datetime.now(timezone.utc).isoformat()
    lines = [
        "# QTN Watch",
        "",
        f"Generated: `{now}`",
        "",
        "External developments are relevance/baseline signals only; they do not validate MatVerse claims.",
        "",
    ]
    if not findings:
        lines.append("No material new candidate met the configured threshold.")
    for finding in findings:
        qtn = ", ".join(finding.qtn_ids)
        lines.extend(
            [
                f"## {finding.title}",
                "",
                f"- Source: **{finding.source}**",
                f"- QTN: **{qtn}**",
                f"- Materiality: **{finding.score:.3f}**",
                f"- Impact: **{finding.impact_type}**",
                f"- Action: **{finding.recommendation}**",
                f"- Classification: `{finding.validation_class}`",
                f"- URL: {finding.url}",
                f"- Digest: `{finding.digest}`",
                f"<!-- qtn-watch:{finding.digest} -->",
                "",
            ]
        )
    if errors:
        lines.extend(["## Source errors", ""])
        for source, error in sorted(errors.items()):
            lines.append(f"- **{source}**: `{error}`")
        lines.append("")
    return "\n".join(lines)
