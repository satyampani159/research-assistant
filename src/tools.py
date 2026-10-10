"""I2 — search + fetch tools. Frozen signatures (implementation.md §0.1).

Every Serper call goes through `_serper_post`, which owns the four things that
protect the quota: a TTL file cache, a module-level credit counter, a
`max_searches` budget, and tenacity backoff on 429/5xx.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup
from . import progress
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

SERPER_BASE = "https://google.serper.dev"
CACHE_DIR = Path(os.getenv("TOOL_CACHE_DIR", ".cache/serper"))
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "86400"))
HTTP_TIMEOUT = 20
FIXTURE_DIR = Path(os.getenv("SERPER_FIXTURE_DIR", "fixtures/serper"))


def fixtures_enabled() -> bool:
    """Offline mode: serve Serper calls from committed fixtures (no key, no spend)."""
    if os.getenv("SERPER_FIXTURES", "").lower() in {"1", "true", "yes"}:
        return True
    return not os.getenv("SERPER_API_KEY", "")

# ---------------------------------------------------------------- budget ----

class BudgetExhausted(RuntimeError):
    """Raised when max_searches is hit. Callers degrade, never crash."""


class _Budget:
    def __init__(self, limit: int | None = None) -> None:
        self.limit = limit if limit is not None else int(os.getenv("MAX_SEARCHES", "40"))
        self.spent = 0

    def reset(self, limit: int | None = None) -> None:
        if limit is not None:
            self.limit = limit
        self.spent = 0

    def take(self) -> None:
        if self.spent >= self.limit:
            raise BudgetExhausted(
                f"Serper budget exhausted: {self.spent}/{self.limit} queries used"
            )
        self.spent += 1


BUDGET = _Budget()


def credits_spent() -> int:
    """Serper credits used by this process (cache hits are free)."""
    return BUDGET.spent


def reset_budget(limit: int | None = None) -> None:
    BUDGET.reset(limit)


# ----------------------------------------------------------------- cache ----

def _cache_path(kind: str, key: str) -> Path:
    digest = hashlib.sha256(f"{kind}:{key}".encode()).hexdigest()[:32]
    return CACHE_DIR / f"{kind}-{digest}.json"


def _cache_get(path: Path) -> Any | None:
    try:
        st = path.stat()
    except OSError:
        return None
    if time.time() - st.st_mtime > CACHE_TTL_SECONDS:
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _cache_put(path: Path, value: Any) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False))
    except OSError:
        pass  # cache is an optimisation, never a hard dependency


# --------------------------------------------------------------- serper -----

class SerperError(RuntimeError):
    pass


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=20),
    retry=retry_if_exception_type((requests.exceptions.Timeout, requests.exceptions.ConnectionError, SerperError)),
    reraise=True,
)
def _serper_post(endpoint: str, payload: dict) -> dict:
    key = os.getenv("SERPER_API_KEY", "")
    if not key:
        raise SerperError("SERPER_API_KEY is not set")
    resp = requests.post(
        f"{SERPER_BASE}/{endpoint}",
        headers={"X-API-KEY": key, "Content-Type": "application/json"},
        json=payload,
        timeout=HTTP_TIMEOUT,
    )
    if resp.status_code == 429:
        raise SerperError("Serper 429: quota exhausted or rate limited")
    if resp.status_code >= 500:
        raise SerperError(f"Serper {resp.status_code}")
    if resp.status_code >= 400:
        raise SerperError(f"Serper {resp.status_code}: {resp.text[:200]}")
    return resp.json()


def _fixture_post(endpoint: str, query: str, num: int) -> dict | None:
    """Pick the committed fixture whose topics best match `query`."""
    if not FIXTURE_DIR.is_dir():
        return None
    best, best_score = None, -1
    for path in sorted(FIXTURE_DIR.glob(f"{endpoint}-*.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        haystack = json.dumps(data.get("topics", [])) + " " + path.stem
        score = len({w for w in re.findall(r"[a-z0-9]{4,}", query.lower())
                     if w in haystack.lower()})
        if score > best_score:
            best, best_score = data, score
    return best


def _cached_post(endpoint: str, payload: dict) -> dict:
    """Cache lookup → fixture (offline mode) → budget → live call → cache store."""
    path = _cache_path(endpoint, json.dumps(payload, sort_keys=True))
    hit = _cache_get(path)
    if hit is not None:
        return hit
    if fixtures_enabled():
        data = _fixture_post(endpoint, payload.get("q", ""), payload.get("num", 8))
        if data is not None:
            return data
        raise SerperError(
            f"offline fixture mode: no {endpoint} fixture matched {payload.get('q')!r}. "
            f"Put one in {FIXTURE_DIR} or set SERPER_API_KEY."
        )
    BUDGET.take()
    data = _serper_post(endpoint, payload)
    _cache_put(path, data)
    return data


# ------------------------------------------------------- public tool API ----

def search_web(query: str, num: int = 8, gl: str = "us") -> list[dict]:
    """[{title, url, snippet}] — organic results from Serper /search."""
    progress.emit("tool", tool="web search", detail=query)
    data = _cached_post("search", {"q": query, "num": num, "gl": gl})
    out = []
    for item in data.get("organic", [])[:num]:
        out.append(
            {
                "title": item.get("title", ""),
                "url": item.get("link", ""),
                "snippet": item.get("snippet", ""),
            }
        )
    return out


def search_news(query: str, num: int = 8) -> list[dict]:
    """[{title, url, snippet, source, date}] — Serper /news."""
    progress.emit("tool", tool="news search", detail=query)
    data = _cached_post("news", {"q": query, "num": num})
    out = []
    for item in data.get("news", [])[:num]:
        out.append(
            {
                "title": item.get("title", ""),
                "url": item.get("link", ""),
                "snippet": item.get("snippet", ""),
                "source": item.get("source", ""),
                "date": item.get("date", ""),
            }
        )
    return out


def search_scholar(query: str, num: int = 8) -> list[dict]:
    """[{title, authors, year, cited_by, url}] — Serper /scholar.

    The stock crewai SerperDevTool only covers /search and /news; this is why
    the scholar endpoint is hand-rolled.
    """
    progress.emit("tool", tool="scholar search", detail=query)
    data = _cached_post("scholar", {"q": query, "num": num})
    out = []
    for item in data.get("organic", [])[:num]:
        info = item.get("publicationInfo") or {}
        raw_authors = info.get("authors", "") if isinstance(info, dict) else ""
        if isinstance(raw_authors, list):
            names = [
                a.get("name", "") if isinstance(a, dict) else str(a) for a in raw_authors
            ]
            authors = ", ".join(n for n in names if n)
        else:
            authors = str(raw_authors or "")
        out.append(
            {
                "title": item.get("title", ""),
                "authors": authors,
                "year": (info.get("year", "") if isinstance(info, dict) else ""),
                "cited_by": item.get("citedBy", 0) or 0,
                "url": item.get("link", ""),
            }
        )
    return out


# ---------------------------------------------------------------- fetch -----

_DROP_TAGS = ("script", "style", "nav", "header", "footer", "aside", "form", "noscript", "iframe")
_DROP_PATTERNS = re.compile(
    r"(cookie|newsletter|subscribe|advertis|sponsor|related-articles|social-share|breadcrumb)",
    re.I,
)


class UnsafeURL(ValueError):
    """The model asked us to fetch something that is not a public web page."""


def assert_public_url(url: str) -> None:
    """Refuse non-http(s) URLs and hosts that resolve to private/internal addresses.

    URLs are chosen by an LLM from search results, so the fetcher must not be
    usable to reach the Docker network (n8n, the API itself, metadata services).
    """
    import ipaddress
    import socket
    from urllib.parse import urlparse

    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise UnsafeURL(f"only http(s) urls may be fetched: {url[:80]}")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise requests.exceptions.ConnectionError(f"cannot resolve {parts.hostname}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise UnsafeURL(f"refusing to fetch non-public address {ip} for {parts.hostname}")


MAX_REDIRECTS = 4


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=1, max=5),
    retry=retry_if_exception_type((requests.exceptions.Timeout, requests.exceptions.ConnectionError)),
    reraise=True,
)
def _http_get(url: str) -> str:
    # Redirects are followed by hand so every hop is checked, not just the first.
    for _ in range(MAX_REDIRECTS + 1):
        assert_public_url(url)
        resp = requests.get(
            url,
            timeout=HTTP_TIMEOUT,
            headers={"User-Agent": "research-assistant/1.0 (+https://serper.dev)"},
            allow_redirects=False,
        )
        if resp.is_redirect and resp.headers.get("Location"):
            from urllib.parse import urljoin

            url = urljoin(url, resp.headers["Location"])
            continue
        resp.raise_for_status()
        return resp.text
    raise requests.exceptions.TooManyRedirects(f"more than {MAX_REDIRECTS} redirects")


PAGE_FIXTURE_DIR = Path(os.getenv("PAGE_FIXTURE_DIR", "fixtures/pages"))


def _fixture_page(url: str) -> str | None:
    """Committed page HTML for offline verification runs."""
    if not PAGE_FIXTURE_DIR.is_dir():
        return None
    index = PAGE_FIXTURE_DIR / "index.json"
    if index.is_file():
        try:
            mapping = json.loads(index.read_text())
        except (OSError, json.JSONDecodeError):
            mapping = {}
        name = mapping.get(url)
        if name:
            path = PAGE_FIXTURE_DIR / name
            return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None
    digest = hashlib.sha256(url.encode()).hexdigest()[:16]
    path = PAGE_FIXTURE_DIR / f"{digest}.html"
    if path.is_file():
        return path.read_text(encoding="utf-8", errors="replace")
    return None


def _html_for(url: str) -> str:
    """Live HTTP, unless a committed fixture exists or no SERPER key is configured."""
    if fixtures_enabled():
        html = _fixture_page(url)
        if html is None:
            raise SerperError(
                f"offline fixture mode: no committed page for {url}. "
                f"Add it to {PAGE_FIXTURE_DIR} or set SERPER_API_KEY."
            )
        return html
    return _http_get(url)


def fetch_page(url: str, max_chars: int = 6000) -> dict:
    """{title, text, retrieved_at} — nav/script/ads stripped, cached."""
    from datetime import datetime, timezone

    progress.emit("tool", tool="fetching page", detail=url)
    path = _cache_path("page", url)
    hit = _cache_get(path)
    if hit is not None:
        return hit

    html = _html_for(url)
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.get_text(strip=True) if soup.title else "")

    for tag in soup(_DROP_TAGS):
        tag.decompose()

    blocks: list[str] = []
    for el in soup.find_all(["p", "li", "h1", "h2", "h3", "h4", "blockquote", "td"]):
        text = el.get_text(" ", strip=True)
        if not text or _DROP_PATTERNS.search(text):
            continue
        if len(text) > 40 and _DROP_PATTERNS.search(el.get("class", [""])[0] if el.get("class") else ""):
            continue
        blocks.append(text)

    # de-dup while preserving order; joined paragraphs read better to the LLM
    seen: set[str] = set()
    kept = [b for b in blocks if not (b in seen or seen.add(b))]

    result = {
        "title": title,
        "text": "\n\n".join(kept)[:max_chars],
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "url": url,
    }
    _cache_put(path, result)
    return result


# ------------------------------------------------------------- utilities ----

def domain_of(url: str) -> str:
    """Bare hostname, lowercased, www-stripped. Used for diversity checks."""
    if isinstance(url, dict):
        url = url.get("url", "") or ""
    m = re.match(r"https?://([^/]+)", (url if isinstance(url, str) else str(url)) or "")
    if not m:
        return ""
    host = m.group(1).lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def tier_of(url: str) -> int:
    """1 = primary/official, 2 = reputable press, 3 = other."""
    host = domain_of(url)
    if not host:
        return 3
    if host.endswith((".gov", ".edu", ".int")) or host.startswith(("docs.", "arxiv.org")):
        return 1
    if host.endswith((".org", ".io", ".ai", ".dev", ".tech", ".com")):
        return 2
    return 3


__all__ = [
    "BudgetExhausted",
    "SerperError",
    "BUDGET",
    "credits_spent",
    "reset_budget",
    "search_web",
    "search_news",
    "search_scholar",
    "fetch_page",
    "fixtures_enabled",
    "domain_of",
    "tier_of",
]