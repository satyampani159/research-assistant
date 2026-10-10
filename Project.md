# Project.md — Multi-Agent Research Assistant

> Status: design frozen · target build ~7h + 2h integration · 3 developers (see `implementation.md`)
> Note: this is a standalone project. It shares nothing with any other codebase.

---

## 1. Understanding the project

### 1.1 One-liner

A crew of specialized AI agents — researcher, writer, fact-checker, editor — that autonomously turns a `topic + audience + depth` into a cited, publication-ready report. Orchestrated by **n8n**, powered by **CrewAI**, grounded in live web data by the **Serper API**.

### 1.2 Problem it solves

Manual research and drafting across multiple sources is slow and error-prone: people juggle dozens of tabs, lose track of which claim came from which source, and publish numbers they never verified. The result is reports that are expensive to write and untrustworthy to read.

### 1.3 Goal

`topic, audience, depth` in → `{report_md, references, per-claim verdicts, cost/time metrics}` out — with **zero human hand-offs between stages** and **zero hallucinated citations**.

### 1.4 Users and practical applications

| User | What they get | Example |
|---|---|---|
| Content teams | Sourced briefs/articles with traceable references | "State of AI coding assistants, 2026" |
| Academic research | Scholar-grounded literature summaries | "Recent advances in retrieval-augmented generation" |
| Competitive intelligence | Recurring briefs on a schedule | "Weekly: rival X pricing and feature moves" |

### 1.5 Inputs and outputs

| Input | Type | Default |
|---|---|---|
| `topic` | string (required) | — |
| `audience` | string | `"general professional"` |
| `depth` | `"brief" \| "standard" \| "deep"` | `"standard"` |
| `max_searches` | int (Serper budget guard) | `40` |

| Output | Description |
|---|---|
| `report_md` | Full report in Markdown, inline `[Sn]` citations |
| `references[]` | `{url, title, publisher, date}` — every cited source |
| `claims[]` | `{id, statement, verdict, confidence}` per atomic claim |
| `metrics{}` | `{tokens, serper_credits, duration_s, repair_needed}` |

### 1.6 Success criteria (measured, not vibes)

| Metric | Target |
|---|---|
| Citation coverage (cited claims / total claims) | ≥ 90% |
| Verification rate (VERIFIED / total) after repair | ≥ 85% |
| Hallucinated citations (URL not in evidence table) | **0** |
| Source diversity (unique domains) | ≥ 6 |
| Serper credits per report | ≤ 60 |
| Cost + wall time per report | measured, printed, committed to README |
| Fix-loop trigger rate | measured (if 0%, the loop is decoration — investigate) |

### 1.7 Scope

**In:** 2 CrewAI crews (research-hierarchical, editorial-sequential), 5 read-only-ish tools, 4 deterministic guardrails, 1 bounded repair pass owned by n8n, FastAPI service, n8n workflow in Docker, offline unit tests, eval on 3 topics.

**Out:** no user-facing UI, no accounts/auth, no private-document RAG, no multi-language reports, no real-time streaming, no autonomous web browsing beyond Serper + page fetch.

### 1.8 Assumptions and constraints

- A Serper API key exists (2,500 free queries/month — enough if the `max_searches` budget is enforced).
- One LLM key exists (Anthropic **or** OpenAI); all model access goes through LiteLLM so switching vendors is a one-line env change.
- Docker is available (confirmed: Docker 29.6.1).
- A deep report takes 3–6 minutes end-to-end (Serper + several LLM calls); HTTP timeout must be ≥ 10 min.
- ⚠️ `crewai==1.15.23` requires `pydantic>=2.11.9,<2.13`. The Python engine therefore runs in its **own Docker container** with its own pinned deps — never installed into a shared interpreter.

---

## 2. Architecture

### 2.1 Big picture — two tiers

