"use strict";
const $ = (id) => document.getElementById(id);
const API = ""; // same origin: FastAPI serves this page

const PROVIDER_LABEL = { gemini: "Gemini", groq: "Groq", ollama: "Ollama", fixture: "Offline fixtures" };
// share of the whole run each crew represents, for the progress bar
const CREW_WEIGHT = { "Research crew": 0.55, "Editorial crew": 0.45 };
let timerHandle = null, pollHandle = null, report = null, tocObserver = null;
const crewState = {}; // crew name -> { total, done:Set }

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtTime = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const short = (s, n = 70) => (s.length > n ? s.slice(0, n - 1) + "…" : s);
const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : "#");

/* ------------------------------------------------------------------ theme */
function applyTheme(t) { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }
try { applyTheme(localStorage.getItem("theme")); } catch (e) { /* storage unavailable */ }
$("theme").addEventListener("click", () => {
  const dark = document.documentElement.dataset.theme
    ? document.documentElement.dataset.theme === "dark"
    : window.matchMedia("(prefers-color-scheme: dark)").matches;
  const next = dark ? "light" : "dark";
  applyTheme(next);
  try { localStorage.setItem("theme", next); } catch (e) { /* ignore */ }
});

/* ------------------------------------------------------------------ toast */
let toastHandle = null;
function toast(msg) {
  const el = $("toast");
  el.textContent = msg; el.hidden = false;
  clearTimeout(toastHandle);
  toastHandle = setTimeout(() => { el.hidden = true; }, 3500);
}

/* ---------------------------------------------------------- provider chips */
async function loadHealth() {
  try {
    const h = await (await fetch(`${API}/health`)).json();
    const p = h.providers || {};
    const chips = ["gemini", "groq", "ollama"].map((name) => {
      const ok = p[name] && p[name].healthy;
      const active = h.mode === name;
      return `<span class="chip ${ok ? "ok" : "off"} ${active ? "active" : ""}">${PROVIDER_LABEL[name]}${active ? " · active" : ""}</span>`;
    });
    chips.push(`<span class="chip ${p.serper && p.serper.healthy ? "ok" : "off"}">Search</span>`);
    $("providers").innerHTML = chips.join("");
    if (h.mode === "fixture") $("providers").insertAdjacentHTML("beforeend", `<span class="chip warn">offline fixture mode</span>`);
  } catch {
    $("providers").innerHTML = `<span class="chip off">API unreachable</span>`;
  }
}

/* ---------------------------------------------------------------- progress */
function addLog(text, t) {
  const li = document.createElement("li");
  li.textContent = `[${fmtTime(t || 0)}] ${text}`;
  $("log").prepend(li);
}
function setLive(text) { $("live-text").textContent = text; }

function updateProgress() {
  const crews = Object.entries(crewState);
  const bar = $("pbar");
  if (!crews.length) { bar.classList.add("indeterminate"); return; }
  bar.classList.remove("indeterminate");
  // a crew that has not announced itself yet still owns its share of the bar
  let pct = 0, doneAgents = 0, totalAgents = 0;
  for (const [name, c] of crews) {
    pct += (CREW_WEIGHT[name] ?? 0.5) * (c.total ? c.done.size / c.total : 0);
    doneAgents += c.done.size; totalAgents += c.total;
  }
  const value = Math.min(99, Math.round(pct * 100));
  $("pbar-fill").style.width = `${value}%`;
  bar.setAttribute("aria-valuenow", String(value));
  $("stage-text").textContent = `${doneAgents} of ${totalAgents} agents finished so far`;
}

function renderCrew(ev) {
  crewState[ev.crew] = { total: ev.agents.length, done: new Set() };
  const el = document.createElement("div");
  el.className = "crew";
  el.dataset.crew = ev.crew;
  el.innerHTML = `<h3>${esc(ev.crew)}</h3>` + ev.agents.map((a, i) =>
    `<div class="agent" data-i="${i}"><span class="ico"></span><span>${esc(a)}</span><span class="tool"></span></div>`).join("");
  $("crews").appendChild(el);
  updateProgress();
}

