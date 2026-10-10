# implementation.md — 3-developer build plan

> Companion to `Project.md` (read §§1–2.6 first). Total effort ≈ 5–7h per developer, mostly parallel.

---

## 0. Read this first: how your work stays independent

The project integrates through **3 frozen interfaces**. They are specified below, frozen after the 1-hour foundation session (§1), and **nobody changes them without all-3 agreement**:

| # | Interface | Defined in | Owned by | Consumed by |
|---|---|---|---|---|
| I1 | Data contracts (pydantic models) | `src/contracts.py` = `Project.md` §2.5 | Dev 1 | Dev 2 (shapes only), Dev 3 (JSON shapes) |
| I2 | Tool function signatures | §0.1 below → `src/tools.py` | Dev 2 | Dev 1 (via stubs until integration) |
| I3 | Engine functions + HTTP API | §0.2 below → `POST /research`, `/repair`, `/health` | Dev 1 (functions), Dev 3 (endpoints) | Dev 3 (via mocks until integration) |

**Rules:**

1. **Edit only files you own** (ownership table in §§2–4). Need something from someone else's file? Ask — don't edit.
2. **Stub rule.** Until integration day (§5), each developer builds against local stubs that mimic the frozen interfaces, then deletes them. Stubs live in your own files only.
3. **Interface-change protocol.** If a signature must change: propose in the group chat, update *this* doc + `Project.md`, all three re-pull. No silent changes.
4. **Branching.** `main` (protected-ish: only merge green work) + one branch per developer (`dev1-crews`, `dev2-tools`, `dev3-api`). Merge to `main` at each milestone below.

### 0.1 Frozen tool signatures (I2 — Dev 2 implements, Dev 1 stubs)

```python
# src/tools.py — exact signatures, frozen
def search_web(query: str, num: int = 8, gl: str = "us") -> list[dict]:
    """Each dict: {title: str, url: str, snippet: str}"""

def search_news(query: str, num: int = 8) -> list[dict]:
    """Each dict: {title: str, url: str, snippet: str, source: str, date: str}"""

def search_scholar(query: str, num: int = 8) -> list[dict]:
    """Each dict: {title: str, authors: str, year: int, cited_by: int, url: str}"""

def fetch_page(url: str, max_chars: int = 6000) -> dict:
    """{title: str, text: str, retrieved_at: str}"""

def numeric_check(claim: str, source_text: str) -> dict:
    """{match: bool, claim_numbers: list[str], source_numbers: list[str], note: str}"""
```

Dev 1: wrap these in CrewAI `@tool` functions inside `crews/` using **identical names and signatures** against canned data; at integration you only swap the import to `src.tools`.

### 0.2 Frozen engine functions + HTTP API (I3 — Dev 1 implements, Dev 3 mocks)

```python
# Engine entry points (Dev 1 implements in src/crews/*.py; Dev 3 mocks until integration)
def run_research(topic: str, audience: str, depth: str, max_searches: int) -> dict:
    """Returns EvidenceTable as JSON-serializable dict (Project.md §2.5)."""

def run_editorial(evidence: dict, topic: str, audience: str) -> dict:
    """Returns {report_md: str, references: [...], claims: [...],
                verification: {...}, metrics: {...}}."""

def run_repair(failed_claims: list[dict], topic: str) -> dict:
    """failed_claims: [{id, statement}]. Returns {repaired_claims: [...], serper_credits: int}."""
```

HTTP (Dev 3 implements in `src/api.py`; n8n calls exactly this):

```
POST /research  {topic, audience?, depth?, max_searches?}
  → 200 {report_md, references[], claims[], metrics{tokens, serper_credits, duration_s, repair_needed}}
POST /repair    {topic, claims: [{id, statement}]}
  → 200 {repaired_claims[], serper_credits}
GET  /health   → 200 {"ok": true}
```

---

## 1. Foundation — all three together (~1 hour)

| ID | Task | Owner(s) | Done when |
|---|---|---|---|
| F1 | `git init`, create layout from `Project.md` §2.7 (empty files + `requirements.txt` + `.env.example` + `.gitignore` for `.env`, `outputs/`, `__pycache__/`) | Dev 3 | Repo pushes, everyone cloned |
| F2 | Read I1/I2/I3 above + `Project.md` §§2.5–2.6 out loud; freeze them | All | Any objection raised **now**, not mid-build |
| F3 | `docker-compose.yml` skeleton (2 services, `CREW_API_URL` env) + `Dockerfile` skeleton | Dev 3 | `docker compose config` validates |
| F4 | `src/config.py` skeleton: `Settings` (keys, `MODEL`, budgets) + `llm(role)` factory stub | Dev 1 | Importable, returns a fake object for now |
| F5 | Each dev creates their stub file (see §§2–4) | Each | Stubs return canned data matching I1–I3 shapes |

