import asyncio
import inspect
import json
import threading
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from agents.sorbobot.graph import REQUIRED_TOOLS, GraphUnavailableError, MissingToolsError, SorbobotGraph
from agents.sorbobot.judges import ConceptJudge, PublicationJudge
from agents.sorbobot.taxi import QueryTooShortError, TaxiClient, TaxiUnavailableError
from tests.sorbobot_fakes import FakeGraph, FakeJudge, FakePublicationJudge, FakeTaxi


class FakeMCPTool:
    """A toolbox tool: a name and an ``ainvoke`` answering a JSON string."""

    def __init__(self, name: str, response: str):
        self.name = name
        self.response = response
        self.calls = []

    async def ainvoke(self, arguments: dict) -> str:
        self.calls.append(arguments)
        return self.response


def toolset(**responses: str) -> list[FakeMCPTool]:
    return [FakeMCPTool(name, responses.get(name, "null")) for name in REQUIRED_TOOLS]


MATCH = {"concept_uid": "t1", "rel_type": "HAS_TOPIC", "value": 0.8}


@asynccontextmanager
async def taxi(handler, prefix: str = "", timeout_seconds: float = 1.0):
    """A client of a loopback crisalid-taxi answering every match request with ``handler``."""
    app = web.Application()
    app.router.add_post(f"{prefix}/api/v1/match/", handler)
    async with TestServer(app) as server:
        yield TaxiClient(f"{server.make_url(prefix)}", 0.56, 50, timeout_seconds, 25)


def answering(matches: list) -> web.Response:
    return web.json_response({"results": [{"id": "query", "matches": matches}]})


def graph_of(tools, timeout_seconds: float = 30.0) -> SorbobotGraph:
    return SorbobotGraph(tools, timeout_seconds)


async def test_taxi_sends_max_topics_and_returns_topics_most_similar_first():
    sent = {}

    async def handler(request: web.Request) -> web.Response:
        sent.update(path=request.path, body=await request.json())
        return answering([
            {"concept_uid": "field", "rel_type": "HAS_FIELD", "value": 0.95},
            {"concept_uid": "t1", "rel_type": "HAS_TOPIC", "value": 0.6},
            {"concept_uid": "t2", "rel_type": "HAS_TOPIC", "value": 0.8},
        ])

    async with taxi(handler) as client:
        topics = await client.match_topics("machine learning for medical imaging")

    assert sent["path"] == "/api/v1/match/"
    assert sent["body"] == {
        "inputs": [{"id": "query", "text": "machine learning for medical imaging"}],
        "similarity_threshold": 0.56,
        "max_topics": 50,
    }
    assert [(topic.uid, topic.similarity) for topic in topics] == [("t2", 0.8), ("t1", 0.6)]


async def server_error(request: web.Request) -> web.Response:
    return web.Response(status=500)


async def not_json(request: web.Request) -> web.Response:
    return web.Response(text="Internal error")


async def too_slow(request: web.Request) -> web.Response:
    await asyncio.sleep(1)
    return answering([MATCH])


@pytest.mark.parametrize("handler", [server_error, not_json, too_slow], ids=["server-error", "not-json", "timeout"])
async def test_taxi_failures_raise_taxi_unavailable(handler):
    async with taxi(handler, timeout_seconds=0.1) as client:
        with pytest.raises(TaxiUnavailableError):
            await client.match_topics("machine learning for medical imaging")


async def test_taxi_is_unavailable_when_nothing_listens():
    async with taxi(server_error) as client:
        url = client.match_url

    with pytest.raises(TaxiUnavailableError):
        await TaxiClient(url.removesuffix("/api/v1/match/"), 0.56, 50, 1.0, 25).match_topics(
            "machine learning for medical imaging"
        )


async def test_the_taxi_url_keeps_the_path_prefix_of_a_reverse_proxy():
    async def handler(request: web.Request) -> web.Response:
        return answering([MATCH])

    async with taxi(handler, prefix="/taxi") as client:
        topics = await client.match_topics("machine learning for medical imaging")

    assert client.match_url.endswith("/taxi/api/v1/match/")
    assert [topic.uid for topic in topics] == ["t1"]


def test_a_missing_mcp_tool_fails_fast():
    with pytest.raises(MissingToolsError, match="list-document-summaries"):
        graph_of([tool for tool in toolset() if tool.name != "list-document-summaries"])


async def test_the_graph_facade_calls_and_decodes_the_toolbox():
    hierarchy = [
        {"uid": "t1", "ancestor_uid": "t1", "ancestor_name": "Logic", "dist": 0},
        {"uid": "t1", "ancestor_uid": "d", "ancestor_name": "Physical Sciences", "dist": 2},
        {"uid": "t1", "ancestor_uid": "f", "ancestor_name": "Computer Science", "dist": 1},
    ]
    tools = toolset(**{
        "get-concept-hierarchy": json.dumps(hierarchy),
        "list-concept-siblings": json.dumps([{"uid": "t2"}]),
    })
    graph = graph_of(tools)

    assert await graph.get_concept_paths(["t1"]) == {"t1": "Physical Sciences > Computer Science > Logic"}
    assert await graph.list_siblings(["t1", "t3"]) == ["t2"]
    assert await graph.list_expert_evidence(["t1"], 0.57) == []
    assert await graph.get_publication_counts([]) == {}
    calls = {tool.name: tool.calls for tool in tools if tool.calls}
    assert calls["list-concept-siblings"] == [{"uids": "t1,t3"}]
    assert "get-person-publication-counts" not in calls


