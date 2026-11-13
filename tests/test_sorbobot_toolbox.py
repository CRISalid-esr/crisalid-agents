import asyncio

import pytest

from agents.sorbobot.toolbox import REQUIRED_TOOLS, GraphUnavailableError, MissingToolsError, SorbobotToolbox, ToolboxConnection
from tests.sorbobot_fakes import FakeMCPTool, mcp_toolset

TOPIC = "https://openalex.org/T11010"


def test_a_missing_tool_is_a_deployment_error():
    tools = [tool for tool in mcp_toolset() if tool.name != "list-document-summaries"]
    with pytest.raises(MissingToolsError, match="list-document-summaries"):
        SorbobotToolbox(tools, 1.0)


async def test_null_answers_are_empty():
    toolbox = SorbobotToolbox(mcp_toolset(), 1.0)
    assert await toolbox.get_concepts([TOPIC], 0.5) == {}
    assert await toolbox.list_expert_evidence([TOPIC], 0.5) == []
    assert await toolbox.search_persons("dupont", 10) == []
    assert await toolbox.get_document_summaries(["d1"]) == {}


async def test_empty_uid_lists_do_not_call_the_toolbox():
    tools = mcp_toolset()
    toolbox = SorbobotToolbox(tools, 1.0)
    assert await toolbox.get_concepts([], 0.5) == {} and await toolbox.list_siblings([]) == []
    assert await toolbox.list_person_documents("p", [], 0.5, 5) == []
    assert all(tool.calls == [] for tool in tools)


async def test_rows_are_typed_and_incomplete_rows_dropped():
    tools = mcp_toolset(
        get_concepts_by_uid=[{"uid": TOPIC, "name": "Logic", "description": None, "nb_docs": "3"}, {"name": "no uid"}],
        get_concept_hierarchy=[
            {"uid": TOPIC, "ancestor_name": "Logic", "ancestor_type": "Topic", "dist": 0},
            {"uid": TOPIC, "ancestor_name": "Physical Sciences", "ancestor_type": "Domain", "dist": 3},
            {"uid": TOPIC, "ancestor_name": "Computer Science", "ancestor_type": "Field", "dist": 2},
            {"uid": TOPIC, "ancestor_name": "AI", "ancestor_type": "SubField", "dist": 1},
            {"uid": TOPIC, "ancestor_name": None, "dist": 9},
        ],
        list_concept_expert_evidence=[
            {"person_uid": "p1", "display_name": "P One", "doc_uid": "d1", "doc_title": "T", "publication_date": "2020",
             "topic_uid": TOPIC, "similarity": 0.8},
            {"person_uid": "p2", "doc_uid": "d2", "topic_uid": TOPIC, "similarity": "bad"},
            {"person_uid": "p3", "doc_uid": None, "topic_uid": TOPIC, "similarity": 0.9},
        ],
        get_person_publication_counts=[{"person_uid": "p1", "nb_publications_total": 12}, {"nb_publications_total": 1}],
        search_person_by_name=[{"uid": "p1", "display_name": "P One", "external": False, "score": 3.2}],
        list_person_research_concepts=[{"domain_uid": TOPIC, "domain_name": "Logic", "nb_publications": 4}, {"domain_name": "x"}],
        list_person_top_documents=[{"doc_uid": "d1", "title": "T", "publication_date": "2020-01-01", "concept_uids": [TOPIC],
                                    "roles": ["aut", "ths"]}, {"title": "no uid"}],
        list_document_summaries=[{"uid": "d1", "abstract": "A", "keywords": ["k", None]}, {"uid": "d2", "abstract": None}],
        search_concept_by_name=[{"uid": TOPIC, "name": "Logic", "type": "Topic"}],
    )
    toolbox = SorbobotToolbox(tools, 1.0, topic_sources="crisalid")
    concepts = await toolbox.get_concepts([TOPIC, " x "], 0.57)
    assert list(concepts) == [TOPIC] and concepts[TOPIC].nb_docs == 3 and concepts[TOPIC].description == ""
    assert tools[0].calls == [{"uids": f"{TOPIC},x", "similarity_threshold": 0.57, "sources": "crisalid"}]
    paths = await toolbox.get_concept_paths([TOPIC])
    assert paths == {TOPIC: "Physical Sciences [Domain] > Computer Science [Field] > AI [SubField] > Logic [Topic]"}
    rows = await toolbox.list_expert_evidence([TOPIC], 0.5)
    assert [(r.person_uid, r.similarity) for r in rows] == [("p1", 0.8)]
    assert await toolbox.get_publication_counts(["p1"]) == {"p1": 12}
    persons = await toolbox.search_persons("p one", 10)
    assert persons[0].uid == "p1" and persons[0].external is False
    concepts = await toolbox.list_person_concepts("p1", 0.5, 5)
    assert [(c.uid, c.nb_publications) for c in concepts] == [(TOPIC, 4)]
    docs = await toolbox.list_person_documents("p1", [TOPIC], 0.5, 5)
    assert [(d.uid, d.roles, d.concept_uids) for d in docs] == [("d1", ["aut", "ths"], [TOPIC])]
    summaries = await toolbox.get_document_summaries(["d1", "d2"])
    assert summaries["d1"].keywords == ("k",) and summaries["d2"].abstract is None
    assert await toolbox.search_concepts_by_name("Logic") == [{"uid": TOPIC, "name": "Logic", "type": "Topic"}]


