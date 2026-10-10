"""Presentation helpers for the Streamlit app, so it matches the local UI (frontend/).

The finished report is rendered by the SAME index.html / style.css / app.js the
local interface uses, embedded in a frame, so the two cannot drift apart. The
header, form and progress card are Streamlit widgets styled with the same
design tokens.
"""

from __future__ import annotations

import base64
import html
import json
from pathlib import Path

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"

LOGO = ('<svg class="ra-logo" viewBox="0 0 32 32" aria-hidden="true"><rect width="32" height="32" rx="8" fill="#2b5bd7"/>'
        '<path d="M9 22V10h7a4 4 0 0 1 0 8h-7m6 0 5 4" fill="none" stroke="#fff" stroke-width="2.6" '
        'stroke-linecap="round" stroke-linejoin="round"/></svg>')

# the same tokens as frontend/style.css (light theme)
PAGE_CSS = """
<style>
:root { --bg:#f5f6f8; --surface:#fff; --surface-2:#f0f2f5; --ink:#141a24; --ink-2:#3b4452; --muted:#66707f;
  --line:#e2e6ec; --line-strong:#cdd3dc; --accent:#2b5bd7; --accent-strong:#1f47b3; --accent-soft:#e8eefc;
  --ok:#157f4a; --ok-soft:#e3f4ea; --warn:#a15c07; --warn-soft:#fbf0dc; --bad:#b42318; --bad-soft:#fbe7e5; }
html, body, [data-testid="stAppViewContainer"], .stApp { background: var(--bg) !important; color: var(--ink);
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; }
header[data-testid="stHeader"], footer, #MainMenu, [data-testid="stToolbar"], [data-testid="stSidebar"],
[data-testid="collapsedControl"], [data-testid="stSidebarCollapsedControl"] { display: none !important; }
.stApp button, .stApp input, .stApp textarea, .stApp label, .stApp p, .stApp li, .stApp [data-baseweb="select"] *,
.stApp [data-testid="stMarkdownContainer"] { font-family: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif !important; }
.block-container { max-width: 1120px !important; padding: 0 20px 40px !important; }
.block-container > div { gap: 1.1rem; }

/* top bar */
.ra-top { display:flex; align-items:center; justify-content:space-between; gap:14px; flex-wrap:wrap; padding:12px 0;
  margin: 0 0 6px; border-bottom:1px solid var(--line); }
.ra-brand { display:flex; align-items:center; gap:11px; } .ra-logo { width:34px; height:34px; flex:none; }
.ra-name { font-weight:650; font-size:15.5px; letter-spacing:-.01em; } .ra-sub { font-size:12px; color:var(--muted); margin-top:-1px; }
.ra-chips { display:flex; gap:6px; flex-wrap:wrap; }
.ra-chip { display:inline-flex; align-items:center; font-size:12px; padding:3px 10px; border-radius:999px; background:var(--surface-2);
  border:1px solid var(--line); color:var(--muted); line-height:1.5; }
.ra-chip.ok::before, .ra-chip.off::before { content:""; width:7px; height:7px; border-radius:50%; margin-right:6px; flex:none; }
.ra-chip.ok::before { background:var(--ok); } .ra-chip.off::before { background:var(--line-strong); }
.ra-chip.active { border-color:var(--accent); color:var(--accent); font-weight:600; background:var(--accent-soft); }
.ra-chip.llm { color:var(--accent); background:var(--accent-soft); border-color:transparent; font-weight:600; }
.ra-mono { font-family: ui-monospace, "Cascadia Mono", Consolas, monospace; }

/* hero card: the keyed container becomes the card */
.st-key-hero { background:var(--surface); border:1px solid var(--line); border-radius:12px; padding:34px 34px 26px !important;
  box-shadow:0 1px 2px rgba(16,24,40,.05), 0 1px 3px rgba(16,24,40,.06); }
.ra-eyebrow { font-size:12px; font-weight:650; letter-spacing:.08em; text-transform:uppercase; color:var(--accent); margin-bottom:8px; }
.ra-h1 { display:block; font-size:clamp(25px,3.4vw,34px); line-height:1.25; letter-spacing:-.01em; font-weight:700; max-width:22ch; margin:0 0 6px; }
.ra-lead { color:var(--ink-2); max-width:62ch; margin:10px 0 18px; font-size:16px; line-height:1.55; }
.st-key-hero label p, .st-key-hero [data-testid="stWidgetLabel"] p { font-size:12.5px !important; font-weight:600 !important; color:var(--ink-2) !important; }
.st-key-hero textarea, .st-key-hero input { border-radius:9px !important; font-size:15px !important; }
.st-key-hero textarea { font-size:16px !important; }
.st-key-hero [data-baseweb="textarea"], .st-key-hero [data-baseweb="input"], .st-key-hero [data-baseweb="base-input"], .st-key-hero [data-baseweb="select"] > div {
  border-radius:9px !important; border:1px solid var(--line-strong) !important; background:#fff !important; }
.st-key-hero [data-baseweb="base-input"] { border:0 !important; }
.st-key-hero [data-testid="stTextAreaRootElement"], .st-key-hero [data-testid="stTextInputRootElement"],
.st-key-hero [data-testid="stNumberInputContainer"], .st-key-hero [data-testid="stSelectbox"] [data-baseweb="select"] > div {
  border:1px solid var(--line-strong) !important; border-radius:9px !important; background:#fff !important; }
.st-key-hero [data-testid="stSelectbox"] [class*="ComboBox"] > div { border:1px solid var(--line-strong) !important; border-radius:9px !important; background:#fff !important; }
.st-key-hero [data-testid="stTextAreaRootElement"]:focus-within, .st-key-hero [data-testid="stTextInputRootElement"]:focus-within,
.st-key-hero [data-testid="stNumberInputContainer"]:focus-within { border-color:var(--accent) !important; box-shadow:0 0 0 3px rgba(43,91,215,.22) !important; }
.st-key-hero [data-baseweb="textarea"]:focus-within, .st-key-hero [data-baseweb="input"]:focus-within, .st-key-hero [data-baseweb="select"]:focus-within > div {
  border-color:var(--accent) !important; box-shadow:0 0 0 3px rgba(43,91,215,.22) !important; }
[class*="st-key-ex-"] button { min-height:0 !important; padding:4px 11px !important; border-radius:999px !important; font-size:12.5px !important;
  background:var(--surface-2) !important; border:1px solid var(--line) !important; color:var(--ink-2) !important; font-weight:400 !important; }
[class*="st-key-ex-"] button:hover { border-color:var(--accent) !important; color:var(--accent) !important; background:var(--accent-soft) !important; }
[class*="st-key-ex-"] button p { font-size:12.5px !important; }
.st-key-run button { background:var(--accent) !important; color:#fff !important; border:0 !important; border-radius:9px !important; font-weight:600 !important;
  min-height:42px; width:100%; }
.st-key-run button:hover { background:var(--accent-strong) !important; }
.st-key-run button:disabled { opacity:.55; }
.ra-how { list-style:none; margin:20px 0 0; padding:18px 0 0; border-top:1px solid var(--line); display:grid; grid-template-columns:repeat(5,1fr); gap:14px; counter-reset:s; }
.ra-how li { counter-increment:s; position:relative; padding-left:30px; font-size:13px; color:var(--muted); }
.ra-how li::before { content:counter(s); position:absolute; left:0; top:0; width:22px; height:22px; border-radius:50%; display:grid; place-items:center;
  font-size:11.5px; font-weight:700; color:var(--accent); background:var(--accent-soft); }
.ra-how b { display:block; color:var(--ink); font-size:13.5px; }

/* progress card */
.ra-card { background:var(--surface); border:1px solid var(--line); border-radius:12px; padding:24px 26px;
  box-shadow:0 1px 2px rgba(16,24,40,.05), 0 1px 3px rgba(16,24,40,.06); }
.ra-ph { display:flex; justify-content:space-between; align-items:flex-start; gap:14px; flex-wrap:wrap; margin-bottom:14px; }
.ra-ph h2 { font-size:19px; margin:0; letter-spacing:-.01em; } .ra-ph p { margin:2px 0 0; color:var(--muted); font-size:13px; }
.ra-meta { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
.ra-pbar { height:6px; border-radius:999px; background:var(--surface-2); overflow:hidden; margin-bottom:14px; }
.ra-pbar span { display:block; height:100%; background:var(--accent); border-radius:999px; transition:width .6s ease; }
.ra-live { display:flex; gap:10px; align-items:center; padding:10px 14px; border-radius:9px; background:var(--accent-soft); margin-bottom:18px; min-height:42px; font-size:14px; }
.ra-live.done { background:var(--ok-soft); } .ra-live.failed { background:var(--bad-soft); color:var(--bad); }
.ra-live .t { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.ra-spin { flex:none; width:15px; height:15px; border-radius:50%; border:2px solid rgba(43,91,215,.25); border-top-color:var(--accent); animation:ra-spin .8s linear infinite; }
@keyframes ra-spin { to { transform:rotate(360deg); } }
.ra-crews { display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:18px; }
.ra-crew h3 { margin:0 0 8px; font-size:11.5px; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); }
.ra-agent { display:flex; align-items:center; gap:11px; padding:7px 10px; border-radius:9px; color:var(--muted); font-size:14px; }
.ra-agent .ico { flex:none; width:20px; height:20px; border-radius:50%; border:2px solid var(--line-strong); display:grid; place-items:center; font-size:12px; color:#fff; background:#fff; }
.ra-agent.working { color:var(--ink); background:var(--accent-soft); font-weight:600; }
.ra-agent.working .ico { border-color:var(--accent); border-top-color:transparent; animation:ra-spin .8s linear infinite; }
.ra-agent.done { color:var(--ink); } .ra-agent.done .ico { background:var(--ok); border-color:var(--ok); }
.ra-agent.done .ico::after { content:"\\2713"; font-weight:700; } .ra-agent.skipped .ico::after { content:"\\2013"; color:var(--muted); }
.ra-foot { text-align:center; color:var(--muted); font-size:12.5px; padding:6px 0 0; }
@media (max-width:720px) { .ra-how { grid-template-columns:1fr 1fr; } .st-key-hero { padding:22px 16px !important; } }
</style>
"""


