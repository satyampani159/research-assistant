"""I1 — the six contracts from Project.md §2.5 plus the two containers CrewAI
needs. A drift in any of these breaks every handoff in the pipeline.
"""

from __future__ import annotations

import pytest

from src.contracts import (
    ClaimResult,
    DraftReport,
    EvidenceTable,
    FinalReport,
    Reference,
    ResearchNote,
    ResearchNoteList,
    ResearchPlan,
    VerificationLog,
)

NOW = "2026-03-11T10:00:00+00:00"


def test_research_note_fields():
    note = ResearchNote(
        claim_id="C1", sub_question_id="SQ1",
        statement="Adoption reached 38%.", url="https://reuters.com/x",
        publisher="Reuters", date="2026-03-11", retrieved_at=NOW,
        verbatim_quote="adoption reached 38%", source_tier=2,
    )
    assert note.source_tier == 2
    assert note.date == "2026-03-11"


def test_research_note_date_is_optional():
    note = ResearchNote(
        claim_id="C1", sub_question_id="SQ1", statement="s", url="u",
        publisher="p", retrieved_at=NOW, verbatim_quote="q",
    )
    assert note.date is None
    assert note.source_tier == 3  # documented default


def test_research_note_allows_missing_metadata_when_model_omits_it():
    note = ResearchNote.model_validate({
        "claim_id": "C1",
        "statement": "Adoption reached 38%.",
        "url": "https://example.com",
        "publisher": "Example",
        "verbatim_quote": "adoption reached 38%",
    })
    assert note.sub_question_id == ""
    assert note.retrieved_at == ""


@pytest.mark.parametrize("tier", [0, 4])
def test_source_tier_is_bounded(tier):
    with pytest.raises(Exception):
        ResearchNote(
            claim_id="C", sub_question_id="S", statement="s", url="u", publisher="p",
            retrieved_at=NOW, verbatim_quote="q", source_tier=tier,
        )


def test_evidence_table_roundtrips():
    table = EvidenceTable(
        claims=[{"claim_id": "C1", "statement": "s", "supports": [], "conflicts": []}],
        gaps=["no pricing data"],
        contradictions=["C1 vs C2"],
    )
    assert EvidenceTable(**table.model_dump()) == table


def test_draft_report_shape():
    draft = DraftReport(
        title="State of AI coding agents",
        thesis="Adoption is broad, trust is not.",
        sections=[{"heading": "Adoption", "body": "38% [1]", "cited_claims": ["C1"],
                   "sources": ["https://reuters.com/x"]}],
    )
    assert draft.sections[0].cited_claims == ["C1"]


def test_claim_result_verdict_is_an_enum():
    for verdict in ["VERIFIED", "UNSUPPORTED", "CONTRADICTED", "STALE"]:
        assert ClaimResult(claim_id="C", verdict=verdict, confidence=0.9).verdict == verdict
    with pytest.raises(Exception):
        ClaimResult(claim_id="C", verdict="PROBABLY_FINE", confidence=0.9)


def test_confidence_is_bounded():
    with pytest.raises(Exception):
        ClaimResult(claim_id="C", verdict="VERIFIED", confidence=1.4)


def test_final_report_grade_is_an_enum():
    assert FinalReport(markdown="m", confidence_grade="A").confidence_grade == "A"
    with pytest.raises(Exception):
        FinalReport(markdown="m", confidence_grade="A+")


def test_containers_carry_their_children():
    """CrewAI's output_pydantic needs one model per task; these are the wrappers."""
    notes = ResearchNoteList(sub_question_id="SQ1", notes=[ResearchNote(
        claim_id="C1", sub_question_id="SQ1", statement="s", url="u", publisher="p",
        retrieved_at=NOW, verbatim_quote="q")])
    assert len(notes.notes) == 1
    plan = ResearchPlan(topic="t", sub_questions=[{"sub_question_id": "SQ1", "question": "q?"}])
    assert plan.sub_questions[0].sub_question_id == "SQ1"


def test_reference_model_matches_the_output_contract():
    ref = Reference(url="u", title="t", publisher="p", date="2026-01-01")
    assert set(ref.model_dump()) == {"url", "title", "publisher", "date"}


def test_models_are_json_serialisable():
    """The API returns JSON, never prose."""
    import json

    log = VerificationLog(results=[ClaimResult(
        claim_id="C1", verdict="VERIFIED", confidence=0.9, evidence_quote="q",
        checked_url="u", checked_at=NOW)])
    assert json.loads(log.model_dump_json())["results"][0]["verdict"] == "VERIFIED"

def test_draft_schema_names_its_section_fields():
    """A bare list[dict] gave Gemini no fields to fill and it returned empty sections."""
    schema = DraftReport.model_json_schema()
    section = schema["$defs"]["DraftSection"]["properties"]
    assert {"heading", "body", "cited_claims", "sources"} <= set(section)


def test_final_report_schema_names_reference_fields():
    from src.contracts import FinalReport

    ref = FinalReport.model_json_schema()["$defs"]["Reference"]["properties"]
    assert {"url", "title", "publisher", "date"} <= set(ref)
    r = FinalReport(markdown="m", references=[{"url": "https://a.com", "title": "A"}])
    assert r.model_dump()["references"][0]["publisher"] == ""


def test_plan_schema_names_subquestion_fields():
    plan = ResearchPlan.model_json_schema()["$defs"]["SubQuestion"]["properties"]
    assert {"sub_question_id", "question", "queries"} <= set(plan)
