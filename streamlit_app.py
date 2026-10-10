"""Streamlit front end: runs the same research pipeline in-process.

Streamlit Community Cloud runs one Python process, with no Docker and no n8n, so
this app calls the engine directly and reuses the job/progress machinery from
`src/api.py` (the FastAPI app and the n8n workflow are unchanged).

Local:  streamlit run streamlit_app.py
Cloud:  set SERPER_API_KEY and GEMINI_API_KEY (and optionally GROQ_API_KEY) in the app's Secrets.
"""

from __future__ import annotations

import os
import re
import threading
import time
from datetime import date

import streamlit as st

st.set_page_config(page_title="Research Assistant", page_icon="🔎", layout="wide")


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

EXAMPLES = [
    "State of AI coding assistants, 2026",
    "Recent advances in retrieval-augmented generation",
    "Enterprise adoption of open-weight language models",
]
VERDICT_LABEL = {"VERIFIED": "Verified", "UNSUPPORTED": "Unsupported", "STALE": "Source unavailable", "CONTRADICTED": "Contradicted"}
CREW_WEIGHT = {"Research crew": 0.55, "Editorial crew": 0.45}

st.markdown(
    """
<style>
.block-container { max-width: 1100px; padding-top: 2.2rem; }
h1 { letter-spacing: -.02em; }
div[data-testid="stMetricValue"] { font-size: 1.6rem; }
.report-body { font-family: Georgia, "Iowan Old Style", serif; font-size: 1.07rem; line-height: 1.75; }
.small { font-size: .85rem; color: #667085; }
</style>
""",
    unsafe_allow_html=True,
)


# ------------------------------------------------------------------ helpers --
def _fmt(seconds: float) -> str:
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


def _bibtex(refs: list[dict]) -> str:
    year = lambda d: (re.search(r"(?:19|20)\d{2}", str(d or "")) or [""])[0]  # noqa: E731
    out = []
    for i, r in enumerate(refs, 1):
        host = re.sub(r"^www\.", "", re.sub(r"^https?://", "", r.get("url", "")).split("/")[0]) or "src"
        key = f"{re.sub(r'[^a-z0-9]', '', host.lower())}{year(r.get('date')) or 'nd'}_{i}"
        clean = lambda t: re.sub(r"[{}\\]", "", str(t or ""))  # noqa: E731
        lines = [f"@misc{{{key},", f"  title = {{{clean(r.get('title') or r.get('url'))}}},",
                 f"  author = {{{clean(r.get('publisher') or host)}}},"]
        if year(r.get("date")):
            lines.append(f"  year = {{{year(r.get('date'))}}},")
        lines += [f"  url = {{{r.get('url', '')}}},", f"  note = {{Accessed {date.today().isoformat()}}}", "}"]
        out.append("\n".join(lines))
    return "\n\n".join(out) + "\n"


def _markdown_with_refs(result: dict) -> str:
    md = result.get("report_md", "")
    refs = result.get("references", [])
    cited = [r for r in refs if r.get("role") != "further_reading"]
    more = [r for r in refs if r.get("role") == "further_reading"]

    def line(r, i):
        head = f"{i}. [{r.get('title') or r.get('url')}]({r.get('url')})"
        meta = " · ".join(str(v) for v in (r.get("publisher"), r.get("date"), r.get("source_type")) if v)
        return head + (f" — {meta}" if meta else "") + (f"\n   - {r['note']}" if r.get("note") else "")

    if cited:
        md += "\n\n## References\n\n" + "\n".join(line(r, i) for i, r in enumerate(cited, 1)) + "\n"
    if more:
        md += "\n## Further reading\n\n" + "\n".join(line(r, i) for i, r in enumerate(more, 1)) + "\n"
    return md


def _link_citations(md: str, cited: list[dict]) -> str:
    """Make [n] in the body clickable: they jump to the source."""
    def sub(m: re.Match) -> str:
        n = int(m.group(1))
        return f"[[{n}]]({cited[n - 1]['url']})" if 1 <= n <= len(cited) and cited[n - 1].get("url") else m.group(0)

    return re.sub(r"\[(\d+)\](?!\()", sub, md)


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

    box = st.container(border=True)
    head = box.empty()
    bar = box.progress(0.0)
    line = box.empty()
    board = box.empty()
    crews: dict[str, dict] = {}
    llm, seen = "", 0
    marks = {"working": "⏳", "done": "✅", "skipped": "➖", "": "⚪"}

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
                    line.caption(f"{ev['name']} is working…")
            elif kind == "tool":
                line.caption(f"{ev.get('tool', '')}: {str(ev.get('detail', ''))[:90]}")
            elif kind == "llm":
                llm = f"{ev.get('provider', '')} · {str(ev.get('model', '')).split('/')[-1]}"
            elif kind == "log" and ev.get("message"):
                line.caption(ev["message"])

        frac = sum(CREW_WEIGHT.get(n, .5) * (sum(s in ("done", "skipped") for s in c["state"]) / max(len(c["agents"]), 1))
                   for n, c in crews.items())
        bar.progress(min(frac, 0.99))
        head.markdown(f"**Working…** `{_fmt(snap['elapsed'])}`" + (f"  ·  model: `{llm}`" if llm else ""))
        cols = board.columns(max(len(crews), 1))
        for col, (name, c) in zip(cols, crews.items()):
            col.markdown(f"**{name}**\n\n" + "\n\n".join(f"{marks[s]} {a}" for a, s in zip(c["agents"], c["state"])))

        if snap["status"] == "done":
            bar.progress(1.0)
            head.markdown(f"**Finished** in `{_fmt(snap['elapsed'])}`")
            return snap["result"]
        if snap["status"] == "error":
            head.markdown("**The run failed**")
            st.error(snap["error"] or "Pipeline failed")
            return None
        time.sleep(1)


