import pytest
from langchain_core.messages import AIMessage

from agents.dummy_agent.agent import DummyAgent
from common.registry import registry
from tests.fake_llm import ScriptedChatModel, tool_call_message


def pytest_addoption(parser):
    parser.addoption("--run-pdf", action="store_true", default=False,
                     help="also parse the real work programme PDFs of HORIZON_WP_DIR")


def scripted_dummy_agent() -> DummyAgent:
    # One tool call, then a final answer streamed word by word.
    llm = ScriptedChatModel(
        responses=[
            tool_call_message("count_words", {"text": "the quick brown fox"}),
            AIMessage(content="There are 4 words."),
        ],
        calls=[],
    )
    return DummyAgent(llm=llm)


@pytest.fixture
def dummy_agent() -> DummyAgent:
    return scripted_dummy_agent()


@pytest.fixture
def registered_dummy_agent(dummy_agent):
    registry.reset()
    registry.register(dummy_agent)
    yield dummy_agent
    registry.reset()
