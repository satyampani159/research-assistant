"""Report organizer: a deterministic layout pass over any finished report.

Runs on both the editor's output and the evidence-only fallback so every
report has the same shape: title, at-a-glance box, contents, sections in a
sensible order, deduplicated bullets with merged citations, and no leaked
error text. It never rewrites prose or invents claims; it only restructures.
"""

from __future__ import annotations

import re
from datetime import datetime

_ERROR_BLOCK = re.compile(r"_The editorial crew did not complete.*?(?:finished report|evidence)[^_]*\._", re.S)
_ERROR_TITLE = re.compile(r"^#\s*Report unavailable.*$", re.M | re.I)
# Internal pipeline vocabulary. A reader of the report should never see any of it.
_LEAK = re.compile(
    r"guardrail|traceback|crewai|site-packages|/app/src|kickoff|editorial crew|research crew|"
    r"did not complete|after \d+ retries|\bException\b|\bError:|raw evidence table|"
    r"evidence table|verification log|unverified_appendix|claim_id|rate limit|"
    r"quota|RESOURCE_EXHAUSTED|\(\d+/\d+\)\s*$|distinct domains",
    re.I,
)
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$")
_CITE = re.compile(r"\s*\[(\d+(?:\s*,\s*\d+)*)\]")

# lower index = earlier in the report; unknown sections keep their order in the middle
_FIRST = ("executive summary", "summary", "overview", "key findings", "findings")
_LAST = ("gaps", "where sources disagree", "contradictions", "limitations", "unverified", "method")


def _rank(title: str) -> int:
    t = title.lower()
    if any(k in t for k in _FIRST):
        return 0
    if any(k in t for k in _LAST):
        return 2
    return 1


def _clean(md: str) -> str:
    md = _ERROR_BLOCK.sub("", md)
    md = _ERROR_TITLE.sub("", md)
    # drop any stray traceback that survived
    out, skipping = [], False
    for line in md.splitlines():
        if line.startswith("Traceback (most recent call last)"):
            skipping = True
            continue
        if skipping:
            if line.strip() == "" or _HEADING.match(line) or _BULLET.match(line):
                skipping = False
            else:
                continue
        out.append(line)
    return _drop_leaks("\n".join(out))


_OWN_METHODS = re.compile(r"^\s*\**methods?( note)?\**\s*:", re.I)


def _drop_leaks(md: str) -> str:
    """Remove paragraphs/bullets that mention internal failures or pipeline machinery."""
    kept: list[str] = []
    for block in re.split(r"\n\s*\n", md):
        lines = block.splitlines()
        if not any(l.strip() for l in lines) or _OWN_METHODS.match(block):
            continue
        if all(_BULLET.match(l) for l in lines if l.strip()):
            lines = [l for l in lines if not _LEAK.search(l)]
            if any(l.strip() for l in lines):
                kept.append("\n".join(lines))
        elif _HEADING.match(lines[0]):
            kept.append(lines[0] if _LEAK.search("\n".join(lines[1:])) else block)
        elif not _LEAK.search(block):
            kept.append(block)
    return "\n\n".join(kept)


def _split(md: str) -> tuple[str, list[tuple[str, list[str]]], list[str]]:
    """-> (title, [(section heading, body lines)], preamble lines)"""
    title = ""
    preamble: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    for line in md.splitlines():
        m = _HEADING.match(line)
        if m and len(m.group(1)) == 1 and not title:
            title = m.group(2).strip()
        elif m and len(m.group(1)) == 2:
            sections.append((m.group(2).strip(), []))
        elif sections:
            sections[-1][1].append(line)
        else:
            preamble.append(line)
    return title, sections, preamble


def _norm(text: str) -> str:
    return re.sub(r"\W+", " ", _CITE.sub("", text)).strip().lower()


def _merge_cites(*texts: str) -> list[int]:
    nums: list[int] = []
    for t in texts:
        for m in _CITE.finditer(t):
            for n in re.split(r"\s*,\s*", m.group(1)):
                if int(n) not in nums:
                    nums.append(int(n))
    return sorted(nums)


def _tidy_body(lines: list[str]) -> list[str]:
    """Dedupe bullets (merging their citations), collapse blank runs."""
    out: list[str] = []
    seen: dict[str, int] = {}
    for line in lines:
        b = _BULLET.match(line)
        if not b:
            if line.strip() or (out and out[-1].strip()):
                out.append(line.rstrip())
            continue
        text = b.group(1).strip()
        key = _norm(text)
        if key in seen:
            i = seen[key]
            body = _CITE.sub("", out[i][2:]).rstrip()
            nums = _merge_cites(out[i], text)
            out[i] = "- " + body + "".join(f" [{n}]" for n in nums)
            continue
        seen[key] = len(out)
        out.append("- " + text)
    while out and not out[-1].strip():
        out.pop()
    while out and not out[0].strip():
        out.pop(0)
    return out


