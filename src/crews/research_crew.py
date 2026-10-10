"""Crew 1 — RESEARCH · sequential planner, researchers, synthesizer.

The Lead Planner creates a typed ResearchPlan, one researcher handles each
sub-question, and the Synthesizer merges their notes into an EvidenceTable.
Returns JSON, never prose (Project.md §2.5).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from crewai import Agent, Crew, Process, Task
from crewai.tools import tool

from ..config import llm
from ..contracts import EvidenceTable, ResearchNoteList, ResearchPlan
from ..prompts import LEAD_PLANNER, RESEARCHER, SYNTHESIZER, agent_prompt
from .. import progress
from .. import tools as shared_tools

DEPTH_SUB_QUESTIONS = {"brief": 3, "standard": 4, "deep": 6}


# --------------------------------------------------------------- tools ------
# CrewAI wrappers: identical names + signatures to the frozen I2 interface, so
# integration is an import swap rather than a rewrite.

@tool("search_web")
def search_web(query: str, num: int = 8) -> str:
    """Search the live web. Returns JSON: [{title, url, snippet}]."""
    import json

    return json.dumps(shared_tools.search_web(query, num), ensure_ascii=False)


@tool("search_news")
def search_news(query: str, num: int = 5) -> str:
    """Search recent news. Returns JSON: [{title, url, snippet, source, date}]."""
    import json

    return json.dumps(shared_tools.search_news(query, num), ensure_ascii=False)


@tool("search_scholar")
def search_scholar(query: str, num: int = 5) -> str:
    """Search academic literature. Returns JSON: [{title, authors, year, cited_by, url}]."""
    import json

    return json.dumps(shared_tools.search_scholar(query, num), ensure_ascii=False)


@tool("fetch_page")
def fetch_page(url: str, max_chars: int = 4000) -> str:
    """Fetch a page and return clean readable text with its title."""
    page = shared_tools.fetch_page(url, max_chars)
    return f"TITLE: {page['title']}\n\n{page['text']}"


RESEARCH_TOOLS = [search_web, search_news, search_scholar, fetch_page]


# ------------------------------------------------------------- factory ------

def build_research_crew(topic: str, audience: str, depth: str = "standard") -> Crew:
    n_researchers = DEPTH_SUB_QUESTIONS.get(depth, 4)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    planner = Agent(
        role=LEAD_PLANNER["role"],
        goal=LEAD_PLANNER["goal"],
        backstory=LEAD_PLANNER["backstory"],
        llm=llm("planner"),
        allow_delegation=False,
        verbose=False,
    )

    planning_task = Task(
        description=agent_prompt(
            LEAD_PLANNER,
            f"TOPIC: {topic}\nAUDIENCE: {audience}\nDEPTH: {depth}\nTODAY: {stamp}\n"
            f"Create exactly {n_researchers} distinct sub-questions, with IDs SQ1 through "
            f"SQ{n_researchers}. Give each at least two concrete search queries and a "
            "short reason it matters. Return a ResearchPlan JSON object.",
        ),
        expected_output=(
            "ResearchPlan JSON: {topic, sub_questions: "
            "[{sub_question_id, question, queries, why_it_matters}], coverage_notes}"
        ),
        agent=planner,
        output_pydantic=ResearchPlan,
    )

    researchers = [
        Agent(
            role=f"{RESEARCHER['role']} #{i + 1}",
            goal=RESEARCHER["goal"],
            backstory=RESEARCHER["backstory"],
            tools=RESEARCH_TOOLS,
            allow_delegation=False,
            llm=llm("researcher"),
            verbose=False,
        )
        for i in range(n_researchers)
    ]

    synthesizer = Agent(
        role=SYNTHESIZER["role"],
        goal=SYNTHESIZER["goal"],
        backstory=SYNTHESIZER["backstory"],
        tools=[fetch_page],
        allow_delegation=False,
        llm=llm("synthesizer"),
        verbose=False,
    )

    # One researcher task per planned sub-question.
    research_tasks = [
        Task(
            description=agent_prompt(
                RESEARCHER,
                f"TOPIC: {topic}\nAUDIENCE: {audience}\nTODAY: {stamp}\n"
                f"You are researcher #{i + 1} of {n_researchers}. Use the ResearchPlan "
                f"in the task context and answer only sub-question SQ{i + 1}. Include "
                f"its exact question in sub_question_id.\n"
                f"Work in this order: 2 broad web searches → 1 specific search → "
                f"search_scholar if the sub-question is technical or academic → "
                f"fetch_page on every URL you intend to cite (a claim without fetched "
                f"page text is not a claim).\n"
                f"Return 3-5 notes in research_notes.notes, each tagged SQ{i + 1}.",
            ),
            expected_output=(
                "ResearchNoteList JSON: {sub_question_id, notes: [ResearchNote, ...]} "
                "with 3-5 notes spanning at least 3 independent domains."
            ),
            agent=researchers[i],
            context=[planning_task],
            output_pydantic=ResearchNoteList,
        )
        for i in range(n_researchers)
    ]

    synthesize_task = Task(
        description=agent_prompt(
            SYNTHESIZER,
            f"TOPIC: {topic}\nAUDIENCE: {audience}\nDEPTH: {depth}\n"
            f"Merge the {n_researchers} research notesets (given as context) into one "
            f"evidence table. Target {n_researchers * 2}-{n_researchers * 3} distinct claims. Drop any note "
            f"whose url does not appear in another note's support list without a matching "
            f"fetch, and drop duplicates from the same domain.",
        ),
        expected_output=(
            "EvidenceTable JSON: {claims: [{claim_id, statement, supports[], conflicts[]}], "
            "gaps: [], contradictions: []}"
        ),
        agent=synthesizer,
        context=research_tasks,
        output_pydantic=EvidenceTable,
    )

    return Crew(
        agents=[planner, *researchers, synthesizer],
        tasks=[planning_task, *research_tasks, synthesize_task],
        process=Process.sequential,
        task_callback=progress.task_done,
        max_rpm=int(os.getenv("CREW_MAX_RPM", "10")),
        verbose=False,
    )


def _renumber_claims(evidence: dict) -> None:
    """Give every claim a stable C1..Cn id.

    The synthesizer mixes schemes ("c1", "claim_1", "C3"), and the writer,
    fact-checker and guardrails all key on these ids, so they are normalised
    once here instead of being guessed at downstream.
    """
    for i, claim in enumerate(evidence.get("claims", []), 1):
        new = f"C{i}"
        claim["claim_id"] = new
        for note in claim.get("supports", []) or []:
            if isinstance(note, dict):
                note["claim_id"] = new


def _name_gaps(evidence: dict) -> None:
    """Replace bare sub-question ids in gaps[] ("SQ2") with the question they refer to."""
    by_id = {q["sub_question_id"]: q["question"] for q in evidence.get("sub_questions", []) if q.get("sub_question_id")}
    evidence["gaps"] = [
        f"No usable sources were found for: {by_id[g.strip()]}" if g.strip() in by_id else g
        for g in (str(x) for x in evidence.get("gaps", []) or [])
    ]


def _plan_questions(crew: Crew) -> list[dict]:
    """The planner's sub-questions, kept so reports can be organised by question."""
    try:
        plan = crew.tasks[0].output.pydantic
        return [{"sub_question_id": q.sub_question_id, "question": q.question}
                for q in (plan.sub_questions if plan else []) if q.question]
    except Exception:  # noqa: BLE001 - grouping is a nicety, never a failure
        return []