function agentEl(ev) {
  return document.querySelector(`.crew[data-crew="${CSS.escape(ev.crew)}"] .agent[data-i="${ev.index}"]`);
}

let currentAgent = null;
function handle(ev) {
  switch (ev.kind) {
    case "plan":
      renderCrew(ev);
      addLog(`${ev.crew}: ${ev.agents.length} agents queued`, ev.t);
      break;
    case "agent": {
      const el = agentEl(ev);
      if (!el) break;
      el.className = `agent ${ev.state}`;
      if (ev.state === "working") {
        currentAgent = el;
        setLive(`${ev.name} is working…`);
        addLog(`${ev.name} started`, ev.t);
      } else {
        el.querySelector(".tool").textContent = "";
        addLog(`${ev.name} ${ev.state}`, ev.t);
        if (crewState[ev.crew]) { crewState[ev.crew].done.add(ev.index); updateProgress(); }
      }
      break;
    }
    case "tool": {
      const label = { "web search": "Searching the web", "news search": "Searching news",
                      "scholar search": "Searching scholar", "fetching page": "Reading page" }[ev.tool] || ev.tool;
      setLive(`${label}: ${short(ev.detail, 80)}`);
      if (currentAgent) currentAgent.querySelector(".tool").textContent = `${ev.tool}: ${short(ev.detail, 38)}`;
      addLog(`${ev.tool}: ${ev.detail}`, ev.t);
      break;
    }
    case "llm": {
      const name = PROVIDER_LABEL[ev.provider] || ev.provider;
      $("llm").textContent = `Model: ${name}${ev.model ? " · " + ev.model.replace(/^.*\//, "") : ""}`;
      addLog(`Model backend: ${name} (${ev.model})`, ev.t);
      break;
    }
    default:
      setLive(ev.message || "");
      addLog(ev.message || "", ev.t);
  }
}

/* ------------------------------------------------------------------ result */
const VERDICT_STYLE = { VERIFIED: ["k-ok", "Verified"], UNSUPPORTED: ["k-warn", "Unsupported"], STALE: ["k-warn", "Source unavailable"], CONTRADICTED: ["k-bad", "Contradicted"] };

function renderStats(r, grade) {
  const m = r.metrics || {};
  const claims = r.claims || [];
  const verified = claims.filter((c) => c.verdict === "VERIFIED").length;
  const cited = (r.references || []).filter((x) => x.role !== "further_reading").length;
  const stat = (value, label, cls = "") => `<div class="stat ${cls}"><b>${esc(value)}</b><span>${esc(label)}</span></div>`;
  const tiles = [];
  if (grade) tiles.push(stat(grade, "Confidence", `grade ${esc(grade)}`));
  tiles.push(stat(claims.length ? `${verified}/${claims.length}` : "–", "Claims verified"));
  tiles.push(stat(cited || "–", "Sources cited"));
  tiles.push(stat(fmtTime(m.duration_s || 0), "Run time"));
  $("metrics").innerHTML = tiles.join("");

  const bar = $("verdict-bar");
  if (!claims.length) { bar.hidden = true; return; }
  const counts = {};
  claims.forEach((c) => { counts[c.verdict] = (counts[c.verdict] || 0) + 1; });
  const order = ["VERIFIED", "UNSUPPORTED", "STALE", "CONTRADICTED"].filter((k) => counts[k]);
  const color = { "k-ok": "var(--ok)", "k-warn": "var(--warn)", "k-bad": "var(--bad)" };
  bar.hidden = false;
  bar.innerHTML = `<div class="bar" role="img" aria-label="Claim verification breakdown">` +
    order.map((k) => `<i style="width:${(counts[k] / claims.length) * 100}%;background:${color[VERDICT_STYLE[k][0]]}"></i>`).join("") +
    `</div><div class="legend">` +
    order.map((k) => `<em class="${VERDICT_STYLE[k][0]}"><span>${counts[k]} ${VERDICT_STYLE[k][1].toLowerCase()}</span></em>`).join("") + `</div>`;
}

