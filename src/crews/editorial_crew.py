"""Crew 2 — EDITORIAL · Process.sequential.

Writer → Fact-checker → Editor, with the four deterministic guardrails from
Project.md §2.9 attached to the tasks. Also owns `run_repair`, the bounded
re-search pass that n8n triggers via POST /repair.

NB: deliberately no `from __future__ import annotations` in this module.
CrewAI validates guardrails with `inspect.signature()` without evaluating
string annotations, so a deferred `tuple[bool, Any]` return type is rejected.
"""

import json
import os
import time
from datetime import datetime, timezone
from typing import Any

from crewai import Agent, Crew, Process, Task
from crewai.tools import tool

from .. import progress, quality
from .. import tools as shared_tools
from ..config import llm, model_for_role
from ..citations import build_references
from ..contracts import CitationList, DraftReport, FinalReport, ResearchNote, VerificationLog
from ..prompts import (
    CITATION_LIBRARIAN,
    EDITOR,
    ENTAILMENT_PROMPT,
    FACT_CHECKER,
    REPAIR_RESEARCHER,
    WRITER,
    agent_prompt,
)

GUARDRAIL_MAX_RETRIES = 2
# Stay under free-tier per-minute limits (Gemini free = 15 RPM).
MAX_RPM = int(os.getenv("CREW_MAX_RPM", "10"))
_RETRYABLE = ("429", "RESOURCE_EXHAUSTED", "503", "500", "UNAVAILABLE", "high demand", "timed out",
              "overloaded", "rate limit", "quota", "Connection", "ServiceUnavailable", "InternalServerError")


# --------------------------------------------------------------- tools ------

@tool("fetch_page")
def fetch_page(url: str, max_chars: int = 6000) -> str:
    """Re-fetch a source page as clean text, for verification."""
    page = shared_tools.fetch_page(url, max_chars)
    return f"TITLE: {page['title']}\nRETRIEVED: {page['retrieved_at']}\n\n{page['text']}"


@tool("numeric_check")
def numeric_check(claim: str, source_text: str) -> str:
    """Compare every figure in a claim against a source text. JSON verdict."""
    return json.dumps(shared_tools.numeric_check(claim, source_text), ensure_ascii=False)


@tool("verify_claim")
def verify_claim(claim_id: str, text: str, url: str) -> str:
    """Verify one claim against its cited URL. JSON: verdict, confidence, quote."""
    return json.dumps(quality.verify_claim(claim_id, text, url), ensure_ascii=False)


EDITORIAL_TOOLS = [fetch_page, numeric_check, verify_claim]


# ------------------------------------------------- LLM entailment hook ------

_JUDGE_FALLBACKS = {"n": 0}


def _llm_entailment(claim: str, source_text: str) -> tuple[bool, str]:
    """Injected into quality.entailment so verdicts use a real model.

    Isolated in one function (D2.5) — if the call fails for any reason the
    lexical fallback in quality.py answers instead.
    """
    try:
        import litellm

        model = model_for_role("fact_checker")
        prompt = ENTAILMENT_PROMPT.format(claim=claim, source_text=source_text[:4000])
        resp = litellm.completion(model=model, messages=[{"role": "user", "content": prompt}])
        raw = resp.choices[0].message.content or "{}"
        start, end = raw.find("{"), raw.rfind("}")
        data = json.loads(raw[start:end + 1])
        return bool(data.get("entails")), str(data.get("reason", ""))[:200]
    except Exception as exc:  # noqa: BLE001 - never let the judge break the run
        _JUDGE_FALLBACKS["n"] += 1
        print(f"[verify] LLM judge failed ({str(exc)[:100]}); used word-overlap fallback", flush=True)
        return quality.lexical_entailment(claim, source_text)


# ----------------------------------------------------------- guardrails -----

