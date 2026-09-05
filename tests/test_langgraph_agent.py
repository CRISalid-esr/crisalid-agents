import threading

import pytest
from langchain_core.messages import HumanMessage

from common.agent import ToolCall, ToolResult

QUESTION = [HumanMessage(content="How many words in: the quick brown fox?")]


@pytest.mark.asyncio
async def test_astream_emits_tool_call_then_result_then_tokens(dummy_agent):
    events = [e async for e in dummy_agent.astream(QUESTION)]

    assert isinstance(events[0], ToolCall)
    assert events[0].name == "count_words"
    assert events[0].args == {"text": "the quick brown fox"}

    assert isinstance(events[1], ToolResult)
    assert events[1].id == events[0].id
    assert events[1].name == "count_words"
    assert events[1].result == "4"

    tokens = events[2:]
    assert all(isinstance(t, str) for t in tokens)
    assert "".join(tokens) == "There are 4 words."


@pytest.mark.asyncio
async def test_ainvoke_returns_final_message(dummy_agent):
    answer = await dummy_agent.ainvoke(QUESTION)
    assert answer.content == "There are 4 words."


def test_sync_stream_works_from_a_thread_without_event_loop(dummy_agent):
    collected = []

    def run():
        collected.extend(dummy_agent.stream(QUESTION))

    thread = threading.Thread(target=run)
    thread.start()
    thread.join()

    assert isinstance(collected[0], ToolCall)
    assert isinstance(collected[1], ToolResult)
    assert "".join(e for e in collected if isinstance(e, str)) == "There are 4 words."


@pytest.mark.asyncio
async def test_graph_is_built_once(dummy_agent):
    graph = await dummy_agent.get_graph()
    assert graph is not None
    assert await dummy_agent.get_graph() is graph