```
┌────────────────────── TIER 1: n8n (Docker) ──────────────────────┐
│  Trigger        Call          Decide         Deliver             │
│  Webhook /  →  POST /research → IF unverified → Google Docs /    │
│  Schedule /      │              > 0 AND        Slack / Email /    │
│  Google Sheet    │              attempt = 1    volume             │
│                  │                 │                             │
│                  │                 ▼ (one retry, no back-edge)    │
│                  │            POST /repair {failed_claims}        │
└──────────────────┼──────────────────────────────────────────────┘
                   │ HTTP (compose network: http://crew-api:8000)
┌──────────────────▼────── TIER 2: Python (Docker) ────────────────┐
│  api.py — FastAPI, 3 endpoints                                   │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ CREW 1 · RESEARCH · Process.hierarchical                    │ │
│  │   Lead Planner (manager_llm)                                │ │
│  │     └─ delegates to → Researcher ×3 (async) → Synthesizer   │ │
│  │   tools: search_web · search_news · search_scholar ·        │ │
│  │          fetch_page                                         │ │
│  ├────────────────────────────────────────────────────────────┤ │
│  │ CREW 2 · EDITORIAL · Process.sequential                     │ │
│  │   Writer → Fact-checker → Editor (+ 4 guardrails)           │ │
│  │   tools: verify_claim · numeric_check · fetch_page          │ │
│  └────────────────────────────────────────────────────────────┘ │
│  contracts.py · prompts.py · config.py · tools.py · quality.py  │
└──────────────────────────────────────────────────────────────────┘
```

**Why two tiers:** n8n owns everything visual and operational (triggers, branching, retries, delivery, execution history). Python owns everything cognitive (agents, tools, verification). The whole integration surface is 3 HTTP endpoints — neither side needs to know how the other works.

**Why no CrewAI Flow:** the repair loop lives in an n8n IF node (6 lines of JSON, visible on the canvas for the demo) instead of `@router` code. Same behavior, half the code, better demo.

### 2.2 Tier 1 — n8n workflow (node-by-node)

```
[Webhook] → [Set: topic,audience,depth,attempt=1] → [HTTP POST /research]
                                                        │
                      ┌─────────────────────────────────┤
                      ▼                                 │
[IF: repair_needed AND attempt=1]                       │
   true → [Set: attempt=2] → [HTTP POST /repair] → [Merge] ──┘
   false ───────────────────────────────────────────────┘
                      │
                      ▼
        [Code: format payload] → [Deliver: Slack + /data volume]
```

- Triggers: Webhook (demo), Schedule (autonomy over time), Google Sheet row (batch) — all three can coexist on one canvas.
- The branch is a **single retry pass, no back-edge** — infinite loops are impossible by construction.
- Serper (if ever needed directly in n8n) is one HTTP Request node: `POST https://google.serper.dev/search`, header `X-API-KEY`. No community node install required.

### 2.3 Tier 2 — agent roster

| Agent | Crew / process | Responsibility | Produces |
|---|---|---|---|
| **Lead Planner** | Crew 1, `manager_llm` | Decompose topic into 3–6 sub-questions with search queries; judge coverage sufficient; delegate to researchers | `ResearchPlan` |
| **Researcher** ×3 | Crew 1, delegated, `async_execution=True` | Answer exactly one sub-question using ≥3 independent domains; every claim carries URL + publisher + date | `ResearchNote[]` |
| **Synthesizer** | Crew 1 | Merge notes, dedupe by domain, flag contradictions, build the evidence table with claim IDs | `EvidenceTable` |
| **Writer** | Crew 2, sequential | Draft from the evidence table **only**; inline `[Sn]` citations; no uncited assertions | `DraftReport` |
| **Fact-checker** | Crew 2, sequential | Split draft into atomic claims; per claim: re-fetch source, run `numeric_check`, LLM entailment check | `VerificationLog` |
| **Editor** | Crew 2, sequential | Enforce house style; delete-or-hedge every non-VERIFIED claim; build reference list; assign confidence grade | `FinalReport` |

Autonomy mechanisms: manager delegation (sub-tasks assigned to the right role automatically), structured pydantic handoffs via `Task.context` + `output_pydantic` (no stringly-typed passing), and the n8n-owned repair pass that re-searches only failed claim IDs.

### 2.4 Tool inventory

