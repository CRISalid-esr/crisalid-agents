import json

import pytest

from agents.sorbobot.domains import DomainExploration
from agents.sorbobot.experts import ExpertSearch
from agents.sorbobot.i18n import LOCALES_DIR, Translator
from agents.sorbobot.researchers import ResearcherSearch
from agents.sorbobot.toolbox import GraphUnavailableError
from agents.sorbobot.tools import TECHNICAL_ERRORS, build_tools, read_result
from agents.sorbobot.topics import TopicFinder
from tests.sorbobot_fakes import FakeTaxi, FakeToolbox, evidence, internal, make_settings

LONG_TOPIC = "machine learning for medical imaging"


def tools_with(taxi=None, toolbox=None, **settings) -> dict:
    values = make_settings(judge_threshold=0, **settings)
    taxi = taxi or FakeTaxi(["t1"])
    toolbox = toolbox or FakeToolbox(topics={"t1"}, evidence=[evidence("a", "d1", "t1")], persons=[internal("p1", "Joe")])
    translator = Translator.load(LOCALES_DIR, values.default_language, values.supported_languages)
    finder = TopicFinder(taxi, toolbox, None, values)
    built = build_tools(ExpertSearch(finder, toolbox, values), ResearcherSearch(toolbox, None, values),
                        DomainExploration(finder, toolbox, values), translator, values)
    return {tool.name: tool for tool in built}


async def call(tools, name, **args) -> dict:
    return json.loads(await tools[name].ainvoke(args))


def test_tool_names_and_descriptions():
    tools = tools_with()
    assert set(tools) == {"find_domain_experts", "get_person_expertise", "get_researcher_profile", "explore_domains"}
    assert "language" in tools["find_domain_experts"].args and "domain_names" in tools["explore_domains"].args
    assert "25 characters" in tools["find_domain_experts"].description


async def test_a_short_topic_is_a_hint_and_never_reaches_taxi():
    taxi = FakeTaxi(["t1"])
    tools = tools_with(taxi)
    result = await call(tools, "find_domain_experts", topic="NLP", language="fr")
    assert result == {"topic": "NLP", "hint": "topic_too_short", "min_chars": 25, "language": "fr"}
    assert await call(tools, "explore_domains", topic="short", language="en") == {
        "topic": "short", "hint": "topic_too_short", "min_chars": 25, "language": "en"}
    assert (await call(tools, "explore_domains", language="en"))["hint"] == "topic_too_short"
    assert taxi.queries == []


async def test_taxi_and_toolbox_failures_are_technical_errors():
    tools = tools_with(FakeTaxi(unavailable=True))
    result = await call(tools, "find_domain_experts", topic=LONG_TOPIC, language="fr")
    assert result == {"error": "taxi_unavailable", "language": "fr"}
    broken = FakeToolbox(fail=GraphUnavailableError("down"))
    tools = tools_with(FakeTaxi(["t1"]), broken)
    assert await call(tools, "find_domain_experts", topic=LONG_TOPIC, language="en") == {"error": "graph_unavailable", "language": "en"}
    assert await call(tools, "get_person_expertise", person_name="Joe", language="en") == {"error": "graph_unavailable", "language": "en"}
    assert await call(tools, "get_researcher_profile", person_name="Joe", question="q", language="en") == {"error": "graph_unavailable", "language": "en"}
    assert await call(tools, "explore_domains", domain_names=["X"], language="en") == {"error": "graph_unavailable", "language": "en"}
    assert set(TECHNICAL_ERRORS) == {"taxi_unavailable", "graph_unavailable"}


async def test_functional_hints_of_each_tool():
    tools = tools_with(FakeTaxi(["t1"]), FakeToolbox(topics={"t1"}, counts={"t1": 0}))
    assert (await call(tools, "find_domain_experts", topic=LONG_TOPIC, language="en"))["hint"] == "no_document"
    tools = tools_with(FakeTaxi(["t1"]), FakeToolbox(topics={"t1"}))
    assert (await call(tools, "find_domain_experts", topic=LONG_TOPIC, language="en"))["hint"] == "no_expert"
    assert (await call(tools, "get_person_expertise", person_name="Nobody", language="en"))["hint"] == "person_not_found"
    assert (await call(tools, "get_researcher_profile", person_name="Nobody", question="q", language="en"))["hint"] == "person_not_found"
    assert (await call(tools, "explore_domains", domain_names=["Nope"], language="en"))["hint"] == "no_domain"


async def test_results_carry_the_validated_language():
    tools = tools_with()
    assert (await call(tools, "find_domain_experts", topic=LONG_TOPIC, language="fr-FR"))["language"] == "fr"
    assert (await call(tools, "find_domain_experts", topic=LONG_TOPIC, language="de"))["language"] == "en"
    assert (await call(tools, "find_domain_experts", topic=LONG_TOPIC))["language"] == "en"
    result = await call(tools, "get_person_expertise", person_name="Joe", language="FR")
    assert result["language"] == "fr" and result["name"] == "Joe"


def test_read_result():
    assert read_result('{"a": 1}') == {"a": 1}
    assert read_result([{"type": "text", "text": '{"a": '}, {"type": "text", "text": "2}"}]) == {"a": 2}
    assert read_result("[1, 2]") is None and read_result("not json") is None and read_result(None) is None