async def test_timeout_failure_and_unreadable_answers_are_graph_unavailable():
    slow = mcp_toolset()
    slow[0].delay = 0.3
    with pytest.raises(GraphUnavailableError):
        await SorbobotToolbox(slow, 0.05).get_concepts([TOPIC], 0.5)
    failing = mcp_toolset()
    failing[0].error = RuntimeError("connection refused")
    with pytest.raises(GraphUnavailableError):
        await SorbobotToolbox(failing, 1.0).get_concepts([TOPIC], 0.5)
    garbage = mcp_toolset(get_concepts_by_uid="<html>")
    with pytest.raises(GraphUnavailableError):
        await SorbobotToolbox(garbage, 1.0).get_concepts([TOPIC], 0.5)
    scalar = mcp_toolset(get_concepts_by_uid="42")
    with pytest.raises(GraphUnavailableError):
        await SorbobotToolbox(scalar, 1.0).get_concepts([TOPIC], 0.5)


class FakeMCPClient:
    def __init__(self, tools):
        self.tools = tools
        self.loop = None
        self.closed = False

    async def aload_tools(self):
        self.loop = asyncio.get_running_loop()
        return self.tools

    async def aclose(self):
        self.closed = True


def test_connection_opens_one_client_per_event_loop_and_closes_it_after_the_last_turn():
    created = []

    def factory():
        client = FakeMCPClient(mcp_toolset(list_concept_siblings=[{"uid": "s1"}]))
        created.append(client)
        return client

    connection = ToolboxConnection(None, "sorbobot", 1.0, factory=factory)

    async def turn():
        await connection.acquire()
        await connection.acquire()  # a second request in flight on the same loop
        siblings = await connection.list_siblings(["t1"])
        await connection.release()
        assert not created[-1].closed  # still leased
        await connection.release()
        assert created[-1].closed
        return siblings

    assert asyncio.run(turn()) == ["s1"]
    assert asyncio.run(turn()) == ["s1"]
    assert len(created) == 2 and created[0].loop is not created[1].loop


def test_connection_loads_lazily_without_a_lease_and_rejects_unknown_attributes():
    created = []

    def factory():
        created.append(FakeMCPClient(mcp_toolset()))
        return created[-1]

    connection = ToolboxConnection(None, "sorbobot", 1.0, factory=factory)
    assert asyncio.run(connection.list_siblings([])) == [] and len(created) == 1
    with pytest.raises(AttributeError):
        connection.not_a_method  # noqa: B018
    assert set(REQUIRED_TOOLS) >= {"search-concept-by-name", "list-concept-siblings"}


def test_connection_failures_and_missing_tools():
    def unreachable():
        class Broken:
            async def aload_tools(self):
                raise ConnectionError("refused")

            async def aclose(self):
                pass

        return Broken()

    with pytest.raises(GraphUnavailableError):
        asyncio.run(ToolboxConnection(None, "sorbobot", 1.0, factory=unreachable).acquire())

    def incomplete():
        return FakeMCPClient([FakeMCPTool("get-concepts-by-uid")])

    with pytest.raises(MissingToolsError):
        asyncio.run(ToolboxConnection(None, "sorbobot", 1.0, factory=incomplete).acquire())
