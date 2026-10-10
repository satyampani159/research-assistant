"""Live progress feed for the web frontend.

One job runs at a time (the engine is single-flight), so the active job is a
module-level slot. Every emit() is a no-op when no job is active, which keeps
the crews, tools and tests unchanged when they run without the frontend.

Event kinds:
  plan   {crew, agents[]}              a crew is about to start; lists its agents
  agent  {crew, index, name, state}    state = working | done
  tool   {tool, detail}                web search / news / scholar / page fetch
  llm    {provider, model}             which LLM backend is serving the agents
  log    {message}                     free-form status line
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any

_LOCK = threading.Lock()
_JOBS: dict[str, "Job"] = {}
_ACTIVE: "Job | None" = None
_MAX_JOBS = 20


class Job:
    def __init__(self, params: dict) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.params = params
        self.status = "queued"  # queued | running | done | error
        self.events: list[dict] = []
        self.result: dict | None = None
        self.error: str = ""
        self.created = time.time()
        self.finished: float | None = None
        # crew-progress bookkeeping for task_callback
        self._crew_name = ""
        self._agents: list[str] = []
        self._cursor = 0

    def add(self, kind: str, **data: Any) -> None:
        with _LOCK:
            self.events.append({"i": len(self.events), "t": round(time.time() - self.created, 2),
                                "kind": kind, **data})

    def snapshot(self, since: int = 0) -> dict:
        with _LOCK:
            return {
                "id": self.id,
                "status": self.status,
                "events": self.events[since:],
                "next": len(self.events),
                "elapsed": round((self.finished or time.time()) - self.created, 1),
                "error": self.error,
                "result": self.result if self.status == "done" else None,
            }


def create(params: dict) -> Job:
    job = Job(params)
    with _LOCK:
        _JOBS[job.id] = job
        for old in sorted(_JOBS.values(), key=lambda j: j.created)[:-_MAX_JOBS]:
            _JOBS.pop(old.id, None)
    return job


def get(job_id: str) -> Job | None:
    return _JOBS.get(job_id)


def activate(job: Job | None) -> None:
    global _ACTIVE
    _ACTIVE = job


def emit(kind: str, **data: Any) -> None:
    job = _ACTIVE
    if job is not None:
        job.add(kind, **data)


# ------------------------------------------------------------- crew hooks ----

def _label(agent: Any) -> str:
    return str(getattr(agent, "role", "") or "Agent").strip()


def announce_llm() -> None:
    """Report which provider/model is serving the agents right now."""
    if _ACTIVE is None:
        return
    try:
        from .config import get_settings

        s = get_settings()
        provider = s.detect_provider()
        emit("llm", provider=provider, model=s.get_active_models().get("strong", ""))
    except Exception:  # noqa: BLE001 - progress must never break a run
        pass


def begin_crew(name: str, crew: Any) -> None:
    """Announce a crew and mark its first agent as working."""
    job = _ACTIVE
    if job is None:
        return
    job._crew_name = name
    job._agents = [_label(t.agent) for t in crew.tasks]
    job._cursor = 0
    announce_llm()
    emit("plan", crew=name, agents=job._agents)
    _working(job)


def _working(job: Job) -> None:
    if job._cursor < len(job._agents):
        job.add("agent", crew=job._crew_name, index=job._cursor,
                name=job._agents[job._cursor], state="working")


def task_done(_output: Any = None) -> None:
    """Crew task_callback: current agent finished, next one starts."""
    job = _ACTIVE
    if job is None or job._cursor >= len(job._agents):
        return
    job.add("agent", crew=job._crew_name, index=job._cursor,
            name=job._agents[job._cursor], state="done")
    job._cursor += 1
    _working(job)


def end_crew() -> None:
    """Close any agents left open (e.g. a crew that failed part-way)."""
    job = _ACTIVE
    if job is None:
        return
    while job._cursor < len(job._agents):
        job.add("agent", crew=job._crew_name, index=job._cursor,
                name=job._agents[job._cursor], state="skipped")
        job._cursor += 1
