from langchain_core.messages import AIMessage, HumanMessage

from agents.dummy_agent.agent import create_agent
from common.agent import ToolCall, ToolResult
from tests.fake_llm import ScriptedChatModel, tool_call_message


def test_dummy_agent_streams_tool_call_and_answer():
    llm = ScriptedChatModel(
        responses=[
            tool_call_message("count_words", {"text": "a b c"}),
            AIMessage(content="3 words."),
        ],
        calls=[],
    )
    agent = create_agent(llm=llm)

    events = list(agent.stream([HumanMessage(content="How many words in: a b c?")]))

    assert agent.name == "dummy_agent"
    assert isinstance(events[0], ToolCall)
    assert isinstance(events[1], ToolResult)
    assert events[1].result == "3"
    assert "".join(e for e in events if isinstance(e, str)) == "3 words."
