"""What a researcher sees: themes, flagged disagreement, honest limits, no internals."""

from src import fallback_report
from src.report_organizer import organize
from src.citations import build_references

EVIDENCE = {
    "topic": "AI coding assistants",
    "sub_questions": [
        {"sub_question_id": "SQ1", "question": "How widely are AI coding tools adopted?"},
        {"sub_question_id": "SQ2", "question": "How reliable are autonomous coding agents?"},
    ],
    "claims": [
        {"claim_id": "C1", "statement": "84% of developers use or plan to use AI tools",
         "supports": [{"url": "https://a.com/1", "publisher": "A", "sub_question_id": "SQ1", "date": "2025-07-29"}]},
        {"claim_id": "C2", "statement": "The best agent completed 41% of tasks",
         "conflicts": ["C3"],
         "supports": [{"url": "https://b.org/2", "publisher": "B", "sub_question_id": "SQ2"}]},
        {"claim_id": "C3", "statement": "The best agent completed 91% of tasks",
         "conflicts": ["C2"],
         "supports": [{"url": "https://c.net/3", "publisher": "C", "sub_question_id": "How reliable are autonomous coding agents?"}]},
    ],
    "gaps": ["No data on small teams"],
    "contradictions": ["C2 vs C3: 41% vs 91%"],
}


def test_fallback_groups_findings_under_the_research_questions():
    md = fallback_report.build(EVIDENCE)["markdown"]
    assert "## How widely are AI coding tools adopted" in md
    assert "## How reliable are autonomous coding agents" in md
    # a claim keyed by the question text instead of its id still lands in the right group
    reliab = md.split("## How reliable")[1]
    assert "91%" in reliab and "41%" in reliab


def test_disagreeing_claims_are_flagged_not_presented_as_settled():
    md = fallback_report.build(EVIDENCE)["markdown"]
    assert md.count("(sources disagree)") == 2


def test_fallback_without_a_plan_still_works():
    ev = {**EVIDENCE, "sub_questions": []}
    md = fallback_report.build(ev)["markdown"]
    assert "## Key findings" in md


def test_degraded_report_says_it_is_provisional():
    fb = fallback_report.build(EVIDENCE)
    refs = build_references(EVIDENCE, fb["references"])
    out = organize(fb["markdown"], topic="AI coding assistants", references=refs, status="degraded")
    assert "provisional" in out.lower()
    assert "not fact-checked" in out.lower()
    assert "## Method and limits" in out
    assert out.index("## Method and limits") > out.index("## Gaps in the evidence")  # limits come last


def test_complete_report_states_what_was_checked_without_overclaiming():
    claims = [{"verdict": "VERIFIED"}, {"verdict": "VERIFIED"}, {"verdict": "UNSUPPORTED"}]
    refs = [{"url": "https://a.com", "date": "2025-07-29", "role": "cited"}]
    out = organize("# T\n\n## Findings\n\n- x [1]\n", topic="T", references=refs, claims=claims)
    assert "2 of 3 claims were checked" in out
    assert "Source dates:** 2025" in out
    assert "second source" in out  # the limit of a same-page check is stated


def test_organizer_does_not_duplicate_an_existing_methods_section():
    out = organize("# T\n\n## Findings\n\n- x [1]\n\n## Methods\n\nDone carefully.\n", references=[{"url": "u"}], claims=[{}])
    assert out.count("Method") == 1


def test_no_internal_wording_reaches_the_reader():
    fb = fallback_report.build(EVIDENCE)
    refs = build_references(EVIDENCE, fb["references"])
    out = organize(fb["markdown"], references=refs, status="degraded").lower()
    for word in ("traceback", "guardrail", "evidence table", "crew", "exception"):
        assert word not in out


def test_references_carry_source_type_and_supporting_passage():
    ev = {"claims": [{"claim_id": "C1", "statement": "s", "supports": [
        {"url": "https://gov.example/x", "publisher": "Gov", "source_tier": 1, "verbatim_quote": "exact words"}]}]}
    ref = build_references(ev, [{"url": "https://gov.example/x"}])[0]
    assert ref["source_type"] == "Primary / official"
    assert ref["quote"] == "exact words"


def test_thin_sections_are_disclosed_in_the_limits():
    md = "# T\n\n## Findings\n\n- x [1]\n"
    out = organize(md, references=[{"url": "u"}], claims=[{"verdict": "VERIFIED"}],
                   thin_sections=["Agentic workflows"])
    assert "Source coverage is thin for: Agentic workflows" in out


def test_editors_own_methods_line_is_not_duplicated():
    nl = chr(10)
    md = nl.join(["# T", "", "## Findings", "", "Fact [1].", "", "Methods note: 4 sources were used.", ""])
    out = organize(md, references=[{"url": "u"}], claims=[{"verdict": "VERIFIED"}])
    assert "Methods note" not in out
    assert out.count("Method and limits") == 1


def test_entailment_prompt_formats_without_error():
    """Unescaped JSON braces once made every LLM judgement raise KeyError('"entails"')."""
    from src.prompts import ENTAILMENT_PROMPT

    text = ENTAILMENT_PROMPT.format(claim="X grew 5%", source_text="X grew 5% in 2025.")
    assert "X grew 5%" in text and '"entails"' in text


def test_very_thin_evidence_is_called_out():
    out = organize("# T\n\n## F\n\n- x [1]\n".replace("\n", chr(10)), references=[{"url": "u"}],
                   claims=[{"verdict": "VERIFIED"}, {"verdict": "VERIFIED"}])
    assert "Only 2 claims survived" in out
