import pytest
from langchain_core.messages import AIMessage, HumanMessage

import common.semantic_params as semantic_params
from common.embedding import EmbeddingServiceError
from common.semantic_params import embed_semantic_params, strip_vector_args
from tests.fake_llm import tool_call_message


class _FakeProvider:
    async def embed_text(self, text: str) -> list[float]:
        return [float(len(text)), 0.5]


class _DownProvider:
    async def embed_text(self, text: str) -> list[float]:
        raise EmbeddingServiceError("down")


@pytest.mark.asyncio
async def test_embeds_semantic_text_params(monkeypatch):
    monkeypatch.setattr(semantic_params, "get_embedding_provider", lambda: _FakeProvider())
    message = tool_call_message("search", {"semantic_topic": "graph theory", "limit": 3})

    result = await embed_semantic_params(message)

    assert result.tool_calls[0]["args"] == {
        "semantic_topic": "graph theory",
        "semantic_topic_vector": [12.0, 0.5],
        "limit": 3,
    }


@pytest.mark.asyncio
async def test_leaves_messages_without_semantic_params_untouched(monkeypatch):
    monkeypatch.setattr(semantic_params, "get_embedding_provider", lambda: _DownProvider())
    message = tool_call_message("search", {"name": "informatique"})

    assert await embed_semantic_params(message) is message


@pytest.mark.asyncio
async def test_embedding_outage_becomes_a_plain_answer(monkeypatch):
    monkeypatch.setattr(semantic_params, "get_embedding_provider", lambda: _DownProvider())
    message = tool_call_message("search", {"semantic_topic": "graph theory"})

    result = await embed_semantic_params(message)

    assert result.tool_calls == []
    assert "embedding service is currently unavailable" in result.content


def test_strip_vector_args_removes_vectors_only():
    messages = [
        HumanMessage(content="hi"),
        AIMessage(content="", tool_calls=[{
            "id": "c1", "name": "search", "type": "tool_call",
            "args": {"semantic_topic": "x", "semantic_topic_vector": [0.1], "limit": 3},
        }]),
    ]

    stripped = strip_vector_args(messages)

    assert stripped[0] is messages[0]
    assert stripped[1].tool_calls[0]["args"] == {"semantic_topic": "x", "limit": 3}
