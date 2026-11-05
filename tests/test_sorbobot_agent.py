import asyncio
import json
import threading

import httpx
import openai
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGenerationChunk

from agents.sorbobot.agent import create_agent
from agents.sorbobot.config import ConfigurationError
from agents.sorbobot.toolbox import ToolboxConnection
from common.agent import ToolCall, ToolResult
from tests.fake_llm import ScriptedChatModel, tool_call_message
from tests.sorbobot_fakes import FakeTaxi, FakeToolbox, evidence, internal, make_settings, mcp_toolset

TOPIC = "machine learning for medical imaging"


def fakes():
    taxi = FakeTaxi(["t1"])
    toolbox = FakeToolbox(topics={"t1"}, evidence=[evidence("alice", "d1", "t1"), evidence("bob", "d2", "t1")],
                          persons=[internal("p1", "Alice Martin")])
    return taxi, toolbox


def agent_with(responses, **settings):
    llm = ScriptedChatModel(responses=responses, calls=[])
    taxi, toolbox = fakes()
    return create_agent(llm=llm, settings=make_settings(judge_threshold=0, **settings), taxi=taxi, toolbox=toolbox), llm


def test_tool_call_is_announced_before_its_result_then_the_answer_streams():
    agent, llm = agent_with([
        tool_call_message("find_domain_experts", {"topic": TOPIC, "language": "fr"}),
        AIMessage(content="Nous avons trouvé des experts."),
    ])
    events = list(agent.stream([HumanMessage(content="Qui travaille sur l'imagerie médicale ?")]))

    assert agent.name == "sorbobot" and agent.display_name == "Sorbobot"
    assert isinstance(events[0], ToolCall) and events[0].name == "find_domain_experts"
    assert isinstance(events[1], ToolResult) and events[1].id == events[0].id
    result = json.loads(events[1].result)
    assert [e["name"] for e in result["experts"]] == ["Alice", "Bob"] and result["language"] == "fr"
    assert "".join(e for e in events[2:]) == "Nous avons trouvé des experts."
    assert len(llm.calls) == 2 and llm.calls[0][0].content.startswith("You are Sorbobot")


def test_empty_and_too_long_messages_are_answered_without_the_llm():
    agent, llm = agent_with([], max_message_chars=20)
    empty = "".join(e for e in agent.stream([HumanMessage(content="   ")]) if isinstance(e, str))
    assert empty.startswith("Your message is empty")
    long = "".join(e for e in agent.stream([HumanMessage(content="x" * 21)]) if isinstance(e, str))
    assert long.startswith("Your message is too long (more than 20 characters)")
    assert llm.calls == []


def test_a_technical_error_is_displayed_in_the_turn_language_without_a_new_llm_call():
    llm = ScriptedChatModel(responses=[tool_call_message("find_domain_experts", {"topic": TOPIC, "language": "fr"})], calls=[])
    agent = create_agent(llm=llm, settings=make_settings(judge_threshold=0), taxi=FakeTaxi(unavailable=True), toolbox=FakeToolbox())
    events = list(agent.stream([HumanMessage(content="Qui travaille sur l'imagerie médicale ?")]))
    assert json.loads([e for e in events if isinstance(e, ToolResult)][0].result) == {"error": "taxi_unavailable", "language": "fr"}
    assert "".join(e for e in events if isinstance(e, str)).startswith("Le service d'appariement des thématiques est indisponible")
    assert len(llm.calls) == 1


class UnavailableModel(ScriptedChatModel):
    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls.append(list(messages))
        raise openai.APIConnectionError(request=httpx.Request("POST", "http://llm.test/v1/chat/completions"))
        yield  # pragma: no cover


class EmptyModel(ScriptedChatModel):
    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls.append(list(messages))
        raise ValueError("empty stream")
        yield  # pragma: no cover


def test_llm_failures_are_translated_messages():
    taxi, toolbox = fakes()
    down = UnavailableModel(responses=[], calls=[])
    agent = create_agent(llm=down, settings=make_settings(judge_threshold=0), taxi=taxi, toolbox=toolbox)
    text = "".join(e for e in agent.stream([HumanMessage(content="Who works on imaging?")]) if isinstance(e, str))
    assert text == "The language model is currently unavailable. Please try again later." and len(down.calls) == 1

    empty = EmptyModel(responses=[], calls=[])
    agent = create_agent(llm=empty, settings=make_settings(judge_threshold=0), taxi=taxi, toolbox=toolbox)
    text = "".join(e for e in agent.stream([HumanMessage(content="Who works on imaging?")]) if isinstance(e, str))
    assert text == "The language model returned no answer. Please try again." and len(empty.calls) == 3


