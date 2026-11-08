import json

import pytest
from fastapi.testclient import TestClient

from chat_api.allow_list import AgentAllowList, set_allow_list
from chat_api.auth import set_verifier
from chat_api.deps import set_exchanger
from chat_api.main import app
from common.token_exchange import TokenExchanger
from tests.jwt_fixtures import foreign_key, make_token, make_verifier

PAYLOAD = {
    "message": {
        "id": "m1",
        "role": "user",
        "parts": [{"type": "text", "text": "How many words in: the quick brown fox?"}],
    }
}


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("KEYCLOAK_ISSUER", "https://keycloak.test/realms/crisalid-inst")
    monkeypatch.delenv("KEYCLOAK_CLIENT_ID", raising=False)
    monkeypatch.delenv("KEYCLOAK_CLIENT_SECRET", raising=False)
    set_verifier(make_verifier())
    set_allow_list(AgentAllowList("sovisuplus:dummy_agent;open-webui:"))
    set_exchanger(TokenExchanger(issuer="https://keycloak.test/realms/x", client_id="", client_secret=""))
    with TestClient(app) as client:
        yield client
    set_verifier(None)
    set_allow_list(None)
    set_exchanger(None)


def _ndjson(response) -> list[dict]:
    return [json.loads(line) for line in response.text.splitlines() if line]


def test_health_is_public(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_agents_listing_without_token_shows_every_agent(client, registered_dummy_agent, monkeypatch):
    monkeypatch.setenv("AGENTS", "dummy_agent")
    body = client.get("/agents").json()
    assert body == [{
        "name": "dummy_agent",
        "display_name": "Dummy agent",
        "description": registered_dummy_agent.description,
    }]


def test_agents_listing_applies_allow_list_with_token(client, registered_dummy_agent, monkeypatch):
    monkeypatch.setenv("AGENTS", "dummy_agent")
    assert [a["name"] for a in client.get("/agents", headers=bearer(make_token(azp="sovisuplus"))).json()] == ["dummy_agent"]
    assert client.get("/agents", headers=bearer(make_token(azp="open-webui"))).json() == []
    assert client.get("/agents", headers=bearer(make_token(azp="unknown"))).json() == []


def test_agents_listing_rejects_invalid_token(client, registered_dummy_agent):
    response = client.get("/agents", headers=bearer("not-a-jwt"))
    assert response.status_code == 401
    assert response.json() == {"error": "invalid_token"}


def test_chat_requires_token(client, registered_dummy_agent):
    response = client.post("/agents/dummy_agent/chat", json=PAYLOAD)
    assert response.status_code == 401
    assert response.json() == {"error": "invalid_token"}
    assert response.headers["WWW-Authenticate"].startswith("Bearer")


@pytest.mark.parametrize("token, code", [
    (make_token(audience="other-api"), "wrong_audience"),
    (make_token(issuer="https://elsewhere/realms/x"), "invalid_token"),
    (make_token(ttl=30), "token_expiring"),
    (make_token(ttl=-10), "invalid_token"),
    (make_token(key=foreign_key()), "invalid_token"),
    (make_token(kid="unknown"), "invalid_token"),
])
def test_chat_rejects_bad_tokens(client, registered_dummy_agent, token, code):
    response = client.post("/agents/dummy_agent/chat", json=PAYLOAD, headers=bearer(token))
    assert response.status_code == 401
    assert response.json() == {"error": code}


def test_chat_rejects_agent_outside_allow_list(client, registered_dummy_agent):
    response = client.post("/agents/dummy_agent/chat", json=PAYLOAD, headers=bearer(make_token(azp="open-webui")))
    assert response.status_code == 403
    assert response.json() == {"error": "agent_not_allowed"}


def test_chat_streams_ndjson_sequence(client, registered_dummy_agent):
    response = client.post("/agents/dummy_agent/chat", json=PAYLOAD, headers=bearer(make_token()))

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
    response = client.post("/agents/nope/chat", json=PAYLOAD, headers=bearer(make_token()))
    assert response.status_code == 404


def test_chat_without_allow_list_accepts_any_client(client, registered_dummy_agent):
    set_allow_list(AgentAllowList(""))
    response = client.post("/agents/dummy_agent/chat", json=PAYLOAD, headers=bearer(make_token(azp="anything")))
    assert response.status_code == 200
