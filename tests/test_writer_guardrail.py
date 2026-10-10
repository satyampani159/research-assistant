"""Writer guardrail judged against the evidence actually gathered.

crewai is stubbed when absent so the pure guardrail logic runs anywhere.
"""

import sys
import types

import pytest


def _editorial():
    try:
        import crewai  # noqa: F401
    except ImportError:
        mod = types.ModuleType("crewai")
        for name in ("Agent", "Crew", "Process", "Task"):
            setattr(mod, name, object)
        tools = types.ModuleType("crewai.tools")
        tools.tool = lambda *_a, **_k: (lambda fn: fn)
        sys.modules["crewai"], sys.modules["crewai.tools"] = mod, tools
    from src.crews import editorial_crew

    return editorial_crew


def _evidence(n_domains_per_claim=1):
    domains = ["a.com", "b.org", "c.net", "d.io"]
    return {"claims": [
        {"claim_id": f"C{i}", "statement": f"s{i}",
         "supports": [{"url": f"https://{domains[(i + k) % 4]}/p{i}"} for k in range(n_domains_per_claim)]}
        for i in range(4)
    ]}


def _draft(sections):
    """CrewAI hands the guardrail a task output exposing .pydantic."""
    return types.SimpleNamespace(pydantic={"title": "t", "thesis": "t", "sections": sections})


def test_passing_guardrail_returns_the_output_untouched():
    """Returning a message string would replace the draft with that string inside CrewAI."""
    ec = _editorial()
    check = ec.make_writer_guardrail(_evidence())
    payload = _draft([{"heading": "A", "body": "Fact [1].", "cited_claims": ["C0", "C1", "C2", "C3"]}])
    ok, result = check(payload)
    assert ok is True and result is payload


def test_editor_guardrail_returns_the_output_untouched():
    ec = _editorial()
    payload = types.SimpleNamespace(pydantic={"markdown": "m", "references": [{"url": "https://a.com/x"}]})
    ok, result = ec.editor_guardrail(["https://a.com/x"])(payload)
    assert ok is True and result is payload


def test_uncited_number_still_fails():
    ec = _editorial()
    check = ec.make_writer_guardrail(_evidence())
    ok, _ = check(_draft([{"heading": "A", "body": "Revenue was 40 percent.", "cited_claims": ["C0", "C1", "C2", "C3"]}]))
    assert ok is False


def test_low_coverage_fails():
    ec = _editorial()
    ok, msg = ec.make_writer_guardrail(_evidence())(_draft([{"heading": "A", "body": "x [1].", "cited_claims": ["C0"]}]))
    assert ok is False and "coverage" in msg


def test_single_source_section_is_reported_not_failed():
    ec = _editorial()
    ev = {"claims": [{"claim_id": "C0", "supports": [{"url": "https://a.com/1"}]},
                     {"claim_id": "C1", "supports": [{"url": "https://a.com/2"}]},
                     {"claim_id": "C2", "supports": [{"url": "https://b.org/1"}]}]}
    draft = {"sections": [{"heading": "Narrow", "cited_claims": ["C0", "C1"], "sources": []},
                          {"heading": "Broad", "cited_claims": ["C0", "C2"], "sources": []}]}
    assert ec.thin_sections(draft, ev) == ["Narrow"]


def test_draft_with_alternate_field_names_is_understood():
    ec = _editorial()
    draft = ec._normalize_draft({"sections": [
        {"title": "Adoption", "content": ["Fact [1]."], "claims": [{"claim_id": "C1"}, "C2"], "urls": ["https://a.com/x"]}]})
    sec = draft["sections"][0]
    assert sec["heading"] == "Adoption" and sec["body"] == "Fact [1]."
    assert sec["cited_claims"] == ["C1", "C2"] and sec["sources"] == ["https://a.com/x"]


def test_claim_ids_are_renumbered_consistently():
    _editorial()
    from src.crews import research_crew

    ev = {"claims": [{"claim_id": "c1", "supports": [{"claim_id": "c1"}]},
                     {"claim_id": "claim_2", "supports": [{"claim_id": "claim_2"}]}]}
    research_crew._renumber_claims(ev)
    assert [c["claim_id"] for c in ev["claims"]] == ["C1", "C2"]
    assert ev["claims"][1]["supports"][0]["claim_id"] == "C2"


def test_bare_subquestion_gap_becomes_readable():
    _editorial()
    from src.crews import research_crew

    ev = {"gaps": ["SQ2", "pricing unknown"], "sub_questions": [{"sub_question_id": "SQ2", "question": "How costly is it?"}]}
    research_crew._name_gaps(ev)
    assert ev["gaps"] == ["No usable sources were found for: How costly is it?", "pricing unknown"]


def test_citation_coverage_ignores_id_case():
    from src import quality

    assert quality.citation_coverage(["c1", "C2"], total=2)[0] is True


def test_grade_is_capped_by_breadth_of_evidence():
    ec = _editorial()
    assert ec.cap_grade("A", 2, 2) == "C"      # all verified, but almost no evidence
    assert ec.cap_grade("A", 5, 3) == "B"
    assert ec.cap_grade("A", 8, 5) == "A"
    assert ec.cap_grade("C", 8, 5) == "C"      # never raises a grade the editor withheld
    assert ec.cap_grade("B", 8, 5) == "B"