def header_html(providers: dict | None) -> str:
    """Top bar with the brand and live provider chips (same labels as the local UI)."""
    chips = []
    p = (providers or {}).get("providers", {}) or {}
    mode = (providers or {}).get("mode", "")
    for key, label in (("gemini", "Gemini"), ("groq", "Groq")):
        ok = bool(p.get(key, {}).get("healthy"))
        active = mode == key
        chips.append(f'<span class="ra-chip {"ok" if ok else "off"}{" active" if active else ""}">{label}{" · active" if active else ""}</span>')
    if providers:
        chips.append(f'<span class="ra-chip {"ok" if p.get("serper", {}).get("healthy") else "off"}">Search</span>')
    return (f'<div class="ra-top"><div class="ra-brand">{LOGO}<div><div class="ra-name">Research Assistant</div>'
            f'<div class="ra-sub">Cited, fact-checked reports</div></div></div><div class="ra-chips">{"".join(chips)}</div></div>')


def hero_head_html() -> str:
    return ('<p class="ra-eyebrow">New research brief</p><div class="ra-h1">Research any topic with sources you can check.</div>'
            '<p class="ra-lead">Specialist agents plan the questions, gather evidence, draft the report and verify each claim '
            'against the page it cites.</p>')


def how_html() -> str:
    steps = [("Plan", "Break the topic into questions"), ("Research", "Search and read sources"),
             ("Write", "Draft from the evidence only"), ("Verify", "Check claims against pages"),
             ("Edit", "Cut what cannot be confirmed")]
    return '<ol class="ra-how">' + "".join(f"<li><b>{a}</b><span>{b}</span></li>" for a, b in steps) + "</ol>"


