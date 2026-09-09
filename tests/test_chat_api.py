import json

import pytest
from fastapi.testclient import TestClient

from chat_api.main import app

PAYLOAD = {
    "message": {
        "id": "m1",
        "role": "user",
        "parts": [{"type": "text", "text": "How many words in: the quick brown fox?"}],
    }
}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ENABLE_API_KEYS", "true")
    monkeypatch.setenv("API_KEYS", "key1")
    with TestClient(app) as client:
        yield client


def _ndjson(response) -> list[dict]:
    return [json.loads(line) for line in response.text.splitlines() if line]


def test_health_is_public(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_agents_requires_api_key(client, registered_dummy_agent):
    assert client.get("/agents").status_code == 401


def test_agents_lists_registered_agent(client, registered_dummy_agent, monkeypatch):
    monkeypatch.setenv("AGENTS", "dummy_agent")
    body = client.get("/agents", headers={"x-api-key": "key1"}).json()
    assert body == [{
        "name": "dummy_agent",
        "display_name": "Dummy agent",
        "description": registered_dummy_agent.description,
    }]


def test_chat_streams_ndjson_sequence(client, registered_dummy_agent):
    response = client.post("/agents/dummy_agent/chat", json=PAYLOAD, headers={"x-api-key": "key1"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    chunks = _ndjson(response)
    types = [c["type"] for c in chunks]

    assert types[0] == "start"
    assert types[1] == "tool-input-available"
    assert chunks[1]["toolName"] == "count_words"
    assert types[2] == "tool-output-available"
    assert chunks[2]["output"] == "4"
    assert types[3] == "text-start"
    assert types[-2] == "text-end"
    assert types[-1] == "finish"
    assert "".join(c["delta"] for c in chunks if c["type"] == "text-delta") == "There are 4 words."


def test_chat_unknown_agent(client, registered_dummy_agent):
    response = client.post("/agents/nope/chat", json=PAYLOAD, headers={"x-api-key": "key1"})
    assert response.status_code == 404
