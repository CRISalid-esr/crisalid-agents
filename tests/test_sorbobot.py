"""Smoke test of the Sorbobot package: the detailed tests live in the test_sorbobot_*.py modules."""

from agents.sorbobot.agent import create_agent


def test_create_agent_reads_nothing_at_construction(monkeypatch):
    monkeypatch.delenv("CRISALID_TAXI_URL", raising=False)
    agent = create_agent()
    assert agent.name == "sorbobot" and agent.display_name == "Sorbobot"