def progress_html(crews: dict[str, dict], percent: float, elapsed: str, model: str, live: str,
                  status: str = "running") -> str:
    """The progress card, same structure as the local UI's."""
    done = sum(s in ("done", "skipped") for c in crews.values() for s in c["state"])
    total = sum(len(c["agents"]) for c in crews.values())
    title = {"running": "Working…", "done": "Pipeline finished", "error": "The run failed"}[status]
    sub = ("All stages complete" if status == "done" else
           f"{done} of {total} agents finished so far" if total else "Starting the pipeline")
    live_cls = {"done": " done", "error": " failed"}.get(status, "")
    spinner = '<span class="ra-spin"></span>' if status == "running" else ""
    cols = []
    for name, c in crews.items():
        rows = "".join(f'<div class="ra-agent {s or ""}"><span class="ico"></span><span>{html.escape(a)}</span></div>'
                       for a, s in zip(c["agents"], c["state"]))
        cols.append(f'<div class="ra-crew"><h3>{html.escape(name)}</h3>{rows}</div>')
    model_chip = f'<span class="ra-chip llm">Model: {html.escape(model)}</span>' if model else ""
    width = 100 if status == "done" else max(0, min(99, round(percent * 100)))
    return (f'<div class="ra-card"><div class="ra-ph"><div><h2>{title}</h2><p>{sub}</p></div>'
            f'<div class="ra-meta">{model_chip}<span class="ra-chip ra-mono">{html.escape(elapsed)}</span></div></div>'
            f'<div class="ra-pbar"><span style="width:{width}%"></span></div>'
            f'<div class="ra-live{live_cls}">{spinner}<span class="t">{html.escape(live)}</span></div>'
            f'<div class="ra-crews">{"".join(cols)}</div></div>')


