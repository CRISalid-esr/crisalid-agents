import json

from langchain_core.messages import AIMessage, HumanMessage

from agents.project_topic_matching.agent import create_agent, hit_to_dict
from common.agent import ToolCall, ToolResult
from common.horizon.models import TopicHit
from tests.fake_llm import ScriptedChatModel, tool_call_message


def _hit(topic_id: str, score: float, passage: str | None = "Scope text") -> TopicHit:
    return TopicHit(
        topic_id=topic_id, score=score, title=f"Title {topic_id}", call_id="HORIZON-CL2-2026-01", call_name="Call",
        cluster="CL2", destination="Dest", type_of_action="RIA", deadlines=["2026-09-23"], opening_date="2026-05-12",
        contribution_min_eur=3_500_000, contribution_max_eur=4_000_000, indicative_budget_eur=12_000_000,
        best_passage=passage, best_passage_section="scope",
    )


class FakeSearch:
    def __init__(self):
        self.queries = []

    async def search_topics(self, query, *, cluster=None, type_of_action=None, top_k=10):
        self.queries.append((query, cluster, type_of_action, top_k))
        return [_hit("HORIZON-CL2-2026-01-HERITAGE-02", 0.032), _hit("HORIZON-CL2-2026-01-HERITAGE-01", 0.016, None)]

    async def get_topic(self, topic_id):
        if topic_id != "HORIZON-CL2-2026-01-HERITAGE-02":
            return None
        return {"topic_id": topic_id, "title": "Boosting creative startups", "scope": "Full scope.",
                "expected_outcome": "Outcomes.", "contribution_min_eur": 5_000_000, "contribution_max_eur": 6_000_000,
                "indicative_budget_eur": 12_000_000, "deadlines": ["2026-09-23"]}

    async def list_clusters(self):
        return [{"cluster": "CL2", "topics": 54, "label": "Culture"}]


def test_project_topic_matching_streams_tool_calls_and_answer():
    llm = ScriptedChatModel(
        responses=[
            tool_call_message("search_horizon_topics", {"query": "creative startups scale-up", "cluster": "CL2"}, "call-1"),
            tool_call_message("search_horizon_topics", {"query": "cultural industries innovation"}, "call-2"),
            AIMessage(content="Best fit: HORIZON-CL2-2026-01-HERITAGE-02."),
        ],
        calls=[],
    )
    search = FakeSearch()
    agent = create_agent(llm=llm, search=search)

    events = list(agent.stream([HumanMessage(content="A startup accelerator for creative industries")]))

    assert agent.name == "project_topic_matching" and agent.display_name == "Horizon topic finder"
    assert [type(e).__name__ for e in events[:4]] == ["ToolCall", "ToolResult", "ToolCall", "ToolResult"]
    assert events[0].args == {"query": "creative startups scale-up", "cluster": "CL2"}
    assert search.queries[0] == ("creative startups scale-up", "CL2", None, 10)
    result = json.loads(events[1].result)
    assert [r["topic_id"] for r in result] == ["HORIZON-CL2-2026-01-HERITAGE-02", "HORIZON-CL2-2026-01-HERITAGE-01"]
    assert result[0]["eu_contribution"] == "EUR 3.50 to 4.00 M per project"
    assert result[0]["best_matching_passage"] == {"section": "scope", "text": "Scope text"}
    assert result[1]["best_matching_passage"]["text"] == ""
    assert "".join(e for e in events if isinstance(e, str)) == "Best fit: HORIZON-CL2-2026-01-HERITAGE-02."


def test_get_topic_and_list_clusters_tools():
    llm = ScriptedChatModel(
        responses=[
            tool_call_message("list_horizon_clusters", {}, "call-1"),
            tool_call_message("get_horizon_topic", {"topic_id": "HORIZON-CL2-2026-01-HERITAGE-02"}, "call-2"),
            tool_call_message("get_horizon_topic", {"topic_id": "NOPE"}, "call-3"),
            AIMessage(content="Done."),
        ],
        calls=[],
    )
    agent = create_agent(llm=llm, search=FakeSearch())
    results = [e for e in agent.stream([HumanMessage(content="Tell me more")]) if isinstance(e, ToolResult)]
    assert json.loads(results[0].result) == [{"cluster": "CL2", "topics": 54, "label": "Culture"}]
    topic = json.loads(results[1].result)
    assert topic["scope"] == "Full scope." and topic["eu_contribution"] == "EUR 5.00 to 6.00 M per project"
    assert topic["indicative_budget"] == "EUR 12.00 M"
    assert json.loads(results[2].result) == {"error": "Unknown topic id NOPE"}


def test_hit_excerpt_is_truncated_at_a_word_boundary():
    long_passage = " ".join(["word"] * 200)
    data = hit_to_dict(_hit("T", 1.0, long_passage))
    text = data["best_matching_passage"]["text"]
    assert text.endswith("…") and len(text) <= 402 and " word…" in text
    assert hit_to_dict(_hit("T", 1.0, None))["best_matching_passage"]["text"] == ""


def test_opensearch_client_is_bound_to_each_turn_event_loop(monkeypatch):
    """OpenWebUI drives agents through the sync bridge: a new event loop per turn, closed afterwards."""
    import asyncio

    from agents.project_topic_matching import agent as agent_module
    from common.horizon.client import LoopBoundOpenSearch
    from common.horizon.search import HorizonSearch

    created = []

    class FakeClient:
        def __init__(self):
            self.loop = asyncio.get_running_loop()
            self.closed = False
            created.append(self)

        async def search(self, index, body, params=None):
            assert asyncio.get_running_loop() is self.loop and not self.loop.is_closed()
            if "aggs" in body:
                return {"aggregations": {"clusters": {"buckets": []}}}
            return {"hits": {"hits": []}}

        async def close(self):
            self.closed = True

    class FakeEmbedder:
        async def embed_text(self, text):
            return [1.0]

    monkeypatch.setenv("HORIZON_OS_URL", "http://unused:9200")
    original = agent_module.LoopBoundOpenSearch
    monkeypatch.setattr(agent_module, "LoopBoundOpenSearch",
                        lambda settings: original(settings, factory=lambda s: FakeClient()))
    monkeypatch.setattr(agent_module, "get_embedding_provider", lambda: FakeEmbedder())

    llm = ScriptedChatModel(
        responses=[
            tool_call_message("search_horizon_topics", {"query": "q1"}, "call-1"), AIMessage(content="A1"),
            tool_call_message("search_horizon_topics", {"query": "q2"}, "call-2"), AIMessage(content="A2"),
        ],
        calls=[],
    )
    agent = create_agent(llm=llm)
    first = list(agent.stream([HumanMessage(content="turn 1")]))
    second = list(agent.stream([HumanMessage(content="turn 1"), AIMessage(content="A1"), HumanMessage(content="turn 2")]))

    assert [e for e in first if isinstance(e, str)] and "".join(e for e in second if isinstance(e, str)) == "A2"
    assert all(isinstance(e, (ToolCall, ToolResult, str)) for e in second)
    assert json.loads([e for e in second if isinstance(e, ToolResult)][0].result) == []
    assert len(created) == 2 and created[0].loop is not created[1].loop and all(c.closed for c in created)
