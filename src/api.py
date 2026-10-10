"""The entire n8n <-> Python integration surface: 3 endpoints + health + admin.

CrewAI is imported lazily so the container can boot and /health can answer even
if the engine is broken — and so the API contract is testable without crew code.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Literal

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import progress

app = FastAPI(
    title="Research Assistant API",
    version="1.0.0",
    description="topic + audience + depth -> cited, verified report. Orchestrated by n8n.",
)

DEFAULT_MAX_SEARCHES = 20

_ENGINE: dict[str, Any] | None = None

# Single-flight guard: CrewAI reuses a process-wide executor, so two
# overlapping kickoff() calls fail with "Executor is already running".
# The single GPU can only do one report at a time anyway — fail fast with
# 429 instead of a confusing 502, and let n8n/demo retry.
_ENGINE_LOCK = threading.Lock()
_BUSY_MSG = (
    "engine busy with another report (single-flight). "
    "Wait for it to finish and retry once."
)


_TRANSIENT = ("503", "500", "UNAVAILABLE", "high demand", "Name or service", "name resolution", "429",
              "timed out", "overloaded", "rate limit", "Connection", "ServiceUnavailable",
              "InternalServerError", "RESOURCE_EXHAUSTED", "quota")


_QUOTA = ("RESOURCE_EXHAUSTED", "quota", "429")


def _retry_delay(msg: str) -> float | None:
    """Seconds the provider asked us to wait ("retry in 20.9s" / retryDelay '20s')."""
    import re

    m = re.search(r"retry in ([\d.]+)s", msg) or re.search(r"retryDelay'?:\s*'?([\d.]+)s", msg)
    return float(m.group(1)) if m else None


def _with_retry(fn, **kwargs):
    """Run a crew stage; on provider trouble, hop to the next LLM and re-run it.

    The hop is invisible to the result: the stage restarts from its (unchanged)
    inputs on the new provider. Per-minute limits wait out the delay the
    provider asked for first; hard quota or repeated trouble switches provider.
    """
    from .config import get_settings

    s = get_settings()
    trouble = 0
    minute_waits = 0
    for _ in range(12):
        try:
            return fn(**kwargs)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            if not any(t in msg for t in _TRANSIENT):
                raise
            delay = _retry_delay(msg)
            if delay is not None and minute_waits < 2 and "PerDay" not in msg:
                minute_waits += 1
                wait = min(delay + 3, 90)
                progress.emit("log", message=f"Rate limit reached, pausing {wait:.0f}s")
                time.sleep(wait)
                continue
            trouble += 1
            hard = any(t in msg for t in ("RESOURCE_EXHAUSTED", "quota", "429"))
            if hard or trouble >= 2:
                current = s.detect_provider()
                nxt = s.mark_failed(current)
                print(f"[failover] {current} failed ({msg[:120]}); now using {nxt}", flush=True)
                if nxt == "fixture":
                    raise
                progress.emit("log", message=f"Switched LLM provider: {current} -> {nxt}")
                progress.announce_llm()
                trouble = minute_waits = 0
            else:
                time.sleep(5)
    raise RuntimeError("LLM providers unavailable after repeated retries")


def _engine() -> dict[str, Any]:
    """Load the crews on first use; surface a clear error if they are missing."""
    global _ENGINE
    if _ENGINE is None:
        try:
            from .crews import get_engine

            _ENGINE = get_engine()
        except ImportError as exc:
            raise HTTPException(
                status_code=503,
                detail=f"CrewAI engine unavailable: {exc}. Install requirements.txt in the container.",
            ) from exc
    return _ENGINE


def set_engine(**overrides: Any) -> None:
    """Swap engine functions. Integration tests and offline runs use this."""
    global _ENGINE
    if _ENGINE is None:
        from .crews import get_engine

        _ENGINE = get_engine()
    _ENGINE.update(overrides)


# ---------------------------------------------------------------- schemas ---

class ResearchRequest(BaseModel):
    topic: str = Field(..., min_length=3, description="The subject of the report")
    audience: str = Field("general professional", min_length=2)
    depth: Literal["brief", "standard", "deep"] = "brief"
    max_searches: int = Field(DEFAULT_MAX_SEARCHES, ge=1, le=200)

    model_config = {
        "json_schema_extra": {
            "example": {
                "topic": "State of AI coding assistants, 2026",
                "audience": "engineering managers",
                "depth": "brief",
                "max_searches": 20,
            }
        }
    }


class ClaimIn(BaseModel):
    id: str = Field(..., min_length=1)
    statement: str = Field(..., min_length=1)


class RepairRequest(BaseModel):
    topic: str = Field(..., min_length=3)
    claims: list[ClaimIn] = Field(..., min_length=1)


class Metrics(BaseModel):
    tokens: int = 0
    serper_credits: int = 0
    duration_s: float = 0.0
    repair_needed: bool = False


class ResearchResponse(BaseModel):
    report_md: str
    references: list[dict] = Field(default_factory=list)
    claims: list[dict] = Field(default_factory=list)
    metrics: Metrics
    status: Literal["complete", "degraded"] = "complete"


class RepairResponse(BaseModel):
    repaired_claims: list[dict] = Field(default_factory=list)
    serper_credits: int = 0


# -------------------------------------------------------------- endpoints ---

@app.get("/health")
def health() -> dict:
    """Enhanced health check with provider status."""
    try:
        from .config import get_settings, get_provider_status

        settings = get_settings()
        status = get_provider_status(settings)
        status["ok"] = True
        status["busy"] = _ENGINE_LOCK.locked()
        return status
    except Exception:
        return {"ok": True, "mode": "unknown", "providers": {}, "busy": _ENGINE_LOCK.locked()}


@app.get("/")
def root() -> dict:
    return {
        "service": "research-assistant",
        "endpoints": ["POST /research", "POST /repair", "GET /health", "POST /admin/import-n8n"],
    }


@app.post("/admin/import-n8n")
def import_n8n_workflow() -> dict:
    """Import and activate the n8n workflow via REST API."""
    try:
        import requests
        import json

        n8n_url = "http://research-n8n:5678"
        workflow_path = "/app/n8n/research-assistant.json"

        # Wait for n8n to be ready
        for _ in range(30):
            try:
                if requests.get(f"http://research-n8n:5678/healthz", timeout=2).ok:
                    break
            except Exception:
                pass
            time.sleep(1)
        else:
            raise HTTPException(status_code=503, detail="n8n not ready")

        # Load workflow
        with open("/app/n8n/research-assistant.json") as f:
            workflow = json.load(f)

        # Import workflow
        r = requests.post(f"http://research-n8n:5678/rest/workflows", json=workflow, timeout=10)
        r.raise_for_status()
        workflow_id = r.json()["id"]

        # Activate workflow
        requests.post(f"http://research-n8n:5678/rest/workflows/{workflow_id}/activate", timeout=10)

        return {"ok": True, "workflow_id": workflow_id, "status": "activated"}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"n8n import failed: {exc}") from exc


def _pipeline(req: ResearchRequest) -> ResearchResponse:
    """Crew 1 then Crew 2. Caller must hold _ENGINE_LOCK."""
    engine = _engine()
    started = time.monotonic()
    try:
        from .config import get_settings

        get_settings().reset_failures()
    except Exception:  # noqa: BLE001
        pass

    try:
        evidence = _with_retry(
            engine["run_research"],
            topic=req.topic,
            audience=req.audience,
            depth=req.depth,
            max_searches=req.max_searches,
        )
    except Exception as exc:  # noqa: BLE001 - report the failure, don't crash the run
        raise HTTPException(status_code=502, detail=f"research crew failed: {exc}") from exc

    try:
        editorial = _with_retry(
            engine["run_editorial"],
            evidence=evidence, topic=req.topic, audience=req.audience
        )
    except Exception as exc:  # noqa: BLE001 - every provider failed: still deliver the findings
        print(f"[editorial] all providers failed, serving evidence-only report: {exc}", flush=True)
        from .fallback_report import build

        from .citations import build_references

        fb = build(evidence)
        editorial = {"report_md": fb["markdown"],
                     "references": build_references(evidence, fb["references"]),
                     "claims": [], "metrics": {}, "degraded": True}

    claims = editorial.get("claims", [])
    metrics = Metrics(
        tokens=int(editorial.get("metrics", {}).get("tokens", 0)) + int(evidence.get("tokens", 0)),
        serper_credits=int(evidence.get("serper_credits", 0)),
        duration_s=round(time.monotonic() - started, 2),
        repair_needed=any(c.get("verdict") != "VERIFIED" for c in claims),
    )

    from .report_organizer import organize

    report_md = organize(
        editorial.get("report_md", ""),
        topic=req.topic,
        audience=req.audience,
        references=editorial.get("references", []),
        confidence_grade=editorial.get("confidence_grade", ""),
        claims=claims,
        status="degraded" if editorial.get("degraded") else "complete",
        thin_sections=editorial.get("thin_sections", []),
    )

    return ResearchResponse(
        report_md=report_md,
        references=editorial.get("references", []),
        claims=claims,
        metrics=metrics,
        status="degraded" if editorial.get("degraded") else "complete",
    )


@app.post("/research", response_model=ResearchResponse)
def research(req: ResearchRequest) -> ResearchResponse:
    """Run Crew 1 (research) then Crew 2 (editorial) and return the report."""
    if not _ENGINE_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=429, detail=_BUSY_MSG)
    try:
        return _pipeline(req)
    finally:
        _ENGINE_LOCK.release()


# ----------------------------------------------- frontend: jobs + export ---

def _run_job(job: progress.Job, req: ResearchRequest) -> None:
    """Worker thread. Holds _ENGINE_LOCK (taken by start_job) until done."""
    progress.activate(job)
    job.status = "running"
    try:
        job.add("log", message="Starting research pipeline")
        resp = _pipeline(req)
        job.result = {
            **resp.model_dump(),
            "topic": req.topic,
            "audience": req.audience,
            "depth": req.depth,
        }
        job.status = "done"
    except HTTPException as exc:
        job.error, job.status = str(exc.detail), "error"
    except Exception as exc:  # noqa: BLE001
        job.error, job.status = f"{type(exc).__name__}: {exc}", "error"
    finally:
        job.finished = time.time()
        progress.activate(None)
        _ENGINE_LOCK.release()


@app.post("/jobs")
def start_job(req: ResearchRequest) -> dict:
    """Start a report in the background; poll GET /jobs/{id} for live progress."""
    if not _ENGINE_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=429, detail=_BUSY_MSG)
    job = progress.create(req.model_dump())
    threading.Thread(target=_run_job, args=(job, req), daemon=True).start()
    return {"id": job.id}


@app.get("/jobs/{job_id}")
def job_status(job_id: str, since: int = 0) -> dict:
    job = progress.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")
    return job.snapshot(since)


class ExportRequest(BaseModel):
    topic: str = ""
    audience: str = ""
    depth: str = ""
    report_md: str
    references: list[dict] = Field(default_factory=list)
    metrics: dict = Field(default_factory=dict)


@app.post("/export/pdf")
def export_pdf(req: ExportRequest) -> Response:
    from .pdf_export import build_pdf

    try:
        data = build_pdf(req.model_dump())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {exc}") from exc
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": 'attachment; filename="research-report.pdf"'})


_FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
if _FRONTEND.is_dir():
    app.mount("/ui", StaticFiles(directory=_FRONTEND, html=True), name="ui")

    @app.get("/ui", include_in_schema=False)
    def _ui_redirect() -> RedirectResponse:
        return RedirectResponse("/ui/")


@app.post("/repair", response_model=RepairResponse)
def repair(req: RepairRequest) -> RepairResponse:
    """Bounded re-search for failed claim ids. One pass, no back-edge."""
    if not _ENGINE_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=429, detail=_BUSY_MSG)
    try:
        engine = _engine()
        try:
            result = engine["run_repair"](
                failed_claims=[c.model_dump() for c in req.claims], topic=req.topic
            )
        except Exception as exc:  # noqa: BLE001 - repair failing is not fatal
            raise HTTPException(status_code=502, detail=f"repair failed: {exc}") from exc

        return RepairResponse(
            repaired_claims=result.get("repaired_claims", []),
            serper_credits=int(result.get("serper_credits", 0)),
        )
    finally:
        _ENGINE_LOCK.release()