# ------------------------------------------------------------------ results --
def _render(result: dict) -> None:
    refs = result.get("references", [])
    cited = [r for r in refs if r.get("role") != "further_reading"]
    more = [r for r in refs if r.get("role") == "further_reading"]
    claims = result.get("claims", [])
    metrics = result.get("metrics", {})
    md = result.get("report_md", "")
    verified = sum(c.get("verdict") == "VERIFIED" for c in claims)
    grade = (re.search(r"\*\*Confidence:\*\*\s*([ABC])\b", md) or [None, "–"])[1]

    title = (re.search(r"^#\s+(.+)$", md, re.M) or [None, result.get("topic", "Report")])[1]
    st.subheader(title)

    if result.get("status") == "degraded":
        st.warning("**Provisional report.** The findings come straight from the sources and were not "
                   "fact-checked. Read each source before relying on a figure, or run it again.")
    elif metrics.get("repair_needed"):
        st.warning("**Some claims could not be confirmed.** They are hedged or left out. See Claim verification below.")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Confidence", grade)
    c2.metric("Claims verified", f"{verified}/{len(claims)}" if claims else "–")
    c3.metric("Sources cited", len(cited))
    c4.metric("Run time", _fmt(metrics.get("duration_s", 0)))

    body = re.sub(r"^#\s+.+\n+", "", md, count=1)
    body = re.sub(r"^## Contents\n+(?:\d+\..*\n)+\n*", "", body, flags=re.M)
    st.markdown(f'<div class="report-body">\n\n{_link_citations(body, cited)}\n\n</div>', unsafe_allow_html=True)

    d1, d2, d3, d4 = st.columns(4)
    full = _markdown_with_refs(result)
    stem = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:50] or "report"
    d1.download_button("Markdown", full, f"{stem}.md", "text/markdown", use_container_width=True)
    d2.download_button("BibTeX", _bibtex(refs), f"{stem}.bib", "application/x-bibtex", use_container_width=True)
    try:
        from src.pdf_export import build_pdf

        pdf = build_pdf({"topic": result.get("topic", ""), "audience": result.get("audience", ""),
                         "depth": result.get("depth", ""), "report_md": md, "references": refs,
                         "metrics": metrics})
        d3.download_button("PDF", pdf, f"{stem}.pdf", "application/pdf", use_container_width=True)
    except Exception as exc:  # noqa: BLE001
        d3.caption(f"PDF unavailable: {exc}")
    d4.caption(f"{metrics.get('tokens', 0):,} tokens · {metrics.get('serper_credits', 0)} searches")

    with st.expander(f"Claim verification ({len(claims)})"):
        if claims:
            st.dataframe(
                [{"Claim": c.get("id"), "Verdict": VERDICT_LABEL.get(c.get("verdict"), c.get("verdict")),
                  "Statement": c.get("statement")} for c in claims],
                hide_index=True, use_container_width=True)
        else:
            st.caption("No claim-level data for this report.")

    with st.expander(f"References ({len(refs)})", expanded=True):
        def card(r: dict, n: int | None) -> None:
            meta = " · ".join(str(v) for v in (r.get("publisher"), r.get("date"), r.get("source_type")) if v)
            st.markdown(f"**{f'[{n}] ' if n else ''}[{r.get('title') or r.get('url')}]({r.get('url')})**  \n"
                        f"<span class='small'>{meta}</span>", unsafe_allow_html=True)
            if r.get("note"):
                st.caption(r["note"])
            if r.get("quote"):
                st.markdown(f"> “{r['quote']}”")

        for i, r in enumerate(cited, 1):
            card(r, i)
        if more:
            st.markdown("##### Further reading")
            for r in more:
                card(r, None)

    st.caption("Every claim is traced to a source. Verification confirms what a page says, not that the page is correct.")


# --------------------------------------------------------------------- page --
st.title("Research Assistant")
st.caption("Specialist agents plan the questions, gather evidence, draft the report and verify each claim against the page it cites.")

if not os.environ.get("SERPER_API_KEY") or not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GROQ_API_KEY")):
    st.info("Add `SERPER_API_KEY` and `GEMINI_API_KEY` (or `GROQ_API_KEY`) under the app's **Secrets** "
            "(locally: `.streamlit/secrets.toml` or `.env`). Without them the app runs in offline demo mode.")

st.session_state.setdefault("topic", "")
with st.sidebar:
    st.header("Options")
    audience = st.text_input("Audience", "general professional")
    depth = st.selectbox("Depth", ["brief", "standard", "deep"], index=1,
                         format_func=lambda d: {"brief": "Brief: 3 research questions", "standard": "Standard: 4",
                                                "deep": "Deep: 6"}[d])
    max_searches = st.number_input("Search budget", 1, 200, 20)
    st.caption("A standard report takes several minutes and uses about 100k model tokens, so free-tier quotas "
               "cover only a few reports a day.")

st.text_area("Topic or question", key="topic", height=80,
             placeholder="e.g. Recent advances in retrieval-augmented generation")
cols = st.columns(len(EXAMPLES))
for col, ex in zip(cols, EXAMPLES):
    col.button(ex, key=f"ex-{ex}", use_container_width=True, on_click=lambda e=ex: st.session_state.update(topic=e))

run = st.button("Run research", type="primary", disabled=len(st.session_state.topic.strip()) < 3)

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
    st.divider()
    _render(st.session_state.result)