def test_llm_failure_after_a_tool_uses_the_language_of_the_turn():
    class FailsAfterTool(ScriptedChatModel):
        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
            if len(self.calls) >= 1:
                self.calls.append(list(messages))
                raise openai.APIConnectionError(request=httpx.Request("POST", "http://llm.test"))
            async for chunk in super()._astream(messages, stop, run_manager, **kwargs):
                yield chunk

    llm = FailsAfterTool(responses=[tool_call_message("get_person_expertise", {"person_name": "Alice", "language": "fr"})], calls=[])
    taxi, toolbox = fakes()
    agent = create_agent(llm=llm, settings=make_settings(judge_threshold=0), taxi=taxi, toolbox=toolbox)
    text = "".join(e for e in agent.stream([HumanMessage(content="Que fait Alice ?")]) if isinstance(e, str))
    assert text == "Le modèle de langue est indisponible pour le moment. Merci de réessayer plus tard."


def test_two_first_messages_on_two_event_loops_share_one_runtime():
    class FakeMCPClient:
        instances = []

        def __init__(self):
            self.closed = False
            FakeMCPClient.instances.append(self)

        async def aload_tools(self):
            return mcp_toolset(search_person_by_name=[{"uid": "p1", "display_name": "Alice", "external": False, "score": 1}],
                               list_person_research_concepts=[{"domain_uid": "t1", "domain_name": "T1", "nb_publications": 2}])

        async def aclose(self):
            self.closed = True

    class RuleBasedModel(ScriptedChatModel):
        """Asks for Alice's expertise, then answers with the text of the user's message."""

        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
            self.calls.append(list(messages))
            if isinstance(messages[-1], ToolMessage):
                yield ChatGenerationChunk(message=AIMessageChunk(content=f"Answer to {messages[1].content}"))
                return
            chunk = {"name": "get_person_expertise", "args": json.dumps({"person_name": "Alice", "language": "en"}),
                     "id": f"call-{len(self.calls)}", "index": 0}
            yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=[chunk]))

    connection = ToolboxConnection(None, "sorbobot", 1.0, factory=FakeMCPClient)
    llm = RuleBasedModel(responses=[], calls=[])
    agent = create_agent(llm=llm, settings=make_settings(judge_threshold=0), taxi=FakeTaxi(), toolbox=connection)
    collected, errors = {}, []

    def run(key):
        try:
            collected[key] = list(agent.stream([HumanMessage(content=key)]))
        except Exception as exc:  # noqa: BLE001 — reported by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(key,)) for key in ("one", "two")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    answers = sorted("".join(e for e in events if isinstance(e, str)) for events in collected.values())
    assert answers == ["Answer to one", "Answer to two"]
    results = [json.loads(e.result) for events in collected.values() for e in events if isinstance(e, ToolResult)]
    assert all(r["name"] == "Alice" and r["domains"][0]["uid"] == "t1" for r in results)
    assert len(FakeMCPClient.instances) == 2 and all(client.closed for client in FakeMCPClient.instances)
    assert agent._runtime is not None and agent.get_graph is not None


def test_configuration_errors_fail_the_first_message(monkeypatch):
    monkeypatch.delenv("CRISALID_TAXI_URL", raising=False)
    agent = create_agent(llm=ScriptedChatModel(responses=[], calls=[]))
    try:
        asyncio.run(agent.get_graph())
    except ConfigurationError as exc:
        assert "CRISALID_TAXI_URL" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("a missing CRISALID_TAXI_URL must fail the initialisation")


def test_ainvoke_and_aclose():
    agent, _ = agent_with([AIMessage(content="I can find experts, describe a researcher's expertise, or explain domains.")])
    answer = asyncio.run(agent.ainvoke([HumanMessage(content="hello")]))
    assert answer.content.startswith("I can find experts")
    asyncio.run(agent.aclose())