function renderBanner(r) {
  const el = $("banner");
  if (r.status === "degraded") {
    el.className = "banner warn"; el.hidden = false;
    el.innerHTML = `<b>Provisional report.</b> The findings below come straight from the sources and were not fact-checked. Read each source before relying on a figure, or run the research again for the verified version.`;
  } else if ((r.metrics || {}).repair_needed) {
    el.className = "banner warn"; el.hidden = false;
    el.innerHTML = `<b>Some claims could not be confirmed.</b> They are hedged or left out of the report. See Claim verification below.`;
  } else {
    el.hidden = true;
  }
}

/* turn "[3]" in the rendered report into a link to reference 3 */
function linkCitations(root, refs) {
  const cited = refs.filter((x) => x.role !== "further_reading");
  if (!cited.length) return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: (n) => (n.parentElement.closest("a, code, pre") || !/\[\d+\]/.test(n.nodeValue) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
  });
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  for (const node of nodes) {
    const frag = document.createDocumentFragment();
    const parts = node.nodeValue.split(/(\[\d+\])/);
    parts.forEach((part, i) => {
      const m = /^\[(\d+)\]$/.exec(part);
      const ref = m && cited[Number(m[1]) - 1];
      if (!ref) {
        // no stray space before a citation mark, and none between two adjacent marks
        const nextIsCite = /^\[\d+\]$/.test(parts[i + 1] || "") && cited[Number(/\d+/.exec(parts[i + 1])[0]) - 1];
        const prevIsCite = i > 0 && /^\[\d+\]$/.test(parts[i - 1]) && cited[Number(/\d+/.exec(parts[i - 1])[0]) - 1];
        let text = nextIsCite ? part.replace(/\s+$/, "") : part;
        if (prevIsCite && nextIsCite && !text.trim()) text = "";
        if (text) frag.appendChild(document.createTextNode(text));
        return;
      }
      const a = document.createElement("a");
      a.className = "cite"; a.href = `#ref-${m[1]}`; a.textContent = m[1];
      a.title = `${ref.title || ref.url}${ref.publisher ? " · " + ref.publisher : ""}`;
      a.addEventListener("click", () => { $("refs-wrap").scrollIntoView(); });
      frag.appendChild(a);
    });
    node.parentNode.replaceChild(frag, node);
  }
}

function buildToc(root) {
  const heads = [...root.querySelectorAll("h2")];
  const list = $("toc");
  list.innerHTML = "";
  if (tocObserver) tocObserver.disconnect();
  $("toc-wrap").hidden = heads.length < 3;
  if (heads.length < 3) return;
  heads.forEach((h, i) => {
    h.id = `sec-${i + 1}`;
    const li = document.createElement("li");
    li.innerHTML = `<a href="#sec-${i + 1}">${esc(h.textContent)}</a>`;
    list.appendChild(li);
  });
  tocObserver = new IntersectionObserver((entries) => {
    entries.forEach((e) => {
      if (e.isIntersecting) {
        list.querySelectorAll("a").forEach((a) => a.classList.toggle("on", a.getAttribute("href") === `#${e.target.id}`));
      }
    });
  }, { rootMargin: "-80px 0px -65% 0px" });
  heads.forEach((h) => tocObserver.observe(h));
}

function renderRefs(refs) {
  const card = (x, n) =>
    `<div class="ref"${n ? ` id="ref-${n}"` : ""}><div class="n">${n ? `[${n}]` : "·"}</div><div>` +
    `<a class="t" href="${esc(safeUrl(x.url))}" target="_blank" rel="noopener noreferrer">${esc(x.title || x.url)}</a>` +
    `<div class="m">${[x.publisher, x.date, x.source_type].filter(Boolean).map((v, i) => i === 2 ? `<span class="chip">${esc(v)}</span>` : `<span>${esc(v)}</span>`).join("")}</div>` +
    (x.note ? `<div class="note">${esc(x.note)}</div>` : "") +
    (x.quote ? `<div class="q">“${esc(x.quote)}”</div>` : "") +
    `<div class="u">${esc(x.url)}</div></div></div>`;
  const cited = refs.filter((x) => x.role !== "further_reading");
  const more = refs.filter((x) => x.role === "further_reading");
  $("refs-count").textContent = cited.length + more.length ? String(cited.length + more.length) : "";
  $("refs").innerHTML = cited.map((x, i) => card(x, i + 1)).join("") +
    (more.length ? `<div class="subhead">Further reading</div>` + more.map((x) => card(x, 0)).join("") : "") ||
    `<p class="muted">No references.</p>`;
}