def _as_dict(value) -> dict:
    """CrewAI hands over a pydantic model; tolerate a plain dict too."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    return value.model_dump()


def _draft(payload) -> dict:
    draft = getattr(payload, "pydantic", None)
    if draft is None:
        return {"sections": [], "raw": str(payload)[:2000]}
    return _normalize_draft(_as_dict(draft))


def _section_url(value) -> str:
    """Extract a URL string from str / {url,...} / nested junk."""
    from ..quality import _s as _qs
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("url", "link", "source"):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                return item
        return ""
    if isinstance(value, list):
        for item in value:
            url = _section_url(item)
            if url:
                return url
        return ""
    return _qs(value)


def _normalize_draft(draft: dict) -> dict:
    """Coerce a weak model's draft into the contract shape before validation.

    Ollama 8b emits cited_claims as [{claim_id...}], bodies as lists, and
    sources as [{url...}]. The guardrails validate the *normalized* draft so
    recoverable shape drift doesn't fail the whole run; genuinely missing
    content (too few domains, no ids at all) still fails honestly.
    """
    if not isinstance(draft, dict):
        return {"sections": []}
    sections = []
    for sec in draft.get("sections", []):
        if not isinstance(sec, dict):
            continue
        heading = _first(sec, "heading", "title", "section", "name", "header")
        sections.append({
            "heading": heading if isinstance(heading, str) else str(heading),
            "body": _body_text(_first(sec, "body", "content", "text", "markdown")),
            "cited_claims": _claim_ids(_first(sec, "cited_claims", "claims", "claim_ids", "citations")),
            "sources": [u for u in (_section_url(u) for u in (_first(sec, "sources", "urls", "references") or [])) if u],
        })
    draft = dict(draft)
    draft["sections"] = sections
    return draft


def _first(section: dict, *keys: str):
    """First non-empty value among alternate key spellings a weaker model may use."""
    for key in keys:
        value = section.get(key)
        if value not in (None, "", [], {}):
            return value
    return ""


def _body_text(value) -> str:
    """Section bodies arrive as str, list[str], or nested junk — join safely."""
    from ..quality import _s as _qs
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n\n".join(_body_text(v) for v in value)
    return _qs(value)


def _claim_ids(items) -> list[str]:
    """Normalize cited_claims entries to id strings.

    The Writer sometimes emits [{claim_id, ...}] objects instead of plain
    ids; set()/dict-key usage on raw dicts raises "unhashable type: 'dict'"
    and kills the editorial run. Coerce defensively at the boundary.
    """
    ids: list[str] = []
    for c in items or []:
        if isinstance(c, str):
            ids.append(c)
        elif isinstance(c, dict):
            for key in ("claim_id", "id", "statement"):
                if c.get(key):
                    ids.append(str(c[key]))
                    break
    return ids


def _evidence_domains(evidence: dict) -> dict[str, set[str]]:
    """claim_id -> distinct domains of the sources that support it."""
    out: dict[str, set[str]] = {}
    for c in evidence.get("claims", []):
        urls = [n.get("url", "") for n in (c.get("supports") or [])]
        out[str(c.get("claim_id", ""))] = {d for d in (shared_tools.domain_of(u) for u in urls) if d}
    return out


def _claim_urls(evidence: dict, claim_id: str) -> list[str]:
    for c in evidence.get("claims", []):
        if str(c.get("claim_id", "")) == claim_id:
            return [n.get("url", "") for n in (c.get("supports") or []) if n.get("url")]
    return []


def make_writer_guardrail(evidence: dict) -> callable:
    """Integrity checks on the draft: claim ids, citation coverage, uncited numbers.

    IMPORTANT: on success a CrewAI guardrail must hand back the output it was
    given. Returning a message string replaces the task's output with that
    string, which silently destroys the draft before the next agent reads it.

    Source diversity is deliberately NOT a failure here: a section resting on one
    source usually reflects thin evidence, which the writer cannot fix by retrying.
    It is measured by `thin_sections` and disclosed in the report instead.
    """
    by_claim = _evidence_domains(evidence)

    def _check(payload) -> tuple[bool, Any]:
        draft = _draft(payload)
        sections = [s for s in draft.get("sections", []) if isinstance(s, dict)]
        cited_ids = [c for s in sections for c in _claim_ids(s.get("cited_claims"))]
        total_claims = len(by_claim) if by_claim else len(set(cited_ids))

        results = [
            quality.all_claims_have_ids(draft),
            quality.citation_coverage(cited_ids, total_claims),
        ]
        body = "\n\n".join(_body_text(s.get("body", "")) for s in sections)
        results.append(quality.no_uncited_numbers(body))

        failures = [msg for ok, msg in results if not ok]
        if failures:
            shape = [(s.get("heading", "")[:30], len(s.get("body", "")), s.get("cited_claims", [])[:4]) for s in sections]
            print(f"[writer] draft rejected: {failures}; sections (heading, body chars, cited) = {shape}", flush=True)
            return False, " | ".join(failures)[:900]
        return True, payload

    return _check


def cap_grade(grade: str, n_claims: int, n_domains: int) -> str:
    """The editor grades verification; breadth of evidence caps it.

    Two claims from two pages can all be verified and still say little, so an A
    needs real breadth (6+ claims, 4+ domains) and a B at least 4 claims, 2 domains.
    """
    ceiling = "A" if (n_claims >= 6 and n_domains >= 4) else "B" if (n_claims >= 4 and n_domains >= 2) else "C"
    return max(grade if grade in ("A", "B", "C") else "C", ceiling)  # "C" > "B" > "A" alphabetically


def thin_sections(draft: dict, evidence: dict, min_domains: int = 2) -> list[str]:
    """Headings of sections whose cited claims come from fewer than `min_domains` domains."""
    thin = []
    for sec in draft.get("sections", []):
        if not isinstance(sec, dict):
            continue
        urls = [*sec.get("sources", []), *(u for cid in _claim_ids(sec.get("cited_claims")) for u in _claim_urls(evidence, cid))]
        domains = {shared_tools.domain_of(u) for u in urls} - {""}
        if sec.get("heading") and len(domains) < min_domains:
            thin.append(sec["heading"])
    return thin


writer_guardrail = make_writer_guardrail({})


def editor_guardrail(allowed_urls: list[str]) -> callable:
    """Zero hallucinated citations: every reference url must be real."""

    def _check(payload) -> tuple[bool, Any]:
        final = getattr(payload, "pydantic", None)
        # Blank-url entries are placeholders, not invented sources; they are stripped
        # from the final output. Any non-empty url must still be in the evidence table.
        refs = [r for r in _as_dict(final).get("references", []) if r.get("url")]
        unknown = [r.get("url", "") for r in refs if r.get("url") not in allowed_urls]
        if unknown:
            return False, f"references contain {len(unknown)} url(s) absent from the evidence table: {unknown[:3]}"
        if not refs:
            return False, "reference list is empty"
        return True, payload

    return _check


# ------------------------------------------------------------- factory ------

def build_editorial_crew(topic: str, audience: str, evidence: dict, depth: str = "standard") -> Crew:
    allowed_urls = sorted(
        {n.get("url", "") for c in evidence.get("claims", []) for n in (c.get("supports") or []) if n.get("url")}
    )
    evidence_json = json.dumps(evidence, ensure_ascii=False)[:60000]

    writer = Agent(
        role=WRITER["role"], goal=WRITER["goal"], backstory=WRITER["backstory"],
        tools=[], allow_delegation=False, llm=llm("writer"), verbose=False,
    )
    fact_checker = Agent(
        role=FACT_CHECKER["role"], goal=FACT_CHECKER["goal"], backstory=FACT_CHECKER["backstory"],
        tools=EDITORIAL_TOOLS, allow_delegation=False, llm=llm("fact_checker"), verbose=False,
    )
    editor = Agent(
        role=EDITOR["role"], goal=EDITOR["goal"], backstory=EDITOR["backstory"],
        tools=[], allow_delegation=False, llm=llm("editor"), verbose=False,
    )

    write_task = Task(
        description=agent_prompt(
            WRITER,
            f"TOPIC: {topic}\nAUDIENCE: {audience}\nDEPTH: {depth}\n"
            f"EVIDENCE TABLE (JSON, given below) — this is your entire source material.\n"
            f"{evidence_json}\n\n"
            f"Number the sources [1], [2], ... in first-appearance order. Every section "
            f"should draw on at least 2 distinct domains from the evidence table, and list the urls it relied on in `sources`.",
        ),
        expected_output="DraftReport JSON: {title, thesis, sections: [{heading, body, cited_claims[claim_id], sources[url]}]}",
        agent=writer,
        output_pydantic=DraftReport,
        guardrail=make_writer_guardrail(evidence),
        guardrail_max_retries=GUARDRAIL_MAX_RETRIES,
    )

    check_task = Task(
        description=agent_prompt(
            FACT_CHECKER,
            "DRAFT (JSON, given as context). For every claim in the draft:\n"
            "1. call verify_claim(claim_id, statement, cited_url)\n"
            "2. if it returns CONTRADICTED, call numeric_check on the figures to see "
            "which number is wrong and record the claim's real value if the source "
            "states it.\n"
            "3. if it returns VERIFIED, keep the verbatim evidence_quote it returned.\n"
            "Never issue a verdict you did not obtain from a tool call.",
        ),
        expected_output="VerificationLog JSON: {results: [ClaimResult, ...]} — one per draft claim",
        agent=fact_checker,
        context=[write_task],
        output_pydantic=VerificationLog,
    )

    edit_task = Task(
        description=agent_prompt(
            EDITOR,
            f"TOPIC: {topic}\nAUDIENCE: {audience}\n"
            "DRAFT and VERIFICATION LOG are in the context. Only these urls exist:\n"
            f"{json.dumps(allowed_urls, ensure_ascii=False)}\n\n"
            f"Walk the draft sentence by sentence against the log, then emit the final "
            f"report plus the numbered reference list and the confidence grade.",
        ),
        expected_output="FinalReport JSON: {markdown, references[], confidence_grade, unverified_appendix[]}",
        agent=editor,
        context=[write_task, check_task],
        output_pydantic=FinalReport,
        guardrail=editor_guardrail(allowed_urls),
        guardrail_max_retries=GUARDRAIL_MAX_RETRIES,
    )

    return Crew(
        agents=[writer, fact_checker, editor],
        tasks=[write_task, check_task, edit_task],
        process=Process.sequential,
        task_callback=progress.task_done,
        max_rpm=MAX_RPM,
        verbose=False,
    )


def _run_librarian(topic: str, evidence: dict, report_md: str) -> list[dict]:
    """Citation Librarian: titles + 'why read this' notes. Best effort, never fatal."""
    urls = sorted({n.get("url", "") for c in evidence.get("claims", [])
                   for n in (c.get("supports") or []) if n.get("url")})
    if not urls or os.getenv("CITATION_AGENT", "1") == "0":
        return []

    def _check(payload) -> tuple[bool, Any]:
        refs = _as_dict(getattr(payload, "pydantic", None)).get("references", [])
        # entries without a usable url are ignored later by build_references; only
        # an invented url is a reason to reject
        bad = [r.get("url") for r in refs if isinstance(r, dict) and r.get("url") and r.get("url") not in urls]
        return (False, f"urls not in the evidence table: {bad[:3]}") if bad else (True, payload)

    librarian = Agent(
        role=CITATION_LIBRARIAN["role"], goal=CITATION_LIBRARIAN["goal"],
        backstory=CITATION_LIBRARIAN["backstory"], tools=[], allow_delegation=False,
        llm=llm("synthesizer"), verbose=False,
    )
    task = Task(
        description=agent_prompt(
            CITATION_LIBRARIAN,
            f"TOPIC: {topic}\nURLS (the only ones that exist):\n{json.dumps(urls)}\n\n"
            f"EVIDENCE TABLE:\n{json.dumps(evidence, ensure_ascii=False)[:40000]}\n\n"
            f"REPORT:\n{report_md[:6000]}",
        ),
        expected_output="CitationList JSON: {references: [{url, title, note}]}",
        agent=librarian, output_pydantic=CitationList,
        guardrail=_check, guardrail_max_retries=1,
    )
    try:
        Crew(agents=[librarian], tasks=[task], process=Process.sequential,
             max_rpm=MAX_RPM, verbose=False).kickoff()
        out = task.output.pydantic if task.output is not None else None
        return _as_dict(out).get("references", [])
    except Exception as exc:  # noqa: BLE001 - references still build from the evidence table
        print(f"[citations] librarian failed, using evidence metadata only: {exc}", flush=True)
        return []


def _usage_tokens(crew: Crew) -> int:
    crew_total = getattr(getattr(crew, "usage_metrics", None), "total_tokens", 0) or 0
    if crew_total:
        return int(crew_total)
    total = 0
    for agent in [*getattr(crew, "agents", []), getattr(crew, "manager_agent", None)]:
        llm_obj = getattr(agent, "llm", None)
        stats = getattr(llm_obj, "usage_metrics", None) or {}
        total += sum(v for v in stats.values() if isinstance(v, (int, float)))
    return int(total)


def run_editorial(evidence: dict, topic: str, audience: str) -> dict:
    """I3. Returns {report_md, references, claims, verification, metrics}."""
    started = time.monotonic()
    quality.set_entailment_fn(_llm_entailment)
    _JUDGE_FALLBACKS["n"] = 0

    crew = build_editorial_crew(topic, audience, evidence)
    write_task, check_task, edit_task = crew.tasks[:3]
    failure = ""
    progress.begin_crew("Editorial crew", crew)
    try:
        crew.kickoff()
        final = edit_task.output.pydantic
        if final is None:
            raise RuntimeError("editor returned no structured output")
        final_data = final.model_dump()
    except Exception as exc:  # noqa: BLE001 - degrade to a bare evidence report
        if any(t in str(exc) for t in _RETRYABLE):
            raise  # let the API wait / fail over to another provider instead of degrading
        import traceback
        tb = traceback.format_exc(limit=12)
        failure = f"{type(exc).__name__}: {exc}\n{tb}"[:3000]
        print(f"[editorial] crew failed, degrading to evidence report:\n{failure}", flush=True)
        final_data = _fallback_report(evidence, f"{type(exc).__name__}: {exc}")
    finally:
        progress.end_crew()

    domains = {shared_tools.domain_of(n.get("url", "")) for c in evidence.get("claims", [])
               for n in (c.get("supports") or [])} - {""}
    final_data["confidence_grade"] = cap_grade(
        final_data.get("confidence_grade", "C"), len(evidence.get("claims", [])), len(domains))
    notes = _run_librarian(topic, evidence, final_data.get("markdown", ""))
    final_data["references"] = build_references(evidence, final_data.get("references", []), notes)

    verification = _verification_log(check_task)
    draft = _draft(write_task.output) if write_task.output is not None else {"sections": []}
    claims_out = _merge_claims(evidence, draft, verification, final_data)

    metrics = {
        "tokens": _usage_tokens(crew),
        "serper_credits": evidence.get("serper_credits", 0),
        "duration_s": round(time.monotonic() - started, 2),
        "repair_needed": any(c["verdict"] != "VERIFIED" for c in claims_out),
    }
    return {
        "report_md": final_data.get("markdown", ""),
        "references": [r for r in final_data.get("references", []) if r.get("url")],
        "claims": claims_out,
        "confidence_grade": final_data.get("confidence_grade", "C"),
        "unverified_appendix": final_data.get("unverified_appendix", []),
        "verification": {
            "checked": len(verification.results),
            "verified": len(verification.verified_ids()),
            "failed": len(verification.failed()),
            "results": [r.model_dump() for r in verification.results],
            "error": failure,
            "judge_fallbacks": _JUDGE_FALLBACKS["n"],
        },
        "degraded": bool(failure),
        "thin_sections": thin_sections(draft, evidence),
        "metrics": metrics,
    }


def _verification_log(check_task) -> VerificationLog:
    out = getattr(check_task, "output", None)
    log = getattr(out, "pydantic", None) if out is not None else None
    return log if log is not None else VerificationLog(results=[])


def _fallback_report(evidence: dict, reason: str = "") -> dict:
    from ..fallback_report import build

    return build(evidence)


def _merge_claims(evidence: dict, draft: dict, log: VerificationLog, final: dict) -> list[dict]:
    """Evidence statements + draft citations + verification verdicts → claims[].

    Order matters: a claim the editor dropped or hedged is reported as it
    landed in the final report, not as the writer originally phrased it.
    """
    verdicts = {r.claim_id: r for r in log.results}
    statements = {
        c.get("claim_id", ""): c.get("statement", "") for c in evidence.get("claims", [])
    }
    cited_ids = [c for s in draft.get("sections", []) if isinstance(s, dict) for c in _claim_ids(s.get("cited_claims"))]

    appendix = (final.get("unverified_appendix") or [])
    hedged = {cid for item in appendix for cid in statements if cid and cid in item}

    claims: list[dict] = []
    for cid in dict.fromkeys(cited_ids) or dict.fromkeys(statements):
        result = verdicts.get(cid)
        verdict = result.verdict if result else "UNSUPPORTED"
        confidence = result.confidence if result else 0.3
        if cid in hedged and verdict == "VERIFIED":
            verdict, confidence = "UNSUPPORTED", 0.4
        claims.append(
            {
                "id": cid,
                "statement": statements.get(cid, cid),
                "verdict": verdict,
                "confidence": confidence,
            }
        )
    return claims


# -------------------------------------------------------------- repair ------

def build_repair_crew(topic: str, statements: list[dict]) -> Crew:
    from .research_crew import RESEARCH_TOOLS

    repairer = Agent(
        role=REPAIR_RESEARCHER["role"], goal=REPAIR_RESEARCHER["goal"],
        backstory=REPAIR_RESEARCHER["backstory"], tools=RESEARCH_TOOLS,
        allow_delegation=False, llm=llm("researcher"), verbose=False,
    )
    tasks = [
        Task(
            description=agent_prompt(
                REPAIR_RESEARCHER,
                f"TOPIC: {topic}\nFAILED CLAIM id: {item.get('id')}\n"
                f"FAILED CLAIM: {item.get('statement')}\n\n"
                f"Search for a different source. Fetch the page before citing it. "
                f"If you find a source that genuinely supports the claim (or corrects "
                f"the figure), return it as a ResearchNote with claim_id '{item.get('id')}'.",
            ),
            expected_output="ResearchNote JSON, or a note whose statement says the claim could not be corroborated",
            agent=repairer,
            output_pydantic=ResearchNote,
        )
        for item in statements
    ]
    return Crew(agents=[repairer], tasks=tasks, process=Process.sequential, verbose=False)


def run_repair(failed_claims: list[dict], topic: str) -> dict:
    """I3. Re-search only the failed claim ids. {repaired_claims, serper_credits}."""
    before = shared_tools.credits_spent()

    repaired: list[dict] = []
    for item in failed_claims:
        cid = item.get("id", "")
        statement = item.get("statement", "")
        try:
            crew = build_repair_crew(topic, [item])
            note = crew.kickoff().pydantic
        except Exception as exc:  # noqa: BLE001 - repair is best-effort by design
            repaired.append(
                {
                    "id": cid, "statement": statement, "repaired": False,
                    "url": "", "publisher": "", "date": None,
                    "note": f"repair failed: {exc}",
                    "repaired_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
            )
            continue

        data = note.model_dump() if note is not None else {}
        repaired.append(
            {
                "id": data.get("claim_id", cid),
                "statement": data.get("statement", statement),
                "repaired": bool(data.get("url")),
                "url": data.get("url", ""),
                "publisher": data.get("publisher", ""),
                "date": data.get("date"),
                "verbatim_quote": data.get("verbatim_quote", ""),
                "note": data.get("statement", "") if not data.get("url") else "",
                "repaired_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        )

    return {
        "repaired_claims": repaired,
        "serper_credits": shared_tools.credits_spent() - before,
    }


__all__ = [
    "build_editorial_crew",
    "run_editorial",
    "run_repair",
    "EDITORIAL_TOOLS",
]