"""Clean evidence-only report, used when the writing stage cannot finish.

No error text ever goes in here: the report must read as a normal document.
Why it degraded is reported separately (response.status).

Findings are grouped under the research questions the planner asked, so the
reader gets themes rather than one flat list. Claims whose sources disagree are
marked instead of being presented as settled.
"""

from __future__ import annotations

import re


def _question_lookup(evidence: dict) -> tuple[dict[str, str], list[str]]:
    """sub-question id or text -> heading text, plus headings in plan order."""
    by_key: dict[str, str] = {}
    order: list[str] = []
    for sq in evidence.get("sub_questions", []) or []:
        if not isinstance(sq, dict):
            continue
        text = str(sq.get("question") or "").strip()
        sid = str(sq.get("sub_question_id") or sq.get("id") or "").strip()
        if not text:
            continue
        heading = text.rstrip("?. ").strip()
        heading = heading[:1].upper() + heading[1:]
        order.append(heading)
        for key in (sid, text):
            if key:
                by_key[key] = heading
    return by_key, order


def _group_key(claim: dict, by_key: dict[str, str]) -> str:
    for note in claim.get("supports", []) or []:
        sq = str(note.get("sub_question_id") or "").strip()
        if sq in by_key:
            return by_key[sq]
        # the researcher sometimes writes the question itself instead of its id
        for text, heading in by_key.items():
            if len(sq) > 12 and (sq.startswith(text[:40]) or text.startswith(sq[:40])):
                return heading
    return ""


_NUMBER = re.compile(r"\d[\d,.]*")


def _conflicting_figures(claims: list[dict]) -> set[int]:
    """Indexes of claims that say the same thing with different numbers (41% vs 91%)."""
    shape: dict[str, list[int]] = {}
    for i, c in enumerate(claims):
        text = _NUMBER.sub("#", c.get("statement", "").lower())
        shape.setdefault(re.sub(r"\W+", " ", text).strip(), []).append(i)
    flagged: set[int] = set()
    for idx in shape.values():
        figures = {tuple(_NUMBER.findall(claims[i].get("statement", ""))) for i in idx}
        if len(idx) > 1 and len(figures) > 1:
            flagged.update(idx)
    return flagged


def build(evidence: dict) -> dict:
    topic = evidence.get("topic", "")
    by_key, plan_order = _question_lookup(evidence)

    all_claims = evidence.get("claims", [])
    figure_conflicts = {id(all_claims[i]) for i in _conflicting_figures(all_claims)}
    groups: dict[str, list[dict]] = {}
    for claim in evidence.get("claims", []):
        groups.setdefault(_group_key(claim, by_key), []).append(claim)

    ordered = [h for h in plan_order if h in groups]
    ordered += [h for h in groups if h and h not in ordered]
    if "" in groups:
        ordered.append("")  # ungrouped claims last

    refs: list[dict] = []
    index: dict[str, int] = {}
    lines = [f"# {topic}" if topic else "# Research findings", ""]
    seen_statements: set[str] = set()

    for heading in ordered:
        bullets: list[str] = []
        for claim in groups[heading]:
            statement = claim.get("statement", "").strip()
            key = re.sub(r"\W+", " ", statement).lower()
            if not statement or key in seen_statements:
                continue
            seen_statements.add(key)
            nums: list[int] = []
            for note in claim.get("supports", []) or []:
                url = note.get("url", "")
                if not url:
                    continue
                if url not in index:
                    refs.append({
                        "url": url,
                        "title": note.get("title") or note.get("publisher") or url,
                        "publisher": note.get("publisher", ""),
                        "date": note.get("date"),
                    })
                    index[url] = len(refs)
                if index[url] not in nums:
                    nums.append(index[url])
            cites = "".join(f" [{n}]" for n in nums)
            disputed = (" _(sources disagree)_"
                        if claim.get("conflicts") or id(claim) in figure_conflicts else "")
            bullets.append(f"- {statement}{cites}{disputed}")
        if bullets:
            fallback_title = "Additional evidence" if plan_order else "Key findings"
            lines += [f"## {heading or fallback_title}", ""] + bullets + [""]

    for title, key in (("Gaps in the evidence", "gaps"), ("Where sources disagree", "contradictions")):
        items = [str(i) for i in evidence.get(key, []) or []]
        if items:
            lines += [f"## {title}", ""] + [f"- {i}" for i in items] + [""]
    return {
        "markdown": "\n".join(lines).rstrip() + "\n",
        "references": refs,
        "confidence_grade": "C",
        "unverified_appendix": [],
    }