| Tool | Source | Purpose |
|---|---|---|
| `SerperDevTool` | `crewai-tools` | Web + news search (`search_type` is `"search"` \| `"news"` only; env `SERPER_API_KEY`) — wrapped, not used raw |
| `search_web(query, num, gl)` | custom (`tools.py`) | Serper `/search` + `tenacity` backoff on 429/5xx + TTL cache + global budget → clean `[{title,url,snippet}]` |
| `search_news(query, num)` | custom | Serper `/news` → `[{title,url,snippet,source,date}]` |
| `search_scholar(query, num)` | custom | Serper `/scholar` (the stock tool doesn't cover it) → `[{title,authors,year,cited_by,url}]` |
| `fetch_page(url, max_chars)` | custom | requests + BeautifulSoup, strip nav/scripts/ads → `{title,text,retrieved_at}`; cached |
| `numeric_check(claim, source_text)` | custom, **pure Python, no LLM** | Extract figures from claim and source, compare with unit/scale normalization → `{match, claim_numbers, source_numbers, note}` |
| `verify_claim(claim_id, text, url)` | custom (`quality.py`) | Re-fetch + `numeric_check` + entailment → `VERIFIED \| UNSUPPORTED \| CONTRADICTED \| STALE` |
| `FileReadTool` / `FileWriterTool` | `crewai-tools` | Artifact read/write between stages |

### 2.5 Data contracts (pydantic — the handoff language)

```python
class ResearchNote(BaseModel):
    claim_id: str; sub_question_id: str; statement: str
    url: str; publisher: str; date: str | None
    retrieved_at: str; verbatim_quote: str
    source_tier: int  # 1 = primary/official, 2 = reputable press, 3 = other

class EvidenceTable(BaseModel):
    claims: list[dict]  # {claim_id, statement, supports[], conflicts[]}
    gaps: list[str]; contradictions: list[str]

class DraftReport(BaseModel):
    title: str; thesis: str
    sections: list[dict]  # {heading, body, cited_claims[]}

class ClaimResult(BaseModel):
    claim_id: str
    verdict: Literal["VERIFIED", "UNSUPPORTED", "CONTRADICTED", "STALE"]
    confidence: float; evidence_quote: str; checked_url: str; checked_at: str

class VerificationLog(BaseModel):
    results: list[ClaimResult]

class FinalReport(BaseModel):
    markdown: str; references: list[dict]  # {url,title,publisher,date}
    confidence_grade: Literal["A", "B", "C"]
    unverified_appendix: list[str]
```

Every task uses `output_pydantic=`. Raw strings between agents are forbidden — this is the #1 cause of flaky crews.

### 2.6 HTTP API (the entire n8n↔Python integration surface)

```
POST /research
  { topic, audience="general professional", depth="standard", max_searches=40 }
  → 200 { report_md, references[], claims[], metrics{tokens, serper_credits,
          duration_s, repair_needed: bool} }

POST /repair
  { topic, claims: [{id, statement}] }
  → 200 { repaired_claims[], serper_credits }

GET /health → 200 {"ok": true}
```

`metrics.repair_needed` is the single flag the n8n IF node branches on.

### 2.7 Repository layout

```
research-assistant/
  docker-compose.yml        # n8n + crew-api services
  Dockerfile                # python:3.12-slim + pinned deps
  requirements.txt
  .env.example              # SERPER_API_KEY, ANTHROPIC_API_KEY|OPENAI_API_KEY, MODEL
  src/
    api.py                  # FastAPI: 3 endpoints
    config.py               # Settings + LLM factory (LiteLLM)
    contracts.py            # §2.5 models
    prompts.py              # every agent prompt, one file
    tools.py                # §2.4 search/fetch tools
    quality.py              # verify_claim + guardrails
    crews/
      research_crew.py      # Crew 1
      editorial_crew.py     # Crew 2
  n8n/
    research-assistant.json # exported workflow (importable)
  fixtures/                 # canned Serper pages for offline tests
  tests/                    # offline unit tests (no keys)
  outputs/  (git-ignored)   # run artifacts
  Project.md  implementation.md  README.md
```

### 2.8 Technology stack

| Layer | Choice | Version | Why |
|---|---|---|---|
| Orchestration shell | n8n (Docker) | latest | Triggers, visual branching, delivery, execution history — no code |
| Agent framework | CrewAI | `==1.15.23` | Role-based crews + hierarchical delegation (assignment stack) |
| Agent tools pkg | crewai-tools | `==1.15.23` | `SerperDevTool`, scraper, file tools |
| LLM routing | LiteLLM | `>=1.84` | One interface for Anthropic/OpenAI/Gemini; switch via `MODEL` env |
| Models (default) | Claude Sonnet (writer/editor/planner), Haiku (researcher/fact-checker) | via `MODEL` env | Strong where quality matters, cheap where volume matters |
| Search | Serper API | — | `/search` + `/news` + `/scholar`, 2,500 free queries/mo |
| API | FastAPI + uvicorn | `>=0.115` | 3-endpoint bridge to n8n |
| Validation | Pydantic | `>=2.11.9,<2.13` | **Pinned: crewai 1.15 hard-requires `<2.13`** |
| HTTP/retry/parse | requests, tenacity, beautifulsoup4 | current | Tool internals |
| Runtime | Docker Compose, `python:3.12-slim` | — | Isolates the pydantic pin; reproducible |
| Tests | pytest | `>=8` | Offline-first unit tests |

### 2.9 Guardrails (deterministic functions in `quality.py`, `guardrail_max_retries=2`)

1. `min_sources_per_section` — ≥3 distinct domains per section.
2. `all_claims_have_ids` — no orphan assertions.
3. `citation_coverage` — cited / total claims ≥ 0.8.
4. `no_uncited_numbers` — regex scan: any digit in the body must sit in a cited sentence.

### 2.10 Key design decisions

1. **n8n wraps CrewAI, not vice versa.** n8n has no official CrewAI node; the HTTP bridge is the documented integration pattern. Visual ops in n8n, cognition in Python.
2. **Structured handoffs everywhere.** `output_pydantic` on every task; the API returns JSON, never prose.
3. **Verification before judgement.** Deterministic `numeric_check` runs before the LLM entailment verdict, and tests include adversarially plausible wrong numbers.
4. **Bounded everything.** `max_searches` budget, 1 repair pass, no loop back-edges, 10-min HTTP timeout.
5. **Graceful degradation.** If repair still fails, the editor hedges wording and moves claims to `unverified_appendix` instead of failing the run.

### 2.11 Risks and mitigations

| Risk | Mitigation |
|---|---|
| Serper quota drains in testing | TTL cache + `max_searches` enforced in `tools.py` + committed `fixtures/` for tests |
| pydantic pin breaks other work | Engine runs only inside its Docker container |
| Model drift degrades prompts | All prompts in `prompts.py`; all model access via `config.llm()` |
| CrewAI 1.x churn | Pin `crewai==1.15.23`; contracts isolate the framework surface |
| Fact-checker rubber-stamps writer | Deterministic check first + adversarial fixture tests |
| 3–6 min runtime hits timeouts | n8n HTTP timeout 10 min; optional `GET /runs/{id}` polling (10 extra lines) |

### 2.12 Open decisions (defaults given, changeable in 1 line each)

1. **Delivery target** — default: Slack webhook + mounted `/data` volume (zero extra credentials). Alternatives: Google Docs, Notion, Email.
2. **Trigger** — default: Webhook (best demo). Add Schedule/Sheets later by node duplication.
3. **Model map** — default Sonnet/Haiku split above; swap via `MODEL` env.


---

## 3. As built (differences from the frozen design above)

The sections above are the original design. These are the deliberate departures; where they conflict, this section is current.

| Design said | As built | Why |
|---|---|---|
| Crew 1 hierarchical, async researchers | Sequential crew: planner, one researcher per sub-question, synthesizer | Free-tier rate limits (`CREW_MAX_RPM`) make parallel calls fail; sequential is predictable |
| Claude Sonnet / Haiku via `MODEL` | Provider chain Gemini, Groq, Ollama (`LLM_PROVIDER_ORDER`); planner, writer, editor and fact-checker use the strong tier | Zero-cost operation. Quality is bounded by the models available; see README Honest Limits |
| "No user-facing UI" | `frontend/` with live progress, report, claims, references, md/PDF download | Needed for demonstration |
| `max_searches` default 40 | 20 | Protects the 2,500/month Serper quota |
| 3 domains per section guardrail | 2 per section, 3 per report, capped by the domains in the evidence; derived from cited claims; one relaxed retry | The original bar was unsatisfiable when most claims have one source |
| Fallback: fail the run | Evidence-only report with `status: "degraded"`, no internal error text | A user should get findings, clearly labelled as unverified |
| References: cited sources only | Cited sources first, then "Further reading" (every other source found), built from the evidence table; optional Citation Librarian adds titles and notes | Researchers can follow every source |
| Report text as written | Deterministic organizer pass: title, summary line, contents, section order, bullet dedupe, internal wording removed | Consistent, readable output |

Not yet done: live evaluation numbers (use `scripts/evaluate.py`), second-source claim verification, API authentication, concurrent reports, persistent job storage, pinning the `n8nio/n8n` image tag.