**Exit gate:** `main` has skeleton + frozen interfaces; all three stubs import cleanly.

---

## 2. Developer 1 — Crew & Agents Engineer

**Owns ( nobody else touches ):** `src/contracts.py`, `src/config.py`, `src/prompts.py`, `src/crews/` (`research_crew.py`, `editorial_crew.py`, `tool_stubs.py` — deleted at integration).

**Consumes:** I1 (you define it), I2 signatures (stub them locally — do **not** wait for Dev 2).

**Delivers:** `run_research`, `run_editorial`, `run_repair` + `samples/` (one real `EvidenceTable.json` and one `FinalReport.json` you generate with your stubs — these become Dev 3's mock data and the integration test fixtures).

| ID | Task | Details | Done when |
|---|---|---|---|
| D1.1 | `contracts.py` | Implement the 6 models from `Project.md` §2.5 verbatim | `pytest`-style import + a construct-each-model smoke script passes |
| D1.2 | `config.py` | `Settings` via env (`SERPER_API_KEY`, `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`, `MODEL`, `MAX_SEARCHES`); `llm(role)` returns `crewai.LLM` per role map (planner/writer/editor → strong, researcher/fact-checker → cheap) | `llm("writer").model` prints the expected model string |
| D1.3 | `tool_stubs.py` | Same 5 signatures as §0.1, returning 8–10 canned results shaped like real Serper output | Crew code runs end-to-end with **no network, no keys** |
| D1.4 | `prompts.py` | One prompt per agent: role, goal, backstory, hard rules (≥3 domains per sub-question; draft from evidence table only; delete-or-hedge non-VERIFIED) | All prompts in one file, no prompt text inside `crews/` |
| D1.5 | `research_crew.py` | `Process.hierarchical` + `manager_llm`; 3 researchers `async_execution=True`; Synthesizer with `output_pydantic=EvidenceTable`; `run_research()` | Stubbed run yields ≥10 notes, ≥6 distinct fake domains, 0 duplicate URLs |
| D1.6 | `editorial_crew.py` | `Process.sequential`: Writer → Fact-checker → Editor; `output_pydantic` on each; `run_editorial()`, `run_repair()` (repair = researcher re-answering only failed claim IDs) | Stubbed run yields `report_md` with `[Sn]` cites + `verification` log |
| D1.7 | Samples | Commit `samples/evidence.json` + `samples/final.json` from a stubbed run | Dev 3 can build mocks from these without talking to you |

**Guardrails note:** you *reference* `quality.check_*` functions in your tasks, but Dev 2 implements them — import them behind a `try/except` that falls back to `return (True, "stub")` until integration.

---

## 3. Developer 2 — Tools & Quality Engineer

**Owns ( nobody else touches ):** `src/tools.py`, `src/quality.py`, `fixtures/`, `tests/`, `scripts/probe.py`.

**Consumes:** Serper API docs + I1 field shapes (what a `ResearchNote` needs: url, publisher, date, quote). You import **nothing** from Dev 1 or Dev 3 — fully independent.

**Delivers:** working tools against the live Serper API + deterministic quality checks + offline test suite.

| ID | Task | Details | Done when |
|---|---|---|---|
| D2.1 | `scripts/probe.py` | Hit Serper `/search`, `/news`, `/scholar` once each with the real key; print credits used | Real JSON back; **key problems surface on day 1** |
| D2.2 | `tools.py`: search trio | Implement §0.1 signatures; `tenacity` backoff on 429/5xx; TTL file cache keyed by query hash; module-level credit counter; enforce `max_searches` by raising `BudgetExhausted` | Probe passes; 2nd identical call hits cache (no new credits) |
| D2.3 | `tools.py`: `fetch_page` | requests + BeautifulSoup, strip nav/script/ads, truncate to `max_chars`, return `{title,text,retrieved_at}`; cached | 3 real URLs return clean text, no nav junk |
| D2.4 | `quality.py`: `numeric_check` | Pure Python: regex-extract numbers + units, normalize scales (%, $M/$B, k/M), compare with tolerance → §0.1 shape | Catches ≥8/10 corrupted claims in `tests/test_numeric_check.py` (include adversarially plausible wrong numbers) |
| D2.5 | `quality.py`: `verify_claim` + 4 guardrails | `verify_claim` = fetch + `numeric_check` + entailment hook (LLM call isolated in one function Dev 1 can inject); guardrails: `min_sources_per_section`, `all_claims_have_ids`, `citation_coverage`, `no_uncited_numbers` — each returns `(bool, message)` | `tests/test_quality.py` green **offline** using `fixtures/` |
| D2.6 | `fixtures/` | Commit 5–8 real Serper responses (redact key) + 3 fetched pages | `pytest -q` green with network disabled |

---

## 4. Developer 3 — API & Automation Engineer

**Owns ( nobody else touches ):** `src/api.py`, `Dockerfile`, `docker-compose.yml`, `.env.example`, `n8n/`, `src/mock_engine.py` (deleted at integration), delivery + README run section.

**Consumes:** I3 signatures (mock them — do **not** wait for Dev 1), I1 JSON shapes (copy Dev 1's `samples/` when available; invent your own before that).

**Delivers:** containerized API + working n8n workflow + delivered report.

| ID | Task | Details | Done when |
|---|---|---|---|
| D3.1 | `mock_engine.py` | Implement `run_research/run_editorial/run_repair` per §0.2 returning canned JSON shaped like I1 (use Dev 1's `samples/` once they land) | `api.py` fully testable with zero crew code |
| D3.2 | `src/api.py` | FastAPI: the 3 endpoints from §0.2; `repair_needed = any(v != VERIFIED)`; include `metrics{serper_credits, duration_s}` (tokens = 0 until real engine); request validation via pydantic | `curl POST /research` returns a full-shaped report in <1s |
| D3.3 | Docker | `Dockerfile` (`python:3.12-slim`, `pip install -r requirements.txt`, `uvicorn src.api:app`); `docker-compose.yml` (`crew-api:8000` + `n8n` + shared network + `/data` volume; `N8N_DIAGNOSTICS_ENABLED=false`) | `docker compose up -d` → both green, `GET /health` → ok |
| D3.4 | n8n workflow | Build per `Project.md` §2.2: Webhook → Set → HTTP `/research` (timeout 600s, `CREW_API_URL` env) → IF (`repair_needed AND attempt=1`) → HTTP `/repair` → Merge → Code format → Deliver | Test-panel webhook run completes end-to-end against the mock |
| D3.5 | Delivery | Default: Slack webhook node **and** write `report.md` to mounted `/data`. (Google Docs/Notion only if time remains) | Report file lands in `./data/` + Slack message arrives |
| D3.6 | Export | Save workflow as `n8n/research-assistant.json` (importable), commit | Fresh `docker compose up` + import JSON reproduces the demo |

---

## 5. Integration — all three (~2 hours, scheduled, not async)

| ID | Task | Owners | Done when |
|---|---|---|---|
| I-1 | Swap stubs: Dev 1 deletes `tool_stubs.py`, imports `src.tools`; Dev 3 deletes `mock_engine.py`, imports real `run_*`; Dev 1 removes guardrail fallback, imports `src.quality` | All, together | `python -c "import src.api, src.crews.research_crew"` clean |
| I-2 | Contract conformance: run Dev 1's crews live (1 `brief` topic), validate outputs against `contracts.py` models strictly | Dev 1 + Dev 2 | Zero validation errors |
| I-3 | End-to-end: fire the n8n webhook, watch research → IF → (repair if needed) → delivery | Dev 3 drives, all watch | Report delivered; `metrics` shows real tokens + credits |
| I-4 | Eval: 3 fixed topics (tech / academic / competitive-intel), record the `Project.md` §1.6 table with real numbers + 2 documented failure cases | All | Table committed to README |
| I-5 | Cleanup: delete stubs/mocks, `pytest -q` green, freeze `requirements.txt`, tag `v1.0` | Dev 3 | `main` is the demoable product |

**If integration fights you**, the most likely culprits in order: (1) someone drifted from a frozen signature — `grep` the signature, fix the drifter, don't "adapt" both sides; (2) Serper shape changed — fix in `tools.py` only; (3) n8n URL wrong — it's `http://crew-api:8000` inside compose, `localhost:8000` outside.

## 6. Definition of done (whole project)

- [ ] Webhook → report delivered with zero manual steps
- [ ] All `Project.md` §1.6 targets met or missed-with-written-reason
- [ ] `pytest -q` green offline; live probe green with keys
- [ ] No stubs/mocks remain; `main` builds from clean clone + `docker compose up`
- [ ] README: 1-paragraph pitch, arch diagram, run instructions, eval table, honest limits

## 7. Working protocol

- Daily 10-min sync: what merged, what blocked, any interface-change proposal.
- Merge to `main` only at §1-exit, §§2–4-exit (per dev), and §5-exit.
- Secrets never committed (`.env` git-ignored; only `.env.example` with empty values).
- Serper budget: testing against live API only via `scripts/probe.py` and I-2/I-3 — everything else uses cache/`fixtures/`.