# --------------------------------------------------- the report: local UI --

_HIDE_CHROME = ("<style>.top,#form-card,#progress-card,.foot,.skip{display:none !important}"
                "html,body{background:#f5f6f8 !important;overflow-y:hidden}main{padding-top:6px !important}</style>")


def result_document(result: dict, pdf: bytes | None = None, frontend: Path = FRONTEND) -> str:
    """A self-contained page that renders `result` with the local UI's own code."""
    page = (frontend / "index.html").read_text(encoding="utf-8")
    css = (frontend / "style.css").read_text(encoding="utf-8")
    js = (frontend / "app.js").read_text(encoding="utf-8")

    data = dict(result)
    data["__pdf"] = base64.b64encode(pdf).decode("ascii") if pdf else ""
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")

    boot = f"""
<script>
window.__DATA__ = {payload};
try {{ localStorage.removeItem("theme"); }} catch (e) {{}}
document.documentElement.dataset.theme = "light";
window.fetch = () => Promise.resolve({{ json: () => Promise.resolve({{}}) }});   // no API behind this page
Element.prototype.scrollIntoView = function () {{}};                              // the parent page owns scrolling
window.addEventListener("load", () => {{
  document.documentElement.dataset.theme = "light";
  showResult(window.__DATA__);
  const pdf = $("dl-pdf");
  if (pdf) {{
    const btn = pdf.cloneNode(true); pdf.replaceWith(btn);
    if (window.__DATA__.__pdf) {{
      btn.addEventListener("click", () => {{
        const bin = atob(window.__DATA__.__pdf), buf = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
        saveBlob(new Blob([buf], {{ type: "application/pdf" }}), slug() + ".pdf");
      }});
    }} else {{ btn.hidden = true; }}
  }}
  const fit = () => {{ if (window.frameElement) window.frameElement.style.height = (document.documentElement.scrollHeight + 8) + "px"; }};
  new ResizeObserver(fit).observe(document.body); fit();
}});
</script>"""
    page = page.replace('<link rel="stylesheet" href="style.css">', f"<style>{css}</style>{_HIDE_CHROME}")
    return page.replace('<script src="app.js"></script>', f"<script>{js}</script>{boot}")
