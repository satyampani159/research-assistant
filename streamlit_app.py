"""Streamlit front end: the same research pipeline and the same look as the local UI.

Streamlit Community Cloud runs one Python process, with no Docker and no n8n, so
this app calls the engine directly and reuses the job/progress machinery from
`src/api.py` (the FastAPI app and the n8n workflow are unchanged). The finished
report is rendered by the local UI's own HTML/CSS/JS (see src/streamlit_view.py).

Local:  streamlit run streamlit_app.py
Cloud:  set SERPER_API_KEY and GEMINI_API_KEY (and optionally GROQ_API_KEY) in the app's Secrets.
"""

from __future__ import annotations

import os
import threading
import time

import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="Research Assistant", page_icon="🔎", layout="wide", initial_sidebar_state="collapsed")


def _load_secrets() -> None:
    """Copy Streamlit secrets into the environment before src.config reads it."""
    try:
        for key in ("SERPER_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "GEMINI_MODEL_STRONG", "GEMINI_MODEL_CHEAP"):
            if key in st.secrets and not os.environ.get(key):
                os.environ[key] = str(st.secrets[key])
    except Exception:  # noqa: BLE001 - no secrets file locally; .env is used instead
        pass
    # there is no local model server on Streamlit Cloud
    os.environ.setdefault("LLM_PROVIDER_ORDER", "gemini,groq")


_load_secrets()

from src import streamlit_view as view  # noqa: E402  (after secrets, so config sees them)

EXAMPLES = [
    "State of AI coding assistants, 2026",
    "Recent advances in retrieval-augmented generation",
    "Enterprise adoption of open-weight language models",
]
CREW_WEIGHT = {"Research crew": 0.55, "Editorial crew": 0.45}


def _fmt(seconds: float) -> str:
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


@st.cache_data(ttl=60, show_spinner=False)
def _providers() -> dict | None:
    try:
        from src.config import get_provider_status

        return get_provider_status()
    except Exception:  # noqa: BLE001 - the header just shows no chips
        return None


# ------------------------------------------------------------------ engine ---
def _start(topic: str, audience: str, depth: str, max_searches: int):
    """Start a report in a worker thread. Returns the job, or None if one is already running."""
    from src import api, progress

    if not api._ENGINE_LOCK.acquire(blocking=False):
        return None
    req = api.ResearchRequest(topic=topic, audience=audience, depth=depth, max_searches=max_searches)
    job = progress.create(req.model_dump())
    threading.Thread(target=api._run_job, args=(job, req), daemon=True).start()
    return job


def _monitor(job_id: str) -> dict | None:
    """Render live progress until the job ends. Returns the result, or None on failure."""
    from src import progress

    slot = st.empty()
    crews: dict[str, dict] = {}
    model, live, seen = "", "Starting…", 0

    while True:
        job = progress.get(job_id)
        if job is None:
            st.error("The run was lost (the app restarted). Please start it again.")
            return None
        snap = job.snapshot(seen)
        seen = snap["next"]
        for ev in snap["events"]:
            kind = ev.get("kind")
            if kind == "plan":
                crews[ev["crew"]] = {"agents": ev["agents"], "state": [""] * len(ev["agents"])}
            elif kind == "agent" and ev["crew"] in crews:
                crews[ev["crew"]]["state"][ev["index"]] = ev["state"]
                if ev["state"] == "working":
                    live = f"{ev['name']} is working…"
            elif kind == "tool":
                label = {"web search": "Searching the web", "news search": "Searching news",
                         "scholar search": "Searching scholar", "fetching page": "Reading page"}.get(ev.get("tool"), ev.get("tool", ""))
                live = f"{label}: {str(ev.get('detail', ''))[:80]}"
            elif kind == "llm":
                model = f"{ev.get('provider', '').title()} · {str(ev.get('model', '')).split('/')[-1]}"
            elif kind == "log" and ev.get("message"):
                live = ev["message"]

        frac = sum(CREW_WEIGHT.get(n, .5) * (sum(s in ("done", "skipped") for s in c["state"]) / max(len(c["agents"]), 1))
                   for n, c in crews.items())
        status = snap["status"] if snap["status"] in ("done", "error") else "running"
        if status == "error":
            live = "The run failed"
        elif status == "done":
            live = "Finished"
        slot.markdown(view.progress_html(crews, frac, _fmt(snap["elapsed"]), model, live, status), unsafe_allow_html=True)

        if status == "done":
            return snap["result"]
        if status == "error":
            st.error(snap["error"] or "Pipeline failed")
            return None
        time.sleep(1)


# --------------------------------------------------------------------- page --
st.markdown(view.PAGE_CSS, unsafe_allow_html=True)
st.markdown(view.header_html(_providers()), unsafe_allow_html=True)

if not os.environ.get("SERPER_API_KEY") or not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GROQ_API_KEY")):
    st.info("Add `SERPER_API_KEY` and `GEMINI_API_KEY` (or `GROQ_API_KEY`) under the app's **Secrets** "
            "(locally: `.streamlit/secrets.toml` or `.env`). Without them the app runs in offline demo mode.")

st.session_state.setdefault("topic", "")
with st.container(key="hero"):
    st.markdown(view.hero_head_html(), unsafe_allow_html=True)
    st.text_area("Topic or question", key="topic", height=84, placeholder="e.g. Recent advances in retrieval-augmented generation")

    chips = st.columns([0.5, 2.3, 3, 3.3])
    chips[0].markdown('<div style="font-size:12.5px;color:#66707f;padding-top:7px">Try:</div>', unsafe_allow_html=True)
    for col, ex in zip(chips[1:], EXAMPLES):
        col.button(ex, key=f"ex-{ex}", on_click=lambda e=ex: st.session_state.update(topic=e))

    c1, c2, c3, c4 = st.columns([1.3, 1.5, 0.8, 0.7], vertical_alignment="bottom")
    audience = c1.text_input("Audience", "general professional")
    depth = c2.selectbox("Depth", ["brief", "standard", "deep"], index=1,
                         format_func=lambda d: {"brief": "Brief: 3 research questions", "standard": "Standard: 4 research questions",
                                                "deep": "Deep: 6 research questions"}[d])
    max_searches = c3.number_input("Search budget", 1, 200, 20)
    run = c4.button("Run research", key="run", disabled=len(st.session_state.topic.strip()) < 3)
    st.markdown(view.how_html(), unsafe_allow_html=True)

if run:
    st.session_state.pop("result", None)
    job = _start(st.session_state.topic.strip(), audience.strip() or "general professional", depth, int(max_searches))
    if job is None:
        st.warning("Another report is already running. Wait for it to finish and try again.")
    else:
        st.session_state.job_id = job.id

if st.session_state.get("job_id"):
    outcome = _monitor(st.session_state.job_id)
    st.session_state.job_id = None
    if outcome is not None:
        st.session_state.result = outcome

if st.session_state.get("result"):
    result = st.session_state.result
    try:
        from src.pdf_export import build_pdf

        pdf = build_pdf({"topic": result.get("topic", ""), "audience": result.get("audience", ""), "depth": result.get("depth", ""),
                         "report_md": result.get("report_md", ""), "references": result.get("references", []),
                         "metrics": result.get("metrics", {})})
    except Exception:  # noqa: BLE001 - the PDF button is simply hidden
        pdf = None
    components.html(view.result_document(result, pdf), height=1400, scrolling=False)

st.markdown('<div class="ra-foot">Every claim is traced to a source. Verification confirms what a page says, not that the page is correct.</div>',
            unsafe_allow_html=True)