def public_methods(cls) -> dict:
    return {
        name: inspect.signature(member)
        for name, member in inspect.getmembers(cls, inspect.isfunction)
        if not name.startswith("_")
    }


@pytest.mark.parametrize(
    ("real", "fake"),
    [
        (SorbobotGraph, FakeGraph),
        (TaxiClient, FakeTaxi),
        (ConceptJudge, FakeJudge),
        (PublicationJudge, FakePublicationJudge),
    ],
    ids=["graph", "taxi", "concept-judge", "publication-judge"],
)
def test_every_fake_offers_the_same_calls_as_the_client_it_stands_in_for(real, fake):
    expected, offered = public_methods(real), public_methods(fake)

    assert set(expected) <= set(offered)
    for name, signature in expected.items():
        assert list(offered[name].parameters) == list(signature.parameters), name


class KeepAliveTaxi(BaseHTTPRequestHandler):
    """A loopback crisalid-taxi. HTTP/1.1 keeps the connection open, which is what traps a pooled client."""

    protocol_version = "HTTP/1.1"

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        body = json.dumps({"results": [{"matches": [MATCH]}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_the_taxi_client_serves_requests_run_on_successive_event_loops():
    # The OpenWebUI adapter builds a new event loop for every request (common/agent.py), so a client holding
    # a connection from one request to the next fails the second one with "Event loop is closed".
    server = HTTPServer(("127.0.0.1", 0), KeepAliveTaxi)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    client = TaxiClient(f"http://127.0.0.1:{server.server_port}", 0.56, 50, 5.0, 25)

    try:
        for _ in range(3):
            loop = asyncio.new_event_loop()
            try:
                topics = loop.run_until_complete(client.match_topics("deep learning and neural networks"))
            finally:
                loop.close()
            assert [topic.uid for topic in topics] == ["t1"]
    finally:
        server.shutdown()


async def test_a_malformed_taxi_match_is_skipped_rather_than_raised():
    matches = [
        {"rel_type": "HAS_TOPIC", "value": 0.8},
        {"concept_uid": "t1", "rel_type": "HAS_TOPIC", "value": "not a number"},
        {"concept_uid": "t2", "rel_type": "HAS_TOPIC", "value": 0.7},
        "not a match at all",
    ]

    async def handler(request: web.Request) -> web.Response:
        return answering(matches)

    async with taxi(handler) as client:
        topics = await client.match_topics("deep learning and neural networks")

    assert [(topic.uid, topic.similarity) for topic in topics] == [("t2", 0.7)]


async def test_a_toolbox_answer_with_incomplete_rows_is_read_without_failing():
    graph = graph_of(
        toolset(
            **{
                "get-concepts-by-uid": json.dumps([{"name": "No uid"}, {"uid": "t1", "name": "T1"}]),
                "get-concept-hierarchy": json.dumps(
                    [{"uid": "t1", "ancestor_name": "Science"}, {"uid": "t1", "dist": 0, "ancestor_uid": "t1"}]
                ),
                "get-person-publication-counts": json.dumps(
                    [{"person_uid": "alice", "nb_publications_total": "12"}, {"nb_publications_total": 3}]
                ),
            }
        )
    )

    assert list(await graph.get_concepts(["t1"], 0.5)) == ["t1"]
    assert await graph.get_concept_paths(["t1"]) == {"t1": "Science > t1"}
    assert await graph.get_publication_counts(["alice"]) == {"alice": 12}


async def test_a_toolbox_call_that_never_answers_is_reported_as_unavailable():
    tools = toolset()
    for tool in tools:
        tool.ainvoke = never_answering
    graph = graph_of(tools, timeout_seconds=0.01)

    with pytest.raises(GraphUnavailableError, match="timed out"):
        await graph.list_children(["t1"])


async def never_answering(arguments: dict):
    await asyncio.sleep(10)


async def test_a_concept_path_carries_the_taxonomy_levels_from_domain_to_topic():
    rows = [
        {"uid": "t1", "ancestor_uid": "t1", "ancestor_name": "Neural Networks", "ancestor_type": "Topic", "dist": 0},
        {"uid": "t1", "ancestor_uid": "d1", "ancestor_name": "Physical Sciences", "ancestor_type": "Domain",
         "dist": 3},
        {"uid": "t1", "ancestor_uid": "f1", "ancestor_name": "Computer Science", "ancestor_type": "Field", "dist": 2},
        {"uid": "t1", "ancestor_uid": "s1", "ancestor_name": "Artificial Intelligence", "ancestor_type": "SubField",
         "dist": 1},
    ]
    graph = graph_of(toolset(**{"get-concept-hierarchy": json.dumps(rows)}))

    assert await graph.get_concept_paths(["t1"]) == {
        "t1": "Physical Sciences [Domain] > Computer Science [Field] > Artificial Intelligence [SubField] > "
        "Neural Networks [Topic]"
    }


async def test_a_query_too_short_for_taxi_is_refused_without_a_request():
    sent = []

    async def handler(request: web.Request) -> web.Response:
        sent.append(request)
        return web.json_response({"results": []})

    async with taxi(handler) as client:
        with pytest.raises(QueryTooShortError):
            await client.match_topics("deep learning")

    assert sent == []