function showResult(r) {
  report = r;
  $("progress-card").hidden = false;
  $("live").classList.add("done");
  setLive("Finished");
  $("pbar").classList.remove("indeterminate");
  $("pbar-fill").style.width = "100%";
  $("stage-text").textContent = "All stages complete";
  $("progress-card").querySelector("h2").textContent = "Pipeline finished";
  $("progress-card").classList.add("compact"); // the report takes over; keep only the header and log

  const md = r.report_md || "";
  const grade = (/\*\*Confidence:\*\*\s*([ABC])\b/.exec(md) || [])[1] || "";
  renderStats(r, grade);
  renderBanner(r);

  const html = window.marked ? marked.parse(md) : `<pre>${esc(md)}</pre>`;
  const out = $("report");
  out.innerHTML = window.DOMPurify ? DOMPurify.sanitize(html) : html;

  // the document title lives in the page header; the contents list is replaced by the side panel
  const h1 = out.querySelector("h1");
  $("result-title").textContent = h1 ? h1.textContent : (r.topic || "Report");
  if (h1) h1.remove();
  const contents = [...out.querySelectorAll("h2")].find((h) => /^contents$/i.test(h.textContent.trim()));
  if (contents) { const next = contents.nextElementSibling; contents.remove(); if (next && /^(OL|UL)$/.test(next.tagName)) next.remove(); }

  linkCitations(out, r.references || []);
  buildToc(out);

  const claims = r.claims || [];
  $("claims-count").textContent = claims.length ? String(claims.length) : "";
  $("claims").innerHTML = claims.map((c) =>
    `<div class="claim"><span class="cid">${esc(c.id)}</span><span class="v ${esc(c.verdict)}">${esc((VERDICT_STYLE[c.verdict] || [0, c.verdict])[1])}</span><span>${esc(c.statement)}</span></div>`).join("")
    || `<p class="muted small">No claim-level data for this report.</p>`;
  renderRefs(r.references || []);

  $("result-card").hidden = false;
  $("result-card").scrollIntoView({ behavior: "smooth", block: "start" });
}

function showError(msg) {
  $("error").hidden = false;
  $("error").textContent = msg;
}

function finish() {
  clearInterval(timerHandle); clearTimeout(pollHandle);
  $("go").disabled = false;
  $("go").textContent = "Run research";
  loadHealth();
}

/* -------------------------------------------------------------------- run */
async function poll(id, since, started) {
  try {
    const r = await fetch(`${API}/jobs/${id}?since=${since}`);
    if (!r.ok) throw new Error(`status ${r.status}`);
    const s = await r.json();
    s.events.forEach(handle);
    if (s.status === "done") { finish(); return showResult(s.result); }
    if (s.status === "error") {
      finish();
      $("live").classList.add("failed");
      setLive("The run failed");
      return showError(s.error || "Pipeline failed");
    }
    pollHandle = setTimeout(() => poll(id, s.next, started), 1000);
  } catch (e) {
    pollHandle = setTimeout(() => poll(id, since, started), 2500); // transient: keep trying
  }
}

document.querySelectorAll(".eg").forEach((b) => b.addEventListener("click", () => {
  $("topic").value = b.textContent; $("topic").focus();
}));

