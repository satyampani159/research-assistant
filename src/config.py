"""Settings + the single LLM factory with auto provider detection.

Priority chain: Ollama (GPU) → Gemini (free tier) → Groq (free tier) → Fixtures
"""

from __future__ import annotations

import os
from functools import lru_cache

import requests
from dotenv import load_dotenv

load_dotenv()

# Roles that need judgement quality vs. roles that need volume/cheap.
# The fact-checker is a judge, not a bulk worker: a weak model there rubber-stamps claims.
STRONG_ROLES = {"planner", "manager", "writer", "editor", "fact_checker"}
CHEAP_ROLES = {"researcher", "synthesizer"}

# Model mapping per provider (LiteLLM format)
# Use a tool-capable Ollama model for both tiers; gemma2:2b rejects tool calls.
MODEL_MAP = {
    "ollama": {"strong": "llama3.1:8b", "cheap": "llama3.1:8b"},
    "gemini": {"strong": "gemini/gemini-3.5-flash-lite", "cheap": "gemini/gemini-3.5-flash-lite"},
    "groq": {"strong": "groq/qwen/qwen3.8-27b", "cheap": "groq/qwen/qwen3.8-27b"},
    "fixture": {"strong": "fixture", "cheap": "fixture"},
}


class Settings:
    def __init__(self, env: dict | None = None) -> None:
        e = os.environ if env is None else env
        self.serper_api_key: str = e.get("SERPER_API_KEY", "")
        self.gemini_api_key: str = e.get("GEMINI_API_KEY", "")
        self.groq_api_key: str = e.get("GROQ_API_KEY", "")
        self.ollama_host: str = e.get("OLLAMA_HOST", "http://localhost:11434")
        self.model_strong: str = e.get("MODEL_STRONG", "llama3.1:8b")
        self.model_cheap: str = e.get("MODEL_CHEAP", "llama3.1:8b")
        self.max_searches: int = int(e.get("MAX_SEARCHES", "20"))
        self.cache_ttl_seconds: int = int(e.get("CACHE_TTL_SECONDS", "86400"))
        self.output_dir: str = e.get("OUTPUT_DIR", "outputs")
        self.crew_api_url: str = e.get("CREW_API_URL", "http://crew-api:8000")
        # Cloud free tiers are far faster than local CPU Ollama, so they go first.
        self.provider_order: list[str] = [
            p.strip().lower()
            for p in e.get("LLM_PROVIDER_ORDER", "gemini,groq,ollama").split(",")
            if p.strip()
        ]
        self._detected: str | None = None
        self._failed: set[str] = set()
        self._internet: bool | None = None

    @property
    def has_serper(self) -> bool:
        return bool(self.serper_api_key)

    @property
    def has_gemini(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def has_groq(self) -> bool:
        return bool(self.groq_api_key)

    def _ollama_urls(self) -> list[str]:
        """Try the configured Docker host first and then localhost for native runs."""
        candidates: list[str] = []
        for raw in [self.ollama_host, "http://localhost:11434", "http://127.0.0.1:11434"]:
            cleaned = raw.strip().rstrip("/")
            if cleaned and cleaned not in candidates:
                candidates.append(cleaned)
        return candidates

    def _ollama_healthy(self) -> tuple[bool, list[str]]:
        """Check if Ollama is healthy and has required models."""
        for base in self._ollama_urls():
            try:
                r = requests.get(f"{base}/api/tags", timeout=3)
                if not r.ok:
                    continue
                models = [m.get("name", "") for m in r.json().get("models", [])]
                has_strong = any("llama3.1:8b" in m for m in models)
                has_cheap = any("llama3.1:8b" in m or "gemma2:2b" in m or "mistral:7b" in m or "qwen2.5:7b" in m for m in models)
                if has_strong and has_cheap:
                    return True, models
                return False, models
            except Exception:
                continue
        return False, []

    def resolve_ollama_base_url(self) -> str:
        """Return the first working Ollama base URL for the current runtime."""
        for base in self._ollama_urls():
            try:
                r = requests.get(f"{base}/api/tags", timeout=3)
                if r.ok:
                    return base
            except Exception:
                continue
        return self._ollama_urls()[0]

    def _internet_up(self) -> bool:
        if self._internet is None:
            try:
                self._internet = requests.get("https://8.8.8.8", timeout=2).ok
            except Exception:
                self._internet = False
        return self._internet

    def _gemini_available(self) -> bool:
        return self.has_gemini and self._internet_up()

    def _groq_available(self) -> bool:
        return self.has_groq and self._internet_up()

    def detect_provider(self) -> str:
        """Returns 'ollama' | 'gemini' | 'groq' | 'fixture' based on health checks."""
        if self._detected is not None:
            return self._detected
        checks = {
            "gemini": self._gemini_available,
            "groq": self._groq_available,
            "ollama": lambda: self._ollama_healthy()[0],
        }
        self._detected = "fixture"
        for name in self.provider_order:
            if name in self._failed:
                continue
            if name in checks and checks[name]():
                self._detected = name
                break
        return self._detected

    def _models(self, provider: str) -> dict[str, str]:
        """Model map for a provider; GEMINI_MODEL_STRONG/CHEAP in .env override the default."""
        m = dict(MODEL_MAP.get(provider, MODEL_MAP["fixture"]))
        if provider == "gemini":
            m["strong"] = os.environ.get("GEMINI_MODEL_STRONG") or m["strong"]
            m["cheap"] = os.environ.get("GEMINI_MODEL_CHEAP") or m["cheap"]
        return m

    def mark_failed(self, provider: str) -> str:
        """Stop using a provider (quota/overload) and re-detect the next one."""
        self._failed.add(provider)
        self._detected = None
        return self.detect_provider()

    def reset_failures(self) -> None:
        """Forget earlier provider failures so each report starts on the preferred provider."""
        self._failed.clear()
        self._detected = None

    def get_active_models(self) -> dict[str, str]:
        """Returns the active model names for strong/cheap roles."""
        provider = self.detect_provider()
        return self._models(provider)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def model_for_role(role: str, settings: Settings | None = None) -> str:
    """Return the model name for a given role based on active provider."""
    s = settings or get_settings()
    provider = s.detect_provider()
    tier = "strong" if role.lower() in STRONG_ROLES else "cheap"
    return s._models(provider)[tier]


def llm(role: str, settings: Settings | None = None):
    """Return a `crewai.LLM` for an agent role.

    Auto-detects best available provider:
    1. Ollama (GPU) if healthy with models
    2. Gemini if API key + internet
    3. Groq if API key + internet
    4. Fixture mode (no LLM)
    """
    from crewai import LLM

    s = settings or get_settings()
    model = model_for_role(role, s)

    # CrewAI expects raw model names for Ollama while the project config keeps the
    # provider-specific naming convention for other backends.
    kwargs = {}
    if s.detect_provider() == "ollama":
        raw_model = model.split("/", 1)[1] if "/" in model else model
        ollama_base = s.resolve_ollama_base_url()
        kwargs = {
            "provider": "ollama",
            "api_key": "ollama",
            "base_url": ollama_base.rstrip("/") + "/v1",
            "timeout": 300,
            "max_retries": 3,
        }
        model = raw_model

    kwargs.setdefault("max_retries", 6)  # ride out transient 503 "high demand" errors
    return LLM(model=model, temperature=_temperature(role), **kwargs)


def _temperature(role: str) -> float:
    if role.lower() in {"researcher", "fact_checker"}:
        return 0.0
    if role.lower() in {"synthesizer", "editor"}:
        return 0.2
    return 0.4


def get_provider_status(settings: Settings | None = None) -> dict:
    """Get detailed provider health status for /health endpoint."""
    s = settings or get_settings()
    provider = s.detect_provider()
    ollama_healthy, ollama_models = s._ollama_healthy()

    return {
        "mode": provider,
        "active_models": s.get_active_models(),
        "providers": {
            "ollama": {
                "healthy": ollama_healthy,
                "models": ollama_models,
                "gpu": ollama_healthy,  # assume GPU if Ollama healthy (we deployed with GPU)
            },
            "gemini": {
                "healthy": s._gemini_available(),
                "reason": "no_key" if not s.has_gemini else ("no_internet" if not s._internet_up() else "ok"),
            },
            "groq": {
                "healthy": s._groq_available(),
                "reason": "no_key" if not s.has_groq else ("no_internet" if not s._internet_up() else "ok"),
            },
            "serper": {
                "healthy": s.has_serper,
                "credits_used": 0,  # populated at runtime
            },
            "internet": s._internet_up(),
        },
    }