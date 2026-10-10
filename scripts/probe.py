#!/usr/bin/env python3
"""Probe all providers: Ollama, Gemini, Groq, Serper, and fixtures.

Usage:
    docker compose run --rm crew-api python scripts/probe.py
    python scripts/probe.py --provider ollama
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import config, tools  # noqa: E402


def probe_ollama(settings: config.Settings) -> bool:
    """Test Ollama connectivity and model availability."""
    print("\n=== OLLAMA ===")
    try:
        r = requests.get(f"{settings.ollama_host}/api/tags", timeout=5)
        if not r.ok:
            print(f"FAIL  HTTP {r.status_code}")
            return False
        models = [m.get("name", "") for m in r.json().get("models", [])]
        has_strong = any("llama3.1:8b" in m for m in models)
        has_cheap = any("gemma2:2b" in m for m in models)
        print(f"OK    Models: {', '.join(models) if models else 'none'}")
        print(f"      Strong (llama3.1:8b): {'YES' if has_strong else 'NO'}")
        print(f"      Cheap (gemma2:2b):  {'YES' if has_cheap else 'NO'}")
        if not (has_strong and has_cheap):
            print("WARN  Missing required models — run: ollama pull llama3.1:8b gemma2:2b")
            return False
        return True
    except Exception as exc:
        print(f"FAIL  {type(exc).__name__}: {exc}")
        return False


def probe_gemini(settings: config.Settings) -> bool:
    """Test Gemini API key validity."""
    print("\n=== GEMINI ===")
    if not settings.has_gemini:
        print("SKIP  No GEMINI_API_KEY set")
        return True
    try:
        import litellm
        # Quick test call
        r = litellm.completion(
            model="gemini/gemini-2.5-flash",
            messages=[{"role": "user", "content": "Say 'ok'"}],
            max_tokens=5,
            timeout=15,
        )
        print(f"OK    Response: {r.choices[0].message.content[:50]}")
        return True
    except Exception as exc:
        print(f"FAIL  {type(exc).__name__}: {exc}")
        return False


def probe_groq(settings: config.Settings) -> bool:
    """Test Groq API key validity."""
    print("\n=== GROQ ===")
    if not settings.has_groq:
        print("SKIP  No GROQ_API_KEY set")
        return True
    try:
        import litellm
        r = litellm.completion(
            model="groq/llama-3.1-8b-instant",
            messages=[{"role": "user", "content": "Say 'ok'"}],
            max_tokens=5,
            timeout=15,
        )
        print(f"OK    Response: {r.choices[0].message.content[:50]}")
        return True
    except Exception as exc:
        print(f"FAIL  {type(exc).__name__}: {exc}")
        return False


def probe_serper() -> bool:
    """Test Serper API connectivity."""
    print("\n=== SERPER ===")
    if not tools.fixtures_enabled():
        print(f"Budget: {tools.BUDGET.limit} searches allowed")
    else:
        print("Fixture mode (no SERPER_API_KEY)")

    ok = True
    for name, query in [
        ("search_web", "AI coding assistants 2026"),
        ("search_news", "AI coding agents"),
        ("search_scholar", "retrieval-augmented generation"),
    ]:
        fn = getattr(tools, name)
        before = tools.credits_spent()
        try:
            results = fn(query, num=3)
        except Exception as exc:
            print(f"FAIL  {name}({query!r}): {exc}")
            ok = False
            continue

        spent = tools.credits_spent() - before
        domains = {tools.domain_of(r.get("url", "")) for r in results} - {""}
        print(f"OK    {name}: {len(results)} results, {len(domains)} domains, {spent} credit(s)")
        if results:
            print(f"       First: {results[0].get('title', '')[:60]}")

    # fetch_page
    try:
        page = tools.fetch_page("https://arxiv.org/abs/2603.01234", max_chars=400)
        print(f"OK    fetch_page: {page['title'][:60]} ({len(page['text'])} chars)")
    except Exception as exc:
        print(f"FAIL  fetch_page: {exc}")
        ok = False

    return ok


def probe_config() -> bool:
    """Test config provider detection."""
    print("\n=== CONFIG PROVIDER DETECTION ===")
    from src.config import get_settings, get_provider_status

    settings = get_settings()
    status = config.get_provider_status(settings)

    print(f"Mode: {status['mode']}")
    print(f"Active models: {status['active_models']}")
    for name, info in status["providers"].items():
        if isinstance(info, dict):
            healthy = info.get("healthy", False)
            reason = info.get("reason", "")
            status_str = "OK" if healthy else f"DOWN ({reason})" if reason else "DOWN"
        else:
            status_str = "OK" if info else "DOWN"
        print(f"  {name:8s}: {status_str}")

    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe all providers")
    parser.add_argument(
        "--provider",
        choices=["ollama", "gemini", "groq", "serper", "config", "all"],
        default="all",
        help="Which provider to test",
    )
    args = parser.parse_args()

    # Import requests here to avoid circular import issues
    global requests
    import requests

    from src.config import get_settings
    settings = get_settings()

    print("=" * 60)
    print("PROVIDER PROBE")
    print("=" * 60)

    all_ok = True
    providers_to_test = (
        ["ollama", "gemini", "groq", "serper", "config"]
        if args.provider == "all"
        else [args.provider]
    )

    for p in providers_to_test:
        if p == "ollama":
            all_ok &= probe_ollama(get_settings())
        elif p == "gemini":
            all_ok &= probe_gemini(get_settings())
        elif p == "groq":
            all_ok &= probe_groq(get_settings())
        elif p == "serper":
            all_ok &= probe_serper()
        elif p == "config":
            all_ok &= probe_config()

    print("\n" + "=" * 60)
    if all_ok:
        print("ALL PROBES PASSED")
        return 0
    else:
        print("SOME PROBES FAILED")
        return 1


if __name__ == "__main__":
    sys.exit(main())