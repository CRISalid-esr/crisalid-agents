import json

import pytest
from fastapi.testclient import TestClient

from chat_api.allow_list import AgentAllowList, set_allow_list
from chat_api.auth import set_verifier
from chat_api.deps import set_exchanger
from chat_api.main import app
from common.token_exchange import TokenExchanger
from tests.jwt_fixtures import make_token, make_verifier

MESSAGES = [{"role": "user", "content": "How many words in: the quick brown fox?"}]


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("KEYCLOAK_ISSUER", "https://keycloak.test/realms/crisalid-inst")
    # A TokenExchanger built with empty credentials falls back to the environment: keep the local .env out.
    monkeypatch.delenv("KEYCLOAK_CLIENT_ID", raising=False)
    monkeypatch.delenv("KEYCLOAK_CLIENT_SECRET", raising=False)
    set_verifier(make_verifier())
    set_allow_list(AgentAllowList("open-webui:dummy_agent;sovisuplus:"))
    set_exchanger(TokenExchanger(issuer="https://keycloak.test/realms/x", client_id="", client_secret=""))
    with TestClient(app) as client:
        yield client
    set_verifier(None)
    set_allow_list(None)
    set_exchanger(None)


def _events(response) -> list:
    events = []
    for line in response.text.split("\n\n"):
        line = line.strip()
        if not line:
            continue
        assert line.startswith("data: ")
        data = line[len("data: "):]
        events.append(data if data == "[DONE]" else json.loads(data))
    return events


def test_models_listing(client, registered_dummy_agent, monkeypatch):
    monkeypatch.setenv("AGENTS", "dummy_agent")
    body = client.get("/v1/models").json()
    assert body["object"] == "list"
    assert [m["id"] for m in body["data"]] == ["dummy_agent"]
    assert body["data"][0]["name"] == "Dummy agent"

    assert [m["id"] for m in client.get("/v1/models", headers=bearer(make_token(azp="open-webui"))).json()["data"]] == ["dummy_agent"]
    assert client.get("/v1/models", headers=bearer(make_token(azp="sovisuplus"))).json()["data"] == []


def test_streaming_completion(client, registered_dummy_agent):
    response = client.post("/v1/chat/completions",
                           json={"model": "dummy_agent", "messages": MESSAGES, "stream": True},
                           headers=bearer(make_token(azp="open-webui")))

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _events(response)
    assert events[-1] == "[DONE]"
    chunks = [e for e in events if isinstance(e, dict) and "choices" in e]
    statuses = [e["event"]["data"]["done"] for e in events if isinstance(e, dict) and "event" in e]

    assert chunks[0]["object"] == "chat.completion.chunk"
    assert chunks[0]["model"] == "dummy_agent"
    assert chunks[0]["choices"][0]["delta"] == {"role": "assistant", "content": ""}
    contents = [c["choices"][0]["delta"].get("content", "") for c in chunks]
    joined = "".join(contents)
    assert '<details type="tool_calls" done="true"' in joined
    assert 'name="count_words"' in joined
    assert joined.endswith("There are 4 words.")
    assert statuses == [False, False, True]
    assert chunks[-1]["choices"][0] == {"index": 0, "delta": {}, "finish_reason": "stop"}


def test_non_streaming_completion(client, registered_dummy_agent):
    response = client.post("/v1/chat/completions",
                           json={"model": "dummy_agent", "messages": MESSAGES},
                           headers=bearer(make_token(azp="open-webui")))
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "chat.completion"
    content = body["choices"][0]["message"]["content"]
    assert 'name="count_words"' in content
    assert content.endswith("There are 4 words.")


def test_completion_requires_token_and_allow_list(client, registered_dummy_agent):
    body = {"model": "dummy_agent", "messages": MESSAGES}
    assert client.post("/v1/chat/completions", json=body).status_code == 401
    forbidden = client.post("/v1/chat/completions", json=body, headers=bearer(make_token(azp="sovisuplus")))
    assert forbidden.status_code == 403
    assert forbidden.json() == {"error": "agent_not_allowed"}
    assert client.post("/v1/chat/completions", json={"model": "nope", "messages": MESSAGES},
                       headers=bearer(make_token(azp="open-webui"))).status_code == 404


def test_content_parts_are_accepted(client, registered_dummy_agent):
    messages = [{"role": "user", "content": [{"type": "text", "text": "How many words in: the quick brown fox?"}]}]
    response = client.post("/v1/chat/completions", json={"model": "dummy_agent", "messages": messages},
                           headers=bearer(make_token(azp="open-webui")))
    assert response.status_code == 200
