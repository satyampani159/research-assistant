# Multi-Agent Research Assistant — Free-First Edition

Give it a **topic**, an **audience** and a **depth**; get back a **PDF report** where
every factual sentence carries an inline `[Sn]` citation, every citation points at a URL
that was actually fetched, every claim carries a machine-readable verdict
(`VERIFIED` / `UNSUPPORTED` / `CONTRADICTED` / `STALE`), and the whole run prints its
own cost and wall time. A crew of CrewAI agents does the thinking, n8n does the
orchestrating, Serper supplies the evidence, and a deterministic Python checker reads the
numbers before any model is allowed to have an opinion about them.

**Completely free to run** — uses local Ollama (GPU) as primary, falls back to free-tier
Gemini/Groq, and finally to offline fixtures. Zero paid APIs required.

```
POST /research  {"topic": "State of AI coding assistants, 2026"}
  → 200 {"report_md", "references", "claims", "metrics": {tokens, serper_credits, duration_s, repair_needed}}
```

---

## Architecture: Free-First Provider Chain

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        PROVIDER PRIORITY CHAIN                              │
├─────────────────────────────────────────────────────────────────────────────┤
│  1. OLLAMA (GPU)          → llama3.1:8b / gemma2:2b   ✅ Zero cost, offline │
│  2. GEMINI (free tier)    → gemini-1.5-pro / flash    ✅ 15 RPM, 1M/day     │
│  3. GROQ (free tier)      → llama-3.1-70b / 8b        ✅ 14k tokens/day     │
│  4. FIXTURE MODE          → fixtures/serper           ✅ Zero network        │
└─────────────────────────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  TIER 1: n8n (Docker)                  │  TIER 2: Python (Docker)           │
│  Webhook → Import WF → Webhook         │  Crew 1: Research (hierarchical)   │
│    → POST /research                    │    Planner → 3×Researcher → Synth   │
│    → IF repair_needed                  │    Tools: search_web/news/scholar,   │
│       → POST /repair → Merge           │    fetch_page                       │
│    → Format → Write PDF → Slack        │  Crew 2: Editorial (sequential)    │
│                                        │    Writer → Fact-checker → Editor    │
│                                        │    Tools: verify_claim, numeric_ck   │
│                                        │    + 4 deterministic guardrails      │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Quick Start (Tonight's Demo)

```bash
cd /home/ashok/research-assistant

# 1. Configure free keys (only Serper required; LLM keys optional for fallback)
cp .env.example .env
# Edit .env and add:
#   SERPER_API_KEY=...        # 2,500 free/mo at serper.dev
#   GEMINI_API_KEY=...        # Optional: free tier at aistudio.google.com
#   GROQ_API_KEY=...          # Optional: free tier at console.groq.com

# 2. Make data dir writable by n8n (uid 1000)
chmod 777 data

# 3. Build & start (pulls Ollama models llama3.1:8b + gemma2:2b on first run)
docker compose up -d --build

# 4. Wait for Ollama models to download (~2-5 min on first run)
docker compose logs -f ollama

# 5. Verify health (shows active provider chain)
curl -s localhost:8010/health | jq .
# Expected: {"ok":true,"mode":"ollama","providers":{"ollama":{"healthy":true,...}}}

# 6. Run the full demo (auto-imports n8n workflow, runs webhook, generates PDF)
python scripts/demo.py --topic "State of AI coding assistants, 2026" --depth brief

# 7. Find your PDF report
ls -la data/report-*.pdf
```

**Ports:** API on `8010` (host), n8n UI on `5678`. Change via `API_PORT`/`N8N_PORT` in `.env`.

---

## Free Provider Setup

### 1. Ollama (Primary — Zero Cost, GPU Accelerated)
- **Models:** `llama3.1:8b` (strong), `gemma2:2b` (cheap)
- **Auto-pulled** on first `docker compose up` (needs ~6GB VRAM)
- **Zero cost, works offline**, GPU accelerated via NVIDIA runtime

### 2. Google Gemini (Fallback 1 — Free Tier)
- Get key: https://aistudio.google.com/apikey
- **Free tier:** 15 RPM, 1M tokens/day
- Add to `.env`: `GEMINI_API_KEY=your_key`

### 3. Groq (Fallback 2 — Free Tier)
- Get key: https://console.groq.com/keys
- **Free tier:** 14,400 tokens/day, fastest inference
- Add to `.env`: `GROQ_API_KEY=your_key`

### 4. Serper (Required for Live Search)
- Get key: https://serper.dev (2,500 free queries/month)
- Add to `.env`: `SERPER_API_KEY=your_key`

**Auto-detection logic:** Ollama (GPU healthy) → Gemini (key + internet) → Groq (key + internet) → Fixtures

---

## One-Command Demo

```bash
# Full pipeline: health check → n8n import → webhook → PDF report
python scripts/demo.py --topic "State of AI coding assistants, 2026" --depth brief

# Custom topic
python scripts/demo.py --topic "Recent advances in retrieval-augmented generation" --depth standard

# Custom output path
python scripts/demo.py --topic "Weekly: rival pricing moves" --output data/my-report.pdf
```

**Output:** `data/report-<topic>-<timestamp>.pdf` with:
- Title page (topic, audience, depth, timestamp)
- Full report with inline citations `[1]`, `[2]`, ...
- Metrics (tokens, Serper credits, duration, repair_needed)
- References section with full URLs

---

## Manual n8n Workflow (Alternative)

```bash
# 1. Start services
docker compose up -d --build

# 2. Auto-import workflow (or import manually in n8n UI)
python scripts/import_n8n.py

# 3. Fire webhook
curl -X POST localhost:5678/webhook/research-assistant \
  -H 'Content-Type: application/json' \
  -d '{"topic":"State of AI coding assistants, 2026","depth":"brief"}'

# 4. Check results
ls -la data/report-*.pdf
```

---

## Offline Development (No Keys Needed)

Everything works offline via fixtures:

```bash
# Run tests (83 tests, zero network)
pip install fastapi httpx pydantic requests tenacity beautifulsoup4 python-dotenv pytest
pytest -q

# Probe providers (uses fixtures if no keys)
python scripts/probe.py

# Test specific provider
python scripts/probe.py --provider ollama
python scripts/probe.py --provider config

# Build sample reports from fixtures
python scripts/make_samples.py
```

With `SERPER_API_KEY` unset, `tools.py` serves `fixtures/` instead of calling Serper.

---

## Provider Health Check

```bash
# Full provider status
curl -s localhost:8010/health | jq .

# Test specific provider
python scripts/probe.py --provider ollama
python scripts/probe.py --provider gemini
python scripts/probe.py --provider groq
python scripts/probe.py --provider serper
```

**Expected healthy output (Ollama primary):**
```json
{
  "ok": true,
  "mode": "ollama",
  "active_models": {"strong": "ollama/llama3.1:8b", "cheap": "ollama/gemma2:2b"},
  "providers": {
    "ollama": {"healthy": true, "models": ["llama3.1:8b", "gemma2:2b"], "gpu": true},
    "gemini": {"healthy": false, "reason": "no_key"},
    "groq": {"healthy": false, "reason": "no_key"},
    "serper": {"healthy": true, "credits_used": 12},
    "internet": true
  }
}
```

---

## Configuration

| Variable | Default | Notes |
|---|---|---|
| `SERPER_API_KEY` | — | **Required** for live search (2,500 free/mo) |
| `GEMINI_API_KEY` | — | Optional fallback (15 RPM, 1M/day free) |
| `GROQ_API_KEY` | — | Optional fallback (14k tokens/day free) |
| `OLLAMA_HOST` | `http://ollama:11434` | Internal Docker network |
| `MODEL_STRONG` | `llama3.1:8b` | Planner, writer, editor, fact-checker |
| `MODEL_CHEAP` | `llama3.1:8b` | Researcher, synthesizer |
| `CITATION_AGENT` | `1` | `0` skips the Citation Librarian model call (references still build from the evidence table) |
| `MAX_SEARCHES` | `20` | Hard cap; raises `BudgetExhausted` |
| `CACHE_TTL_SECONDS` | `86400` | Cache hits cost no credits |
| `API_PORT` | `8010` | Host port for API |
| `N8N_PORT` | `5678` | Host port for n8n UI |
| `SLACK_WEBHOOK_URL` | — | Empty ⇒ Slack skipped |

---

## How a Claim Earns "Verified"

1. **Researcher** fetches real page, copies `verbatim_quote`
2. **Synthesizer** merges notes into evidence table with stable claim IDs
3. **Writer** uses only evidence table; guardrails enforce source diversity (≥2 domains/section and ≥3 per report, capped by what research found), ≥80% citation coverage, no uncited numbers. If the diversity bar fails, the stage re-runs once with integrity checks only (ids, coverage, uncited numbers) rather than discarding the report
4. **Fact-checker** runs `numeric_check` **first** (pure Python — normalizes `$1.2B` = `1,200,000,000` = `1200M`, rejects `%` vs bare count), then entailment
5. **Editor** deletes/hedges non-`VERIFIED` claims; guardrail rejects hallucinated URLs
6. If unverified claims remain → `repair_needed=true` → n8n runs single repair pass

---

## Eval Status

| Metric | Target | Measured |
|---|---|---|
| Deterministic numeric verification | catches ≥8/10 corrupted | **10/10** (incl. `$1.2B`→`$1.2M`, date-year) |
| Verify-claim verdicts | — | **12/12** as expected |
| Source diversity | ≥6 domains | **6** in sample corpus |
| Hallucinated citations | 0 | Enforced by editor guardrail |
| Citation coverage | ≥90% | Not yet measured live |
| Verification rate after repair | ≥85% | Not yet measured live |
| Serper credits/report | ≤60 | Budget enforced at 20 |

83 offline tests passing, `docker compose up` healthy, crew modules import against pinned `crewai==1.15.23` / `pydantic<2.13`.

---

## Honest Limits

- Fixtures are hand-authored (schema-accurate but invented figures) — never cite them
- Live eval metrics (citation coverage, verification rate, cost, wall time) unmeasured: run `python scripts/evaluate.py` against the live stack and paste the summary here
- Verification re-reads the same URL the researcher used: it catches misquotes, not a wrong source. Second-source corroboration is not implemented
- One report at a time (single-flight lock) and job state is in memory: fine for a demo, not a multi-user service
- The API has no authentication; keep it on a private network
- Page fetching refuses private/loopback addresses and re-checks every redirect hop (SSRF guard)
- Repair pass is single n8n branch, no back-edge — cannot retry failed repairs
- `fetch_page` is plain requests + BeautifulSoup — no JS rendering, no paywall handling
- Pydantic pin is real (`crewai==1.15.23` requires `pydantic<2.13`) — run in container only
- Fixture mode silently degrades keyless runs — meant for tests, not production

---

## Troubleshooting

| Issue | Fix |
|---|---|
| Ollama unhealthy | `docker compose logs ollama` — ensure models pulled, GPU available |
| `CUDA out of memory` | Use smaller models: `MODEL_STRONG=ollama/llama3.2:3b` |
| n8n workflow not importing | Check `docker compose logs n8n` — ensure n8n healthy first |
| PDF generation fails | Install `fpdf2`: `pip install fpdf2` |
| Port 8010/5678 busy | Set `API_PORT=8011` / `N8N_PORT=5679` in `.env` |
| Serper 429 rate limit | Reduce `MAX_SEARCHES`, increase `CACHE_TTL_SECONDS` |
| GPU not detected | Ensure NVIDIA Container Toolkit installed: `nvidia-smi` in container |

---

## Project Structure

```
research-assistant/
├── docker-compose.yml          # 4 services: crew-api, n8n, ollama
├── Dockerfile                  # python:3.12-slim + curl
├── .env.example                # Free provider keys
├── requirements.txt            # Pinned deps (crewai 1.15.23)
├── n8n/research-assistant.json # Importable workflow
├── fixtures/                   # Offline Serper + page fixtures
├── samples/                    # Validated EvidenceTable + FinalReport
├── scripts/
│   ├── demo.py                 # One-command demo → PDF
│   ├── import_n8n.py           # Auto-import n8n workflow
│   ├── probe.py                # Provider health checks
│   ├── probe.py                # Provider health checks
│   └── make_samples.py         # Build samples from fixtures
├── src/
│   ├── api.py                  # FastAPI: /research, /repair, /health, /admin/import-n8n
│   ├── config.py               # Provider detection + model routing
│   ├── contracts.py            # Pydantic models (frozen handoffs)
│   ├── tools.py                # Serper + fetch_page + fixtures
│   ├── quality.py              # numeric_check, verify_claim, 4 guardrails
│   ├── prompts.py              # All agent prompts
│   └── crews/                  # Crew 1 (research) + Crew 2 (editorial)
├── tests/                      # 83 offline tests
└── data/                       # Output reports (PDF + MD)
```

---

## License

MIT — Free for any use, including commercial.