"""D3.2 / I3 — the HTTP contract n8n depends on.

The engine is swapped for a fake, so these tests are exactly as offline as the
rest of the suite (§6: pytest green with no keys).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src import api

EVIDENCE = {
    "claims": [
        {
            "claim_id": "C1",
            "statement": "Adoption of coding agents reached 38%.",
            "supports": [{"url": "https://reuters.com/x", "publisher": "Reuters"}],
            "conflicts": [],
        },
        {
            "claim_id": "C2",
            "statement": "The strongest system completed 41% of tasks.",
            "supports": [{"url": "https://arxiv.org/abs/2603.01234", "publisher": "arXiv"}],
            "conflicts": [],
        },
    ],
    "gaps": [],
    "contradictions": [],
    "serper_credits": 17,
    "topic": "ai coding agents",
    "depth": "standard",
}


def fake_run_research(topic, audience, depth, max_searches):
    return dict(EVIDENCE)


def fake_run_editorial(evidence, topic, audience):
    verdicts = {"C1": "VERIFIED", "C2": "UNSUPPORTED"}
    return {
        "report_md": "# Report\n\nAdoption reached 38% [1].\n\n## References\n[1] Reuters",
        "references": [{"url": "https://reuters.com/x", "title": "t", "publisher": "Reuters", "date": None}],
        "claims": [
            {"id": cid, "statement": c["statement"], "verdict": verdicts[cid], "confidence": 0.9}
            for c in evidence["claims"]
            for cid in [c["claim_id"]]
        ],
        "confidence_grade": "B",
        "unverified_appendix": [],
        "metrics": {"tokens": 4211, "serper_credits": evidence.get("serper_credits", 0)},
    }


def fake_run_repair(failed_claims, topic):
    return {
        "repaired_claims": [
            {"id": c["id"], "statement": c["statement"], "repaired": True, "url": "https://new.example/x"}
            for c in failed_claims
        ],
        "serper_credits": 4,
    }


@pytest.fixture
def client():
    api.set_engine(
        run_research=fake_run_research,
        run_editorial=fake_run_editorial,
        run_repair=fake_run_repair,
    )
    with TestClient(api.app) as c:
        yield c


# ------------------------------------------------------------------ health ---

def test_health(client):
    body = client.get("/health").json()
    assert body["ok"] is True
    # Enhanced health also reports provider chain + engine lock state.
    assert body["mode"] in {"ollama", "gemini", "groq", "fixture", "unknown"}
    assert "providers" in body
    assert body["busy"] in {True, False}


# ---------------------------------------------------------------- research ---

def test_research_returns_the_full_contract(client):
    r = client.post("/research", json={"topic": "AI coding agents"})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"report_md", "references", "claims", "metrics", "status"}
    assert "[1]" in body["report_md"]
    assert body["references"][0]["url"] == "https://reuters.com/x"
    assert {c["id"] for c in body["claims"]} == {"C1", "C2"}


def test_research_metrics_shape(client):
    metrics = client.post("/research", json={"topic": "AI coding agents"}).json()["metrics"]
    assert set(metrics) == {"tokens", "serper_credits", "duration_s", "repair_needed"}
    assert metrics["serper_credits"] == 17
    assert metrics["tokens"] == 4211
    assert metrics["duration_s"] >= 0


def test_repair_needed_flag_drives_the_n8n_if_node(client):
    """§2.6: metrics.repair_needed is the single flag the IF node branches on."""
    body = client.post("/research", json={"topic": "AI coding agents"}).json()
    unverified = [c for c in body["claims"] if c["verdict"] != "VERIFIED"]
    assert body["metrics"]["repair_needed"] is bool(unverified)
    assert body["metrics"]["repair_needed"] is True  # C2 is UNSUPPORTED


def test_all_verified_means_no_repair_needed(client):
    api.set_engine(run_editorial=lambda **kw: {
        "report_md": "# ok", "references": [], "claims": [
            {"id": "C1", "statement": "s", "verdict": "VERIFIED", "confidence": 0.9}
        ],
        "metrics": {"tokens": 1, "serper_credits": 1},
    })
    body = client.post("/research", json={"topic": "AI coding agents"}).json()
    assert body["metrics"]["repair_needed"] is False


def test_research_defaults_match_the_spec(client):
    """Defaults follow ResearchRequest: audience='general professional',
    depth='brief', max_searches=DEFAULT_MAX_SEARCHES (20)."""
    seen = {}

    def capture(topic, audience, depth, max_searches):
        seen.update(topic=topic, audience=audience, depth=depth, max_searches=max_searches)
        return dict(EVIDENCE)

    api.set_engine(run_research=capture)
    client.post("/research", json={"topic": "AI coding agents"})
    assert seen == {
        "topic": "AI coding agents",
        "audience": "general professional",
        "depth": "brief",
        "max_searches": api.DEFAULT_MAX_SEARCHES,
    }


def test_max_searches_is_passed_through(client):
    seen = {}

    def capture(topic, audience, depth, max_searches):
        seen["max_searches"] = max_searches
        return dict(EVIDENCE)

    api.set_engine(run_research=capture)
    client.post("/research", json={"topic": "AI coding agents", "max_searches": 12})
    assert seen["max_searches"] == 12


# --------------------------------------------------------------- validation ---

@pytest.mark.parametrize("payload", [
    {},                                              # topic required
    {"topic": "ab"},                                 # too short
    {"topic": "AI coding agents", "depth": "enormous"},  # depth is an enum
    {"topic": "AI coding agents", "max_searches": 0},
    {"topic": "AI coding agents", "max_searches": 5000},
    {"topic": "AI coding agents", "audience": ""},
])
def test_bad_requests_are_rejected_with_422(client, payload):
    assert client.post("/research", json=payload).status_code == 422


# ------------------------------------------------------------------ repair ---

def test_repair_endpoint(client):
    r = client.post("/repair", json={
        "topic": "AI coding agents",
        "claims": [{"id": "C2", "statement": "The strongest system completed 41% of tasks."}],
    })
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"repaired_claims", "serper_credits"}
    assert body["repaired_claims"][0]["id"] == "C2"
    assert body["serper_credits"] == 4


def test_repair_requires_at_least_one_claim(client):
    assert client.post("/repair", json={"topic": "x", "claims": []}).status_code == 422


# ------------------------------------------------------- failure behaviour ---

def test_engine_failure_becomes_502_not_a_crash(client):
    def boom(**kwargs):
        raise RuntimeError("crew exploded")

    api.set_engine(run_research=boom)
    r = client.post("/research", json={"topic": "AI coding agents"})
    assert r.status_code == 502
    assert "crew exploded" in r.json()["detail"]


def test_concurrent_research_returns_429_not_executor_crash(client):
    """Single-flight: CrewAI's executor can't run two kickoffs at once."""
    assert api._ENGINE_LOCK.acquire(blocking=False)
    try:
        r = client.post("/research", json={"topic": "AI coding agents"})
        assert r.status_code == 429
        assert "busy" in r.json()["detail"]
        r = client.post("/repair", json={
            "topic": "AI coding agents",
            "claims": [{"id": "C2", "statement": "s"}],
        })
        assert r.status_code == 429
    finally:
        api._ENGINE_LOCK.release()


def test_openapi_documents_the_contract(client):
    spec = client.get("/openapi.json").json()
    assert {"/research", "/repair", "/health"} <= set(spec["paths"])