def _year_range(references: list[dict]) -> tuple[int, str]:
    """(how many sources carry a date, 'YYYY-YYYY' or '')."""
    years = []
    for r in references:
        m = re.search(r"(19|20)\d{2}", str(r.get("date") or ""))
        if m:
            years.append(int(m.group(0)))
    if not years:
        return 0, ""
    lo, hi = min(years), max(years)
    return len(years), str(lo) if lo == hi else f"{lo}-{hi}"


def _method_note(references: list[dict], claims: list[dict], status: str, thin: list[str] | None = None) -> str:
    """What was and was not checked, stated plainly. Never claims more than was done."""
    cited = [r for r in references if r.get("role") != "further_reading"] or references
    n = len(cited)
    dated, span = _year_range(cited)
    if status == "degraded" or not claims:
        text = (f"This is a provisional summary of findings gathered from {n} sources. The findings were "
                "not fact-checked against their pages, so treat figures as unconfirmed until you have "
                "read them at the source.")
    else:
        total = len(claims)
        ok = sum(1 for c in claims if c.get("verdict") == "VERIFIED")
        text = (f"This report draws on {n} sources. {ok} of {total} claims were checked against the page "
                "they cite; the rest could not be confirmed and are hedged or left out. A check confirms "
                "what the cited page says, not that the page itself is correct, so key figures deserve "
                "a second source.")
    if claims and len(claims) < 4:
        text += (f" Only {len(claims)} claim{'s' if len(claims) != 1 else ''} survived, so this is a starting "
                 "point for reading the sources, not a survey of the topic.")
    if thin:
        names = "; ".join(thin[:4])
        text += f" Source coverage is thin for: {names} (fewer than two independent sources)."
    if n:
        text += (f" {dated} of {n} sources show a publication date ({span})." if dated
                 else " The sources carry no publication dates, so judge how current they are from the links.")
    return text


def organize(md: str, *, topic: str = "", audience: str = "", references: list[dict] | None = None,
             confidence_grade: str = "", claims: list[dict] | None = None, status: str = "complete",
             thin_sections: list[str] | None = None) -> str:
    """Return `md` restructured into the standard report layout."""
    title, sections, preamble = _split(_clean(md or ""))
    title = title or topic or "Research findings"

    # text before the first H2 (other than blank lines) becomes an introduction
    intro = _tidy_body(preamble)
    if intro and not any("summary" in s.lower() for s, _ in sections):
        all_bullets = all(_BULLET.match(l) for l in intro if l.strip())
        sections.insert(0, ("Key findings" if all_bullets else "Executive summary", intro))
        intro = []

    cleaned = [(h, _tidy_body(b)) for h, b in sections]
    cleaned = [(h, b) for h, b in cleaned if b]
    if not any("method" in h.lower() for h, _ in cleaned) and (references or claims):
        cleaned.append(("Method and limits", [_method_note(references or [], claims or [], status, thin_sections)]))
    cleaned = sorted(cleaned, key=lambda s: _rank(s[0]))  # stable

    n_sources = len(references or [])
    facts = []
    if audience:
        facts.append(f"**Audience:** {audience}")
    if n_sources:
        facts.append(f"**Sources:** {n_sources}")
    if confidence_grade:
        facts.append(f"**Confidence:** {confidence_grade}")
    if status == "degraded":
        facts.append("**Status:** provisional, not fact-checked")
    _, span = _year_range(references or [])
    if span:
        facts.append(f"**Source dates:** {span}")
    facts.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d')}")

    out = [f"# {title}", "", "> " + "  |  ".join(facts), ""]
    if intro:
        out += intro + [""]

    if len(cleaned) >= 3:
        out += ["## Contents", ""]
        out += [f"{i}. {h}" for i, (h, _) in enumerate(cleaned, 1)]
        out += [""]

    for h, body in cleaned:
        out += [f"## {h}", ""]
        # a lone run of bullets reads better numbered, so findings can be referred to
        if all(_BULLET.match(l) or not l.strip() for l in body) and sum(bool(l.strip()) for l in body) > 1 \
                and _rank(h) == 0:
            n = 0
            for l in body:
                if l.strip():
                    n += 1
                    out.append(f"{n}. {l[2:]}")
        else:
            out += body
        out += [""]

    return "\n".join(out).rstrip() + "\n"
