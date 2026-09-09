"""Dummy agent: a minimal LangGraph ReAct loop with one local tool.

Everything below is plain LangGraph / LangChain code. The interface adapters (OpenWebUI
pipeline, chat API), the event streaming and the discovery are provided by ``common/``.
"""

from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langchain_core.tools import tool
from langgraph.graph import END, StateGraph
from langgraph.graph.message import MessagesState
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode

from common.langgraph_agent import LangGraphAgent
from common.llm import build_chat_model

_SYSTEM_PROMPT = (Path(__file__).resolve().parent / "system_prompt.md").read_text(encoding="utf-8")


@tool
def count_words(text: str) -> int:
    """Count the words in a piece of text."""
    return len(text.split())


class DummyAgent(LangGraphAgent):
    def __init__(self, llm: BaseChatModel | None = None):
        super().__init__(
            name="dummy_agent",
            display_name="Dummy agent",
            description="Reference agent: answers questions and counts words with a local tool.",
        )
        # An injected model allows offline tests with a fake chat model.
        self._llm = llm

    async def build_graph(self) -> CompiledStateGraph:
        # Called once, on first use. Async so that agents can open connections here.
        llm = self._llm or build_chat_model()
        tools = [count_words]
        llm_with_tools = llm.bind_tools(tools)

        async def call_model(state: MessagesState):
            # The "agent" node: one LLM call over the system prompt + conversation.
            # The returned AIMessage either carries tool_calls or the final answer.
            messages = [SystemMessage(content=_SYSTEM_PROMPT), *state["messages"]]
            return {"messages": [await llm_with_tools.ainvoke(messages)]}

        def should_continue(state: MessagesState):
            # Route to the tools node while the model keeps asking for tools.
            if state["messages"][-1].tool_calls:
                return self.tools_node
            return END

        graph = StateGraph(MessagesState)
        graph.add_node(self.agent_node, call_model)
        # ToolNode executes every tool call of the last AIMessage and appends ToolMessages.
        graph.add_node(self.tools_node, ToolNode(tools))
        graph.set_entry_point(self.agent_node)
        graph.add_conditional_edges(self.agent_node, should_continue)
        graph.add_edge(self.tools_node, self.agent_node)

        return graph.compile()


def create_agent(llm: BaseChatModel | None = None) -> DummyAgent:
    return DummyAgent(llm=llm)
