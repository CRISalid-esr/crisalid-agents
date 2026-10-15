"""Sorbobot: research expertise of Sorbonne University over the CRISalid graph.

The graph is the ReAct loop of the framework (agent → tools → agent). The LLM only sees the business tools of
``tools.py``; each runs a deterministic search over crisalid-taxi and the ``sorbobot`` MCP toolset. Nothing is
stored between requests: the adapters send the whole conversation with every message.
"""

import os
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.runnables import RunnableLambda
from langgraph.graph import END, StateGraph
from langgraph.graph.message import MessagesState
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode

from agents.sorbobot.config import SorbobotSettings
from agents.sorbobot.graph import SorbobotGraph
from agents.sorbobot.judges import ConceptJudge, PublicationJudge
from agents.sorbobot.services import SorbobotServices
from agents.sorbobot.taxi import TaxiClient
from agents.sorbobot.tools import build_tools
from common.langgraph_agent import LangGraphAgent
from common.llm import build_chat_model
from common.mcp_toolbox_client import MCPToolboxClient
from common.tool_calls import fix_raw_tool_calls

SYSTEM_PROMPT_FILE = Path(__file__).resolve().parent / "system_prompt.md"

DEFAULT_TOOLSET = "sorbobot"


class SorbobotAgent(LangGraphAgent):
    def __init__(self, llm: BaseChatModel | None = None, services: SorbobotServices | None = None):
        super().__init__(
            name="sorbobot",
            display_name="Sorbobot",
            description=(
                "Finds Sorbonne University experts on a research topic, describes and summarises the expertise "
                "of a researcher, and explores research domains"
            ),
        )
        self.toolbox_client = MCPToolboxClient(
            toolbox_url=os.getenv("SORBOBOT_MCP_TOOLBOX_URL"),
            toolset_name=os.getenv("SORBOBOT_MCP_TOOLBOX_TOOLSET", DEFAULT_TOOLSET),
        )
        # Injected by the offline tests; built on first use otherwise.
        self._llm = llm
        self._services = services
        self._owns_services = services is None

    async def _build_services(self, llm: BaseChatModel) -> SorbobotServices:
        # Settings and prompt files first: a bad configuration fails before the toolbox is contacted.
        settings = SorbobotSettings.from_env()
        # A threshold of 0 disables both judges: they are simply not built.
        judging = settings.judge_threshold > 0
        concept_judge = ConceptJudge(llm, settings.judge_timeout_seconds) if judging else None
        publication_judge = PublicationJudge(llm, settings.judge_timeout_seconds) if judging else None
        taxi = TaxiClient(
            base_url=settings.taxi_url,
            similarity_threshold=settings.semantic_threshold,
            max_topics=settings.taxi_max_topics,
            timeout_seconds=settings.taxi_timeout_seconds,
            min_query_chars=settings.taxi_min_query_chars,
        )
        graph = SorbobotGraph(await self.toolbox_client.aload_tools(), settings.toolbox_timeout_seconds)
        return SorbobotServices(
            graph=graph,
            taxi=taxi,
            settings=settings,
            concept_judge=concept_judge,
            publication_judge=publication_judge,
        )

    async def _ensure_services(self, llm: BaseChatModel) -> SorbobotServices:
        """Built on first use."""
        if self._services is None:
            built = await self._build_services(llm)
            if self._services is None:
                self._services = built
        return self._services

    async def build_graph(self) -> CompiledStateGraph:
        llm = self._llm or build_chat_model()
        services = await self._ensure_services(llm)
        system_prompt = SYSTEM_PROMPT_FILE.read_text(encoding="utf-8")
        tools = build_tools(services)

        llm_with_tools = llm.bind_tools(tools).with_retry(
            retry_if_exception_type=(ValueError,),
            stop_after_attempt=3,
        ) | RunnableLambda(fix_raw_tool_calls)

        async def call_model(state: MessagesState):
            messages = [SystemMessage(content=system_prompt)] + state["messages"]
            try:
                return {"messages": [await llm_with_tools.ainvoke(messages)]}
            except ValueError:
                # The model returned an empty stream (e.g. transient API error).
                return {"messages": [AIMessage(content="The model returned an empty response. Please try again.")]}

        def should_continue(state: MessagesState):
            if state["messages"][-1].tool_calls:
                return self.tools_node
            return END

        graph = StateGraph(MessagesState)
        graph.add_node(self.agent_node, call_model)
        graph.add_node(self.tools_node, ToolNode(tools))
        graph.set_entry_point(self.agent_node)
        graph.add_conditional_edges(self.agent_node, should_continue)
        graph.add_edge(self.tools_node, self.agent_node)
        return graph.compile()

    async def aclose(self) -> None:
        if self._owns_services:
            self._services = None
        self._graph = None
        await self.toolbox_client.aclose()


def create_agent(llm: BaseChatModel | None = None, services: SorbobotServices | None = None) -> SorbobotAgent:
    return SorbobotAgent(llm=llm, services=services)
