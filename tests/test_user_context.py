"""The identity bound around a turn reaches the toolbox token getter through the LangGraph tool node."""

import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from common.langgraph_agent import LangGraphAgent
from common.mcp_toolbox_client import MCPToolboxClient, NoUserTokenError
from common.user_context import UserPrincipal, bind_user, current_toolbox_token, stream_as_user
from tests.fake_llm import ScriptedChatModel, tool_call_message


@tool
def whoami() -> str:
    """Return the toolbox token of the current request."""
    return MCPToolboxClient._delegated_token()


class WhoamiAgent(LangGraphAgent):
    def __init__(self):
        super().__init__("whoami_agent", "Whoami")
        self.llm = ScriptedChatModel(
            responses=[tool_call_message("whoami", {}), AIMessage(content="done")], calls=[]
        )

    async def build_graph(self):
        graph = StateGraph(MessagesState)

        async def call_model(state):
            return {"messages": [await self.llm.ainvoke(state["messages"])]}

        graph.add_node("agent", call_model)
        graph.add_node("tools", ToolNode([whoami]))
        graph.add_edge(START, "agent")
        graph.add_conditional_edges("agent", lambda s: "tools" if s["messages"][-1].tool_calls else END)
        graph.add_edge("tools", "agent")
        return graph.compile()


PRINCIPAL = UserPrincipal(sub="s", username="u", email=None, client_id="c", token="t", exp=0)


async def test_token_reaches_tool_call_through_stream_as_user():
    agent = WhoamiAgent()
    stream = stream_as_user(PRINCIPAL, "exchanged-token", agent.astream([HumanMessage(content="hi")]))
    events = [e async for e in stream]
    results = [e for e in events if getattr(e, "name", None) == "whoami"]
    assert results and results[-1].result == "exchanged-token"
    assert current_toolbox_token.get() is None


async def test_concurrent_requests_keep_their_own_token():
    async def run(token: str) -> str:
        agent = WhoamiAgent()
        events = [e async for e in stream_as_user(PRINCIPAL, token, agent.astream([HumanMessage(content="hi")]))]
        return [e for e in events if getattr(e, "name", None) == "whoami"][-1].result

    assert await asyncio.gather(run("one"), run("two")) == ["one", "two"]


def test_delegated_getter_without_request_context():
    with pytest.raises(NoUserTokenError):
        MCPToolboxClient._delegated_token()
    with bind_user(PRINCIPAL, "x"):
        assert MCPToolboxClient._delegated_token() == "x"


def test_service_account_provider_keeps_client_credentials(monkeypatch):
    client = MCPToolboxClient(keycloak_issuer="https://k/realms/r", keycloak_client_id="c",
                              keycloak_client_secret="s", token_provider="service_account")
    getter = client._make_token_getter()
    assert getter is not MCPToolboxClient._delegated_token
    assert MCPToolboxClient(token_provider="delegated")._make_token_getter() is MCPToolboxClient._delegated_token
