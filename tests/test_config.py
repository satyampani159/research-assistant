import pytest

pytest.importorskip("crewai")
from crewai import Process

from src.config import Settings, model_for_role
from src.crews.research_crew import build_research_crew


def test_ollama_cheap_role_uses_tool_capable_model(monkeypatch):
    monkeypatch.setattr(
        Settings,
        "_ollama_healthy",
        lambda self: (True, ["llama3.1:8b"]),
    )

    s = Settings({"OLLAMA_HOST": "http://ollama:11434"})
    assert model_for_role("researcher", s) == "llama3.1:8b"
    assert model_for_role("fact_checker", s) == "llama3.1:8b"


def test_ollama_detection_falls_back_to_localhost(monkeypatch):
    calls = []

    def fake_get(url, timeout=3):
        calls.append(url)
        if url == "http://ollama:11434/api/tags":
            raise OSError("not found")
        if url == "http://localhost:11434/api/tags":
            class Resp:
                ok = True

                def json(self):
                    return {"models": [{"name": "llama3.1:8b"}, {"name": "gemma2:2b"}]}

            return Resp()
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr("src.config.requests.get", fake_get)
    s = Settings({"OLLAMA_HOST": "http://ollama:11434"})
    assert s._ollama_healthy() == (True, ["llama3.1:8b", "gemma2:2b"])
    assert "http://localhost:11434/api/tags" in calls


def test_default_ollama_host_uses_localhost():
    s = Settings({})
    assert s.ollama_host == "http://localhost:11434"


def test_llm_uses_healthy_localhost_when_docker_host_is_unreachable(monkeypatch):
    def fake_get(url, timeout=3):
        if url == "http://ollama:11434/api/tags":
            raise OSError("dns failed")
        if url == "http://localhost:11434/api/tags":
            class Resp:
                ok = True

                def json(self):
                    return {"models": [{"name": "llama3.1:8b"}]}

            return Resp()
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr("src.config.requests.get", fake_get)

    llm_obj = __import__("src.config", fromlist=["llm"]).llm("researcher", Settings({"OLLAMA_HOST": "http://ollama:11434"}))
    assert llm_obj.base_url == "http://localhost:11434/v1"


def test_research_crew_avoids_concurrent_async_task_execution():
    crew = build_research_crew("AI coding assistants", "engineering managers", "brief")
    assert crew.process == Process.sequential
    assert len(crew.tasks) == 5
    assert crew.tasks[0].output_pydantic.__name__ == "ResearchPlan"
    assert all(crew.tasks[index].context == [crew.tasks[0]] for index in range(1, 4))
