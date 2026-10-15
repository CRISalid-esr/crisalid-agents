import asyncio
import json
import re
import threading
from pathlib import Path

import httpx
import openai
import pytest
from langchain_core.messages import AIMessage, HumanMessage

import agents.sorbobot.agent
from agents.sorbobot import tools
from agents.sorbobot.agent import SorbobotAgent, create_agent
from agents.sorbobot.graph import GraphUnavailableError
from agents.sorbobot.taxi import QueryTooShortError
from common.agent import ToolCall, ToolResult
from tests.fake_llm import ScriptedChatModel, tool_call_message
from tests.sorbobot_fakes import FakeGraph, FakeTaxi, evidence, make_services


def run_tool(tool_name: str, args: dict, services) -> list:
    llm = ScriptedChatModel(responses=[tool_call_message(tool_name, args), AIMessage(content="Done.")], calls=[])
    return list(create_agent(llm=llm, services=services).stream([HumanMessage(content="A question")]))


def tool_result(events: list) -> dict:
    return json.loads(next(event for event in events if isinstance(event, ToolResult)).result)


def test_a_tool_call_is_streamed_before_its_result_and_the_answer():
    graph = FakeGraph(evidence={frozenset({"t1"}): [evidence("alice", "d1", "t1")]})

    events = run_tool("find_domain_experts", {"topic": "machine learning"}, make_services(graph=graph))

    assert isinstance(events[0], ToolCall)
    assert events[0].args == {"topic": "machine learning"}
    assert tool_result(events)["experts"][0]["name"] == "Alice"
    assert "".join(event for event in events if isinstance(event, str)) == "Done."


def test_expected_failures_are_explained_to_the_llm():
    unavailable = run_tool("explore_domains", {"topic": "vision"}, make_services(taxi=FakeTaxi(unavailable=True)))
    unknown = run_tool(
        "get_researcher_profile", {"person_name": "Nobody Known", "question": "summary"}, make_services()
    )

    assert "unavailable" in tool_result(unavailable)["error"]
    assert "No Sorbonne University researcher" in tool_result(unknown)["error"]


@pytest.mark.parametrize("tool_name", ["find_domain_experts", "explore_domains"])
def test_topics_without_documents_are_explained_to_the_llm(tool_name):
    services = make_services(graph=FakeGraph(counts={"t1": 0}))

    events = run_tool(tool_name, {"topic": "computer vision and image processing"}, services)

    assert tool_result(events)["error"] == "No document of the database is linked to the topics found."


class BusyChatModel(ScriptedChatModel):
    def _next(self, messages):
        request = httpx.Request("POST", "https://llm.test/v1/chat/completions")
        raise openai.InternalServerError(
            "Error code: 503 - model is busy", response=httpx.Response(503, request=request), body=None
        )


def test_an_unavailable_language_model_is_reported_to_the_user():
    agent = create_agent(llm=BusyChatModel(responses=[], calls=[]), services=make_services())

    answer = "".join(event for event in agent.stream([HumanMessage(content="A question")]) if isinstance(event, str))

    assert answer.startswith("Error: the language model is currently unavailable")
    assert "503" in answer


def test_the_sorbobot_toolset_is_loaded_by_default(monkeypatch):
    monkeypatch.delenv("SORBOBOT_MCP_TOOLBOX_TOOLSET", raising=False)
    monkeypatch.setenv("CRISALID_MCP_TOOLBOX_TOOLSET", "crisalid-restricted")

    assert create_agent().toolbox_client.toolset_name == "sorbobot"


def test_a_toolbox_failure_is_explained_to_the_llm():
    class BrokenGraph(FakeGraph):
        async def get_concepts(self, uids, similarity_threshold):
            raise GraphUnavailableError("toolbox is down")

    broken = run_tool("find_domain_experts", {"topic": "vision"}, make_services(graph=BrokenGraph()))

    assert "unavailable" in tool_result(broken)["error"]


def scripted_agent(monkeypatch, services, builds: list) -> SorbobotAgent:
    async def build_services(self, llm):
        builds.append(llm)
        return services

    monkeypatch.setattr(SorbobotAgent, "_build_services", build_services)
    return create_agent(llm=ScriptedChatModel(responses=[], calls=[]))


def test_services_are_built_once_and_dropped_with_the_agent(monkeypatch):
    services, builds = make_services(), []
    agent = scripted_agent(monkeypatch, services, builds)

    async def one_request_then_close():
        assert await agent._ensure_services(agent._llm) is services
        assert await agent._ensure_services(agent._llm) is services
        await agent.aclose()

    asyncio.run(one_request_then_close())

    assert len(builds) == 1
    assert agent._services is None


def test_a_cold_start_served_on_two_event_loops_never_blocks(monkeypatch):
    # Each OpenWebUI request runs on its own event loop (common/agent.py), so nothing here may wait on a
    # primitive owned by another loop: two concurrent first requests must both come back.
    services, builds = make_services(), []
    agent = scripted_agent(monkeypatch, services, builds)
    seen = []

    def request():
        loop = asyncio.new_event_loop()
        try:
            seen.append(loop.run_until_complete(agent._ensure_services(agent._llm)))
        finally:
            loop.close()

    threads = [threading.Thread(target=request) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not any(thread.is_alive() for thread in threads), "a request is stuck waiting on another loop"
    assert seen == [services, services]


def test_closing_the_agent_leaves_injected_services_alone():
    services = make_services()
    agent = create_agent(services=services)

    asyncio.run(agent.aclose())

    assert agent._services is services


def experts_template_placeholders() -> set:
    prompt = (Path(agents.sorbobot.agent.__file__).parent / "system_prompt.md").read_text(encoding="utf-8")
    template = prompt.split("<answer_experts>", 1)[1].split("```")[1]
    return set(re.findall(r"<([a-z_ ]+)>", template))


def test_the_experts_template_only_names_fields_the_tool_really_sends():
    graph = FakeGraph(evidence={frozenset({"t1"}): [evidence("alice", "d1", "t1")]})
    payload = tool_result(run_tool("find_domain_experts", {"topic": "vision"}, make_services(graph)))

    available = set(payload) | set(payload["domains"][0]) | set(payload["experts"][0])

    assert experts_template_placeholders() - {"ranking block"} <= available
    # The domains of this answer are shown by their path, so no bare name is offered to the model.
    assert "name" not in payload["domains"][0]
    assert {"path", "nb_publications"} <= set(payload["domains"][0])


def test_a_topic_too_short_for_taxi_tells_the_llm_how_to_retry():
    class ShortRefusingTaxi(FakeTaxi):
        min_query_chars = 40

        async def match_topics(self, text):
            raise QueryTooShortError("too short")

    events = run_tool("find_domain_experts", {"topic": "biology"}, make_services(taxi=ShortRefusingTaxi()))

    assert "40 characters" in tool_result(events)["hint"]


def test_the_tool_descriptions_quote_the_length_taxi_really_requires():
    class PickyTaxi(FakeTaxi):
        min_query_chars = 40

    built = tools.build_tools(make_services(taxi=PickyTaxi()))
    described = {tool.name: tool.args_schema.model_json_schema() for tool in built}

    assert "40 characters" in described["find_domain_experts"]["properties"]["topic"]["description"]
    assert "40 characters" in described["explore_domains"]["properties"]["topic"]["description"]
