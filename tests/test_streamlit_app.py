"""The Streamlit app mirrors the local UI: same report page code, same design tokens."""

from pathlib import Path

import pytest

from src import streamlit_view as view

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


def test_report_page_is_the_local_ui_code():
    doc = view.result_document(RESULT, pdf=b"%PDF-fake")
    frontend = Path(view.FRONTEND)
    # the exact local stylesheet and script are inlined, so the two UIs cannot drift
    assert (frontend / "style.css").read_text(encoding="utf-8").strip()[:200] in doc
    assert "function linkCitations" in doc and "function showResult" in doc
    assert "showResult(window.__DATA__)" in doc
    assert "Test Report" in doc and "https://a.com/x" in doc
    # the local page's own header/form/progress are hidden: Streamlit provides those
    assert "#form-card" in doc and "display:none" in doc


def test_embedded_data_cannot_break_out_of_the_script():
    nasty = {**RESULT, "report_md": "# T\n\n</script><script>alert(1)</script>"}
    doc = view.result_document(nasty)
    assert "</script><script>alert(1)" not in doc


def test_pdf_is_embedded_for_the_download_button():
    import base64

    doc = view.result_document(RESULT, pdf=b"%PDF-1.4 hello")
    assert base64.b64encode(b"%PDF-1.4 hello").decode() in doc
    assert "__pdf" in view.result_document(RESULT, pdf=None)


def test_design_tokens_match_the_local_stylesheet():
    css = (Path(view.FRONTEND) / "style.css").read_text(encoding="utf-8")
    for token in ("#2b5bd7", "#f5f6f8", "#e2e6ec", "#157f4a", "#a15c07", "#b42318"):
        assert token in css and token in view.PAGE_CSS


def test_header_and_progress_markup():
    head = view.header_html({"mode": "gemini", "providers": {"gemini": {"healthy": True}, "groq": {"healthy": False},
                                                              "serper": {"healthy": True}}})
    assert "Gemini · active" in head and "Research Assistant" in head and "Search" in head
    crews = {"Research crew": {"agents": ["Lead Planner", "Researcher #1"], "state": ["done", "working"]},
             "Editorial crew": {"agents": ["Report Writer"], "state": [""]}}
    card = view.progress_html(crews, 0.3, "1:05", "Gemini · flash", "Reading page: x.com")
    assert "1 of 3 agents finished so far" in card and 'class="ra-agent working"' in card and "Gemini · flash" in card
    assert "Pipeline finished" in view.progress_html(crews, 1, "2:00", "", "Finished", "done")


def test_app_boots_and_shows_form():
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "streamlit_app.py"), default_timeout=30).run()
    assert not at.exception
    assert any(b.key == "run" for b in at.button)
    assert at.text_area[0].label == "Topic or question"


def test_finished_report_renders_without_error():
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "streamlit_app.py"), default_timeout=30)
    at.session_state["result"] = RESULT
    at.run()
    assert not at.exception
