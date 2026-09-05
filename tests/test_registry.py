import pytest

from common.agent import BaseAgent
from common.registry import AgentRegistry, UnknownAgentError


@pytest.fixture
def fresh_registry():
    return AgentRegistry()


def test_discovers_agent_packages(fresh_registry):
    discovered = fresh_registry.discovered_agents()
    assert "dummy_agent" in discovered
    assert "crisalid_graph_agent" in discovered


def test_agents_env_var_restricts_available_agents(fresh_registry, monkeypatch):
    monkeypatch.setenv("AGENTS", "dummy_agent, not_an_agent")
    assert fresh_registry.available_agents() == ["dummy_agent"]
    with pytest.raises(UnknownAgentError):
        fresh_registry.get_agent("crisalid_graph_agent")


def test_get_agent_instantiates_once(fresh_registry, monkeypatch):
    monkeypatch.delenv("AGENTS", raising=False)
    agent = fresh_registry.get_agent("dummy_agent")
    assert isinstance(agent, BaseAgent)
    assert agent.name == "dummy_agent"
    assert fresh_registry.get_agent("dummy_agent") is agent


def test_unknown_agent(fresh_registry, monkeypatch):
    monkeypatch.delenv("AGENTS", raising=False)
    with pytest.raises(UnknownAgentError):
        fresh_registry.get_agent("nope")


def test_describe_isolates_broken_agents(fresh_registry, monkeypatch):
    monkeypatch.delenv("AGENTS", raising=False)

    def broken(name):
        raise RuntimeError("boom")

    monkeypatch.setattr(fresh_registry, "discovered_agents", lambda: ["dummy_agent", "broken_agent"])
    original = fresh_registry.get_agent
    monkeypatch.setattr(
        fresh_registry, "get_agent", lambda name: broken(name) if name == "broken_agent" else original(name)
    )

    described = {d["name"]: d for d in fresh_registry.describe_agents()}
    assert described["dummy_agent"]["display_name"] == "Dummy agent"
    assert described["broken_agent"]["error"] == "RuntimeError: boom"
