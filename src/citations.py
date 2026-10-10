"""Reference list builder behind the Citation Librarian agent.

The agent proposes titles and a one-line "why read this" note per source; this
module owns the facts. Every url, publisher, date and quote comes from the
evidence table, never from the model, so a weak or failed agent cannot
introduce a bad link. Cited sources keep the report's [n] numbering; sources
the research crew found but the report did not cite follow as further reading.
"""

from __future__ import annotations

from urllib.parse import urlparse


TIER_LABELS = {1: "Primary / official", 2: "Reputable press", 3: "Other"}


def _domain(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _evidence_sources(evidence: dict) -> dict[str, dict]:
    """url -> metadata gathered from every note in the evidence table."""
    sources: dict[str, dict] = {}
    for claim in evidence.get("claims", []):
        for note in claim.get("supports", []) or []:
            url = (note.get("url") or "").strip()
            if not url:
                continue
            src = sources.setdefault(url, {
                "url": url,
                "title": "",
                "publisher": "",
                "date": None,
                "tier": note.get("source_tier") or 3,
                "quote": "",
                "supports": [],
            })
            src["title"] = src["title"] or note.get("title") or ""
            src["publisher"] = src["publisher"] or note.get("publisher") or ""
            src["date"] = src["date"] or note.get("date")
            src["tier"] = min(src["tier"], note.get("source_tier") or 3)
            src["quote"] = src["quote"] or (note.get("verbatim_quote") or "")[:300]
            statement = (claim.get("statement") or "").strip()
            if statement and statement not in src["supports"]:
                src["supports"].append(statement)
    return sources


def build_references(evidence: dict, cited: list[dict] | None = None,
                     agent_notes: list[dict] | None = None) -> list[dict]:
    """Merge editor-cited refs, librarian notes and all other evidence sources.

    Returns refs ordered cited-first (report numbering preserved) then further
    reading, each with: url, title, publisher, date, domain, tier, quote,
    supports, note, role ("cited" | "further_reading").
    """
    sources = _evidence_sources(evidence)
    notes = {(n.get("url") or "").strip(): n for n in (agent_notes or []) if isinstance(n, dict)}
    out: list[dict] = []
    used: set[str] = set()

    def add(url: str, role: str, hint: dict) -> None:
        if not url or url in used or url not in sources and role == "further_reading":
            return
        base = sources.get(url, {"url": url, "tier": 3, "quote": "", "supports": []})
        agent = notes.get(url, {})
        used.add(url)
        out.append({
            "url": url,
            "title": hint.get("title") or agent.get("title") or base.get("title") or url,
            "publisher": hint.get("publisher") or base.get("publisher") or _domain(url),
            "date": hint.get("date") or base.get("date"),
            "domain": _domain(url),
            "tier": base.get("tier", 3),
            "quote": base.get("quote", ""),
            "supports": base.get("supports", [])[:3],
            "note": str(agent.get("note") or "")[:240],
            "role": role,
            "source_type": TIER_LABELS.get(base.get("tier", 3), "Other"),
        })

    for ref in cited or []:
        url = (ref.get("url") or "").strip()
        if url in sources or not sources:  # never admit a url the research crew did not find
            add(url, "cited", ref)
    for url in sorted(sources, key=lambda u: (sources[u]["tier"], u)):
        add(url, "further_reading", {})
    _fill_titles(out)
    return out


def _fill_titles(refs: list[dict], limit: int = 20) -> None:
    """Replace bare-URL titles with the real page title (from the page cache when verified already)."""
    todo = [r for r in refs if r["title"] == r["url"]][:limit]
    if not todo:
        return
    from concurrent.futures import ThreadPoolExecutor

    from . import tools

    def title_of(ref: dict) -> str:
        try:
            return (tools.fetch_page(ref["url"]).get("title") or "").strip()[:160]
        except Exception:  # noqa: BLE001 - a missing title never blocks the list
            return ""

    try:
        # default max_chars on purpose: the page cache is keyed by url, and claim
        # verification reads the same entry, so it must hold the full text
        with ThreadPoolExecutor(max_workers=6) as pool:
            for ref, title in zip(todo, pool.map(title_of, todo, timeout=45)):
                if title:
                    ref["title"] = title
    except Exception:  # noqa: BLE001 - slow pages just keep their url as the title
        pass
