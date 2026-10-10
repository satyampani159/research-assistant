"""The Streamlit app renders a finished report and its form without needing crewai."""

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

RESULT = {
    "topic": "AI coding", "audience": "pros", "depth": "brief", "status": "complete",
    "report_md": ("# Test Report\n\n> **Audience:** pros | **Confidence:** B\n\n## Findings\n\n"
                  "Agents help [1]. They are not perfect [2].\n\n## Method and limits\n\nChecked."),
    "references": [
        {"url": "https://a.com/x", "title": "A page", "publisher": "A", "date": "2025-01-02", "role": "cited",
         "source_type": "Reputable press", "note": "n", "quote": "exact words"},
        {"url": "https://b.org/y", "title": "B page", "publisher": "B", "role": "cited"},
        {"url": "https://c.net/z", "title": "C page", "publisher": "C", "role": "further_reading"},
    ],
    "claims": [{"id": "C1", "statement": "Agents help", "verdict": "VERIFIED"},
               {"id": "C2", "statement": "Not perfect", "verdict": "UNSUPPORTED"}],
    "metrics": {"duration_s": 125, "tokens": 90000, "serper_credits": 12, "repair_needed": True},
}


APP = str(Path(__file__).resolve().parent.parent / "streamlit_app.py")


def _app():
    return AppTest.from_file(APP, default_timeout=30)


def test_form_renders_and_run_needs_a_topic():
    at = _app().run()
    assert not at.exception
    assert at.title[0].value == "Research Assistant"
    assert [b.label for b in at.button if b.label == "Run research"][0] == "Run research"
    assert at.button(key="ex-State of AI coding assistants, 2026") is not None


def test_report_renders_with_stats_citations_and_downloads():
    at = _app()
    at.session_state["result"] = RESULT
    at.run()
    assert not at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Confidence"] == "B" and metrics["Claims verified"] == "1/2" and metrics["Sources cited"] == "2"
    body = " ".join(m.value for m in at.markdown)
    assert "[[1]](https://a.com/x)" in body            # citation marks link to their source
    assert "Test Report" not in body.split("report-body")[-1]  # title is shown once, as the heading
    assert at.subheader[0].value == "Test Report"
    assert any("not fact-checked" in w.value or "could not be confirmed" in w.value for w in at.warning)


def test_degraded_report_shows_provisional_banner():
    at = _app()
    at.session_state["result"] = {**RESULT, "status": "degraded"}
    at.run()
    assert any("Provisional" in w.value for w in at.warning)