$("form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("error").hidden = true;
  $("result-card").hidden = true;
  $("crews").innerHTML = ""; $("log").innerHTML = "";
  Object.keys(crewState).forEach((k) => delete crewState[k]);
  $("live").classList.remove("done", "failed");
  $("progress-card").classList.remove("compact");
  $("progress-card").querySelector("h2").textContent = "Working…";
  $("stage-text").textContent = "Starting the pipeline";
  $("pbar-fill").style.width = "0%";
  $("llm").textContent = "Model: detecting…";
  $("progress-card").hidden = false;
  updateProgress();
  setLive("Starting…");
  $("go").disabled = true; $("go").textContent = "Running…";

  const t0 = Date.now();
  $("timer").textContent = "0:00";
  timerHandle = setInterval(() => ($("timer").textContent = fmtTime((Date.now() - t0) / 1000)), 500);
  $("progress-card").scrollIntoView({ behavior: "smooth", block: "start" });

  try {
    const res = await fetch(`${API}/jobs`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        topic: $("topic").value.trim(), audience: $("audience").value.trim() || "general professional",
        depth: $("depth").value, max_searches: Number($("max").value) || 20,
      }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = body.detail;
      throw new Error(typeof d === "string" ? d : JSON.stringify(d || body));
    }
    poll(body.id, 0, t0);
  } catch (err) {
    finish();
    $("progress-card").hidden = true;
    showError(err.message);
  }
});

/* --------------------------------------------------------------- downloads */
function saveBlob(blob, name) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}
const slug = () => (report.topic || $("result-title").textContent || "report").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 50) || "report";

function fullMarkdown() {
  let md = report.report_md || "";
  if ((report.references || []).length) {
    const line = (r, i) => `${i}. ${r.title || r.url}${r.publisher ? " — " + r.publisher : ""}${r.date ? " (" + r.date + ")" : ""} <${r.url}>` +
      (r.note ? `\n   - ${r.note}` : "");
    const cited = report.references.filter((r) => r.role !== "further_reading");
    const more = report.references.filter((r) => r.role === "further_reading");
    md += "\n\n## References\n\n" + cited.map((r, i) => line(r, i + 1)).join("\n") + "\n";
    if (more.length) md += "\n## Further reading\n\n" + more.map((r, i) => line(r, i + 1)).join("\n") + "\n";
  }
  return md;
}

$("dl-md").addEventListener("click", () => {
  saveBlob(new Blob([fullMarkdown()], { type: "text/markdown;charset=utf-8" }), `${slug()}.md`);
});

$("copy").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText(fullMarkdown()); toast("Report copied as Markdown"); }
  catch (e) { toast("Copy is blocked by the browser; use the Markdown download"); }
});

$("dl-bib").addEventListener("click", () => {
  const NL = String.fromCharCode(10);
  const year = (d) => ((String(d || "").match(/(?:19|20)[0-9]{2}/) || [""])[0]);
  const tex = (t) => String(t || "").replace(/[{}]/g, "").split(String.fromCharCode(92)).join("");
  const today = new Date().toISOString().slice(0, 10);
  const entries = (report.references || []).map((r, i) => {
    let host = "src";
    try { host = new URL(r.url).hostname.replace(/^www[.]/, ""); } catch (e) { /* keep default */ }
    const key = host.replace(/[^a-z0-9]/gi, "") + (year(r.date) || "nd") + "_" + (i + 1);
    const lines = [
      "@misc{" + key + ",",
      "  title = {" + tex(r.title || r.url) + "},",
      "  author = {" + tex(r.publisher || host) + "},",
    ];
    if (year(r.date)) lines.push("  year = {" + year(r.date) + "},");
    lines.push("  url = {" + r.url + "},", "  note = {Accessed " + today + "}", "}");
    return lines.join(NL);
  });
  saveBlob(new Blob([entries.join(NL + NL) + NL], { type: "application/x-bibtex;charset=utf-8" }), slug() + ".bib");
});

$("dl-pdf").addEventListener("click", async () => {
  const btn = $("dl-pdf"); btn.disabled = true; btn.textContent = "Building PDF…";
  try {
    const res = await fetch(`${API}/export/pdf`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ topic: report.topic, audience: report.audience, depth: report.depth,
                             report_md: report.report_md, references: report.references, metrics: report.metrics }),
    });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `status ${res.status}`);
    saveBlob(await res.blob(), `${slug()}.pdf`);
  } catch (err) {
    toast("PDF export failed: " + err.message);
  } finally {
    btn.disabled = false; btn.textContent = "Download PDF";
  }
});

loadHealth();