def run_research(topic: str, audience: str, depth: str, max_searches: int) -> dict:
    """I3. Returns an EvidenceTable as a JSON-serializable dict.

    max_searches is applied before the crew starts. Producing *no* evidence at
    all is an error, not a report: /research must fail loudly rather than hand
    n8n an empty report that looks successful. Degradation (§2.10.5) is for
    individual unverifiable claims, which happens downstream in Crew 2.
    """
    shared_tools.reset_budget(max_searches)
    crew = build_research_crew(topic, audience, depth)

    progress.begin_crew("Research crew", crew)
    try:
        result = crew.kickoff()
        table = result.pydantic
        evidence = table.model_dump() if table is not None else {}
    except shared_tools.BudgetExhausted as exc:
        raise RuntimeError(
            f"Serper budget of {max_searches} searches exhausted before any evidence "
            f"was gathered: {exc}"
        ) from exc
    except Exception as exc:  # noqa: BLE001 - surfaced as a 502 by the API
        raise RuntimeError(f"{type(exc).__name__}: {exc}") from exc

    finally:
        progress.end_crew()

    if not evidence.get("claims"):
        raise RuntimeError(
            f"research crew returned no claims for {topic!r} within {max_searches} "
            "searches; refusing to publish an empty report"
        )

    evidence["tokens"] = int(getattr(getattr(crew, "usage_metrics", None), "total_tokens", 0) or 0)
    _renumber_claims(evidence)
    evidence["sub_questions"] = _plan_questions(crew)
    _name_gaps(evidence)
    evidence["serper_credits"] = shared_tools.credits_spent()
    evidence["topic"] = topic
    evidence["depth"] = depth
    return evidence


__all__ = ["build_research_crew", "run_research", "RESEARCH_TOOLS"]