"""I2 tool contract: shapes, caching, budget enforcement, page hygiene."""

from __future__ import annotations

import pytest

from src import tools


# ------------------------------------------------------- frozen signatures ---

def test_search_web_shape():
    results = tools.search_web("state of AI coding assistants 2026", num=8)
    assert results
    for item in results:
        assert set(item) == {"title", "url", "snippet"}
        assert all(isinstance(v, str) and v for v in item.values())


def test_search_news_shape():
    results = tools.search_news("coding agents", num=5)
    assert results
    for item in results:
        assert set(item) == {"title", "url", "snippet", "source", "date"}


def test_search_scholar_shape_and_normalisation():
    """Serper returns authors as objects; I2 freezes the field as a string."""
    results = tools.search_scholar("retrieval-augmented generation", num=5)
    assert results
    for item in results:
        assert set(item) == {"title", "authors", "year", "cited_by", "url"}
        assert isinstance(item["authors"], str)
        assert isinstance(item["cited_by"], int)


def test_num_is_respected():
    assert len(tools.search_web("ai coding assistants", num=3)) <= 3


def test_fetch_page_shape():
    page = tools.fetch_page("https://arxiv.org/abs/2603.01234")
    assert set(page) >= {"title", "text", "retrieved_at"}
    assert page["title"] and page["text"] and page["retrieved_at"]


def test_fetch_page_strips_chrome():
    """nav / cookie / ads / footer junk must never reach the LLM."""
    text = tools.fetch_page("https://arxiv.org/abs/2603.01234")["text"]
    for junk in ["Cookie", "Log in", "Terms of Service", "Related articles", "window.dataLayer"]:
        assert junk.lower() not in text.lower(), junk


def test_fetch_page_respects_max_chars():
    page = tools.fetch_page("https://arxiv.org/abs/2603.01234", max_chars=120)
    assert len(page["text"]) <= 120


def test_fetch_page_is_cached(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(tools, "_html_for", lambda url: calls.append(url) or "<html><p>x</p></html>")
    monkeypatch.setattr(tools, "CACHE_DIR", tmp_path / "c")
    tools.fetch_page("https://example.com/a")
    tools.fetch_page("https://example.com/a")
    assert len(calls) == 1


# ------------------------------------------------------------ source diversity ---

def test_search_returns_source_diversity():
    """§1.6 target: >= 6 unique domains, which drives the >=6 metric."""
    results = tools.search_web("state of AI coding assistants 2026", num=10)
    domains = {tools.domain_of(r["url"]) for r in results}
    assert len(domains) >= 6, domains


def test_domain_and_tier_helpers():
    assert tools.domain_of("https://www.reuters.com/technology/x") == "reuters.com"
    assert tools.domain_of("https://blog.example.com:8443/a") == "blog.example.com"
    assert tools.domain_of("garbage") == ""
    assert tools.tier_of("https://arxiv.org/abs/1") == 1
    assert tools.tier_of("https://data.gov/x") == 1
    assert tools.tier_of("https://docs.anthropic.com/x") == 1
    assert tools.tier_of("https://www.reuters.com/x") == 2
    assert tools.tier_of("https://random-blog.example/x") == 3


# --------------------------------------------------------------- budget ---

def test_budget_blocks_further_calls(monkeypatch):
    """max_searches is a hard guard, not a suggestion (Project.md §1.8)."""
    def fake_post(endpoint, payload):
        return {"organic": [{"title": "t", "link": "https://a.com/1", "snippet": "s"}]}

    monkeypatch.setattr(tools, "_html_for", lambda url: "<html><p>x</p></html>")
    monkeypatch.setattr(tools, "_serper_post", fake_post)
    monkeypatch.setattr(tools, "fixtures_enabled", lambda: False)
    tools.reset_budget(2)
    tools.BUDGET.spent = 0

    tools.search_web("q1")
    tools.search_web("q2")
    assert tools.credits_spent() == 2
    with pytest.raises(tools.BudgetExhausted):
        tools.search_web("q3")
    tools.reset_budget(40)


def test_cache_hits_spend_nothing(monkeypatch):
    calls = []

    def fake_post(endpoint, payload):
        calls.append(payload)
        return {"organic": [{"title": "t", "link": "https://a.com/1", "snippet": "s"}]}

    monkeypatch.setattr(tools, "fixtures_enabled", lambda: False)
    monkeypatch.setattr(tools, "_serper_post", fake_post)
    tools.reset_budget(40)
    tools.BUDGET.spent = 0

    tools.search_web("cache me")
    tools.search_web("cache me")
    assert len(calls) == 1, "second identical call must hit cache"
    assert tools.credits_spent() == 1, "a cache hit is free"


def test_missing_key_raises_a_clear_error(monkeypatch):
    monkeypatch.setattr(tools, "fixtures_enabled", lambda: False)
    monkeypatch.setenv("SERPER_API_KEY", "")
    with pytest.raises(tools.SerperError, match="SERPER_API_KEY"):
        tools._serper_post("search", {"q": "x"})


def test_offline_mode_needs_no_key():
    assert tools.fixtures_enabled() is True  # conftest clears SERPER_API_KEY
    assert tools.search_web("anything at all")


def test_unmatched_fixture_fails_loudly(monkeypatch, tmp_path):
    monkeypatch.setattr(tools, "FIXTURE_DIR", tmp_path / "empty")
    with pytest.raises(tools.SerperError, match="fixture mode"):
        tools.search_web("nothing matches here")