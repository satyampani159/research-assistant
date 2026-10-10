"""Offline-first: no API keys, no network. Fixtures serve every HTTP call."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Force fixture mode for every test, whatever the developer's shell has.
os.environ["SERPER_FIXTURES"] = "1"
os.environ.pop("SERPER_API_KEY", None)
os.environ.setdefault("TOOL_CACHE_DIR", str(ROOT / ".cache" / "test"))
os.environ.setdefault("SERPER_FIXTURE_DIR", str(ROOT / "fixtures" / "serper"))
os.environ.setdefault("PAGE_FIXTURE_DIR", str(ROOT / "fixtures" / "pages"))


@pytest.fixture(autouse=True)
def _isolated_budget_and_cache(monkeypatch, tmp_path):
    """Each test gets its own budget and cache dir; nothing leaks between tests."""
    from src import tools

    monkeypatch.setattr(tools, "CACHE_DIR", tmp_path / "cache")
    tools.reset_budget(40)
    tools.BUDGET.spent = 0
    yield
    tools.reset_budget(40)
    tools.BUDGET.spent = 0