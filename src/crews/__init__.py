"""Engine entry points (I3). api.py imports from here and nothing else.

The real crews are imported lazily so that importing this package — which the
API does on every request path — never hard-fails when CrewAI is unavailable.
"""

from __future__ import annotations

from typing import Any, Callable


def run_research(topic: str, audience: str, depth: str, max_searches: int) -> dict:
    """Crew 1. Returns an EvidenceTable as a JSON-serializable dict."""
    from .research_crew import run_research as impl

    return impl(topic, audience, depth, max_searches)


def run_editorial(evidence: dict, topic: str, audience: str) -> dict:
    """Crew 2. Returns {report_md, references, claims, verification, metrics}."""
    from .editorial_crew import run_editorial as impl

    return impl(evidence, topic, audience)


def run_repair(failed_claims: list[dict], topic: str) -> dict:
    """Bounded re-search of failed claim ids."""
    from .editorial_crew import run_repair as impl

    return impl(failed_claims, topic)


_ENGINE: dict[str, Callable[..., Any]] = {
    "run_research": run_research,
    "run_editorial": run_editorial,
    "run_repair": run_repair,
}


def get_engine() -> dict[str, Callable[..., Any]]:
    """The live crew functions. Tests swap these out; nothing else should."""
    return dict(_ENGINE)


def set_engine(**overrides: Callable[..., Any]) -> None:
    _ENGINE.update(overrides)


__all__ = [
    "run_research",
    "run_editorial",
    "run_repair",
    "get_engine",
    "set_engine",
]