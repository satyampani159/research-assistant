"""I1 — frozen data contracts (Project.md §2.5).

Every agent handoff is one of these models. Raw strings between agents are
forbidden; if you find yourself passing prose, add a field here instead.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Verdict = Literal["VERIFIED", "UNSUPPORTED", "CONTRADICTED", "STALE"]
ConfidenceGrade = Literal["A", "B", "C"]


class ResearchNote(BaseModel):
    """One atomic claim extracted from one URL."""

    claim_id: str
    sub_question_id: str = ""
    statement: str
    url: str
    publisher: str
    date: str | None = None
    retrieved_at: str = ""
    verbatim_quote: str
    source_tier: int = Field(default=3, ge=1, le=3)
    """1 = primary/official, 2 = reputable press, 3 = other."""


class ResearchNoteList(BaseModel):
    """Container for one researcher's sub-question answer.

    CrewAI's `output_pydantic` needs a single model per task, and a researcher
    answering one sub-question returns several notes — this is the wrapper.
    """

    sub_question_id: str = ""
    notes: list[ResearchNote] = Field(default_factory=list)


class SubQuestion(BaseModel):
    sub_question_id: str = ""
    question: str = ""
    queries: list[str] = Field(default_factory=list)
    why_it_matters: str = ""


class ResearchPlan(BaseModel):
    """Lead Planner output: sub-questions + the search queries for each."""

    topic: str
    sub_questions: list[SubQuestion] = Field(default_factory=list)

    coverage_notes: list[str] = Field(default_factory=list)


class EvidenceTable(BaseModel):
    """Synthesizer output — the single source of truth for the Writer."""

    claims: list[dict] = Field(default_factory=list)
    """[{claim_id, statement, supports[ResearchNote], conflicts[]}]"""

    gaps: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)


class DraftSection(BaseModel):
    heading: str = ""
    body: str = ""
    cited_claims: list[str] = Field(default_factory=list)
    """claim ids such as ["C1", "C2"]"""
    sources: list[str] = Field(default_factory=list)
    """urls relied on, copied from the evidence"""


class DraftReport(BaseModel):
    """Writer output. `cited_claims` keeps the claim→citation mapping machine-readable."""

    title: str
    thesis: str
    sections: list[DraftSection] = Field(default_factory=list)


class ClaimResult(BaseModel):
    """Fact-checker output for one atomic claim."""

    claim_id: str
    verdict: Verdict
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_quote: str = ""
    checked_url: str = ""
    checked_at: str = ""


class VerificationLog(BaseModel):
    results: list[ClaimResult] = Field(default_factory=list)

    def verified_ids(self) -> list[str]:
        return [r.claim_id for r in self.results if r.verdict == "VERIFIED"]

    def failed(self) -> list[ClaimResult]:
        return [r for r in self.results if r.verdict != "VERIFIED"]


class Reference(BaseModel):
    """One row of the final reference list."""

    url: str
    title: str
    publisher: str = ""
    date: str | None = None


class CitationItem(BaseModel):
    url: str = ""
    title: str = ""
    note: str = ""


class CitationList(BaseModel):
    """Citation Librarian output: one item per evidence source."""

    references: list[CitationItem] = Field(default_factory=list)


class FinalReport(BaseModel):
    """Editor output — what /research returns as report_md + references."""

    markdown: str
    references: list[Reference] = Field(default_factory=list)
    confidence_grade: ConfidenceGrade = "C"
    unverified_appendix: list[str] = Field(default_factory=list)


__all__ = [
    "Verdict",
    "ConfidenceGrade",
    "ResearchNote",
    "ResearchNoteList",
    "ResearchPlan",
    "EvidenceTable",
    "DraftReport",
    "ClaimResult",
    "VerificationLog",
    "Reference",
    "DraftSection",
    "SubQuestion",
    "CitationItem",
    "CitationList",
    "FinalReport",
]