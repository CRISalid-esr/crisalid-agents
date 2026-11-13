"""Horizon topic finder: a LangGraph ReAct loop over the Horizon Europe topic index.

Given a project title or idea, the agent writes a few English search queries, runs each one through the hybrid
search of ``common.horizon`` (one tool call per query, visible to the user), merges the results and presents the
best-fitting work programme topics. Everything below is plain LangGraph / LangChain code; the interface adapters,
the event streaming and the discovery are provided by ``common/``.
"""

import json
from collections.abc import AsyncIterator
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.graph import END, StateGraph
from langgraph.graph.message import MessagesState
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode

from common.embedding import get_embedding_provider
from common.horizon.client import HorizonSettings, LoopBoundOpenSearch
from common.horizon.models import TopicHit
from common.horizon.search import HorizonSearch
from common.agent import AgentEvent
from common.langgraph_agent import LangGraphAgent
from common.llm import build_chat_model

_SYSTEM_PROMPT = (Path(__file__).resolve().parent / "system_prompt.md").read_text(encoding="utf-8")
EXCERPT_CHARS = 400


def _money(value: int | None) -> str | None:
    return f"EUR {value / 1_000_000:.2f} M" if value else None


def _contribution(hit_min: int | None, hit_max: int | None) -> str | None:
    if hit_min and hit_max and hit_min != hit_max:
        return f"EUR {hit_min / 1_000_000:.2f} to {hit_max / 1_000_000:.2f} M per project"
    return f"{_money(hit_max or hit_min)} per project" if (hit_max or hit_min) else None


def hit_to_dict(hit: TopicHit) -> dict:
    excerpt = (hit.best_passage or "").replace("\n", " ")
    if len(excerpt) > EXCERPT_CHARS:
        excerpt = excerpt[:EXCERPT_CHARS].rsplit(" ", 1)[0] + "…"
    return {
        "topic_id": hit.topic_id,
        "title": hit.title,
        "cluster": hit.cluster,
        "call_id": hit.call_id,
        "type_of_action": hit.type_of_action,
        "deadlines": hit.deadlines,
        "opening_date": hit.opening_date,
        "destination": hit.destination,
        "eu_contribution": _contribution(hit.contribution_min_eur, hit.contribution_max_eur),
        "score": round(hit.score, 4),
        "best_matching_passage": {"section": hit.best_passage_section, "text": excerpt},
    }


def build_tools(search: HorizonSearch) -> list:
    @tool
    async def search_horizon_topics(
        query: str, cluster: str | None = None, type_of_action: str | None = None, top_k: int = 10
    ) -> str:
        """Search the Horizon Europe work programme topics with one English query (hybrid keyword + semantic
        search). Returns the best-matching topics with their id, title, call, type of action, deadlines, EU
        contribution and the passage of the topic that matches the query best. Optional filters: cluster
        (CL1 … CL6) and type of action (RIA, IA, CSA, COFUND, PCP, PPI)."""
        hits = await search.search_topics(query, cluster=cluster, type_of_action=type_of_action, top_k=top_k)
        return json.dumps([hit_to_dict(h) for h in hits], ensure_ascii=False)

    @tool
    async def get_horizon_topic(topic_id: str) -> str:
        """Return the full text of one work programme topic (expected outcome, scope, specific conditions,
        budget, deadlines) from its id, e.g. HORIZON-CL2-2026-01-HERITAGE-02."""
        topic = await search.get_topic(topic_id)
        if topic is None:
            return json.dumps({"error": f"Unknown topic id {topic_id}"})
        fields = (
            "topic_id", "title", "cluster", "cluster_label", "call_id", "call_name", "destination", "type_of_action",
            "type_of_action_label", "stage", "opening_date", "deadlines", "expected_projects", "trl",
            "expected_outcome", "scope", "conditions_text",
        )
        data = {k: topic.get(k) for k in fields}
        data["eu_contribution"] = _contribution(topic.get("contribution_min_eur"), topic.get("contribution_max_eur"))
        data["indicative_budget"] = _money(topic.get("indicative_budget_eur"))
        return json.dumps(data, ensure_ascii=False)

    @tool
    async def list_horizon_clusters() -> str:
        """List the Horizon Europe clusters present in the topic index (id, label, number of topics), to narrow
        a search with the cluster filter."""
        return json.dumps(await search.list_clusters(), ensure_ascii=False)

    return [search_horizon_topics, get_horizon_topic, list_horizon_clusters]


class ProjectTopicMatchingAgent(LangGraphAgent):
    def __init__(self, llm: BaseChatModel | None = None, search: HorizonSearch | None = None):
        super().__init__(
            name="project_topic_matching",
            display_name="Horizon topic finder",
            description="Finds the Horizon Europe work programme topics that best fit a project idea.",
        )
        # An injected model and search allow offline tests; otherwise the OpenSearch client is opened lazily,
        # once per event loop (OpenWebUI runs every turn in a new loop) and closed at the end of each turn.
        self._llm = llm
        self._search = search
        self._client: LoopBoundOpenSearch | None = None

    async def _get_search(self) -> HorizonSearch:
        if self._search is None:
            settings = HorizonSettings.from_env()
            self._client = LoopBoundOpenSearch(settings)
            self._search = HorizonSearch(self._client, get_embedding_provider(), settings)
        return self._search

    async def astream(self, messages: list[BaseMessage]) -> AsyncIterator[AgentEvent]:
        try:
            async for event in super().astream(messages):
                yield event
        finally:
            if self._client is not None:
                await self._client.close()

    async def build_graph(self) -> CompiledStateGraph:
        # Called once, on first use: opens the OpenSearch connection.
        llm = self._llm or build_chat_model()
        tools = build_tools(await self._get_search())
        llm_with_tools = llm.bind_tools(tools)

        async def call_model(state: MessagesState):
            messages = [SystemMessage(content=_SYSTEM_PROMPT), *state["messages"]]
            return {"messages": [await llm_with_tools.ainvoke(messages)]}

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
        if self._client is not None:
            await self._client.close()


def create_agent(llm: BaseChatModel | None = None, search: HorizonSearch | None = None) -> ProjectTopicMatchingAgent:
    return ProjectTopicMatchingAgent(llm=llm, search=search)
