"""Sorbobot: the research expertise of an institution's researchers, read from the CRISalid knowledge graph.

The graph is the ReAct loop of the framework (``agent -> tools -> agent``), but the LLM only sees the four business
tools of ``tools.py``. Each tool runs a deterministic search in Python over crisalid-taxi and the ``sorbobot`` MCP
toolset and answers JSON; the LLM detects the language, picks the tool and writes the answer. Technical errors and
the input control are handled by the ``agent`` node without any LLM call (§3 and §4 of the spec).
"""

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import openai
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableLambda
from langgraph.graph import END, StateGraph
from langgraph.graph.message import MessagesState
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode

from agents.sorbobot.config import ConfigurationError, SorbobotSettings
from agents.sorbobot.domains import DomainExploration
from agents.sorbobot.experts import ExpertSearch
from agents.sorbobot.i18n import LOCALES_DIR, Translator
from agents.sorbobot.judges import PublicationJudge, TopicJudge
from agents.sorbobot.researchers import ResearcherSearch
from agents.sorbobot.taxi import TaxiClient
from agents.sorbobot.toolbox import GraphUnavailableError, ToolboxConnection
from agents.sorbobot.tools import TECHNICAL_ERRORS, build_tools, read_result
from agents.sorbobot.topics import TopicFinder
from common.agent import AgentEvent, BaseAgent
from common.langgraph_agent import LangGraphAgent
from common.llm import build_chat_model
from common.tool_calls import fix_raw_tool_calls

logger = logging.getLogger(__name__)

PACKAGE_DIR = Path(__file__).resolve().parent


def _read_prompt(name: str) -> str:
    path = PACKAGE_DIR / name
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ConfigurationError(f"Missing or unreadable prompt file {path}") from exc
    if not text:
        raise ConfigurationError(f"Empty prompt file {path}")
    return text


def message_text(message: BaseMessage | None) -> str:
    if message is None:
        return ""
    content = message.content
    if isinstance(content, str):
        return content
    return "".join(part if isinstance(part, str) else str(part.get("text", "")) for part in content)


def trailing_tool_results(messages: list[BaseMessage]) -> list[dict]:
    """The parsed results of the tool messages that end the state (the tools of the last step)."""
    results = []
    for message in reversed(messages):
        if not isinstance(message, ToolMessage):
            break
        data = read_result(message.content)
        if data is not None:
            results.append(data)
    return list(reversed(results))


def turn_language(messages: list[BaseMessage]) -> str | None:
    """The language the tools of the current turn reported, if any tool ran since the last user message."""
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return None
        if isinstance(message, ToolMessage):
            data = read_result(message.content)
            if data and isinstance(data.get("language"), str):
                return data["language"]
    return None


@dataclass(frozen=True)
class _Runtime:
    """What the agent builds on the first message. Internal to the agent: the components never see it."""

    settings: SorbobotSettings
    translator: Translator
    toolbox: object
    tools: list
    system_prompt: str
    llm_with_tools: object


class SorbobotAgent(LangGraphAgent):
    def __init__(self, llm: BaseChatModel | None = None, settings: SorbobotSettings | None = None, taxi=None,
                 toolbox=None):
        super().__init__(
            name="sorbobot",
            display_name="Sorbobot",
            description="Finds the experts of a research topic in the CRISalid knowledge graph, describes and "
                        "summarises a researcher's expertise, and explains research domains.",
        )
        # Injected by the offline tests; built from the environment on the first message otherwise.
        self._llm = llm
        self._settings = settings
        self._taxi = taxi
        self._toolbox = toolbox
        self._runtime: _Runtime | None = None

    def _build_runtime(self) -> _Runtime:
        # Configuration, catalogues and prompts first: a deployment error fails here, before any service is used.
        settings = self._settings or SorbobotSettings.from_env()
        translator = Translator.load(LOCALES_DIR, settings.default_language, settings.supported_languages)
        system_prompt = _read_prompt("system_prompt.md")
        topic_prompt = _read_prompt("topic_judge_prompt.md")
        publication_prompt = _read_prompt("publication_judge_prompt.md")
        llm = self._llm or build_chat_model()

        taxi = self._taxi or TaxiClient(
            settings.taxi_url, settings.semantic_threshold, settings.taxi_max_topics, settings.taxi_min_query_chars,
            settings.taxi_timeout_seconds,
        )
        toolbox = self._toolbox or ToolboxConnection(settings.toolbox_url, settings.toolset, settings.toolbox_timeout_seconds,
                                                      topic_sources=settings.topic_sources)
        topic_judge = TopicJudge(llm, topic_prompt, settings.judge_timeout_seconds) if settings.judges_enabled else None
        publication_judge = (
            PublicationJudge(llm, publication_prompt, settings.judge_timeout_seconds) if settings.judges_enabled else None
        )
        finder = TopicFinder(taxi, toolbox, topic_judge, settings)
        tools = build_tools(
            ExpertSearch(finder, toolbox, settings),
            ResearcherSearch(toolbox, publication_judge, settings),
            DomainExploration(finder, toolbox, settings),
            translator,
            settings,
        )
        llm_with_tools = llm.bind_tools(tools).with_retry(
            retry_if_exception_type=(ValueError,), stop_after_attempt=3
        ) | RunnableLambda(fix_raw_tool_calls)
        return _Runtime(settings, translator, toolbox, tools, system_prompt, llm_with_tools)

    def _get_runtime(self) -> _Runtime:
        if self._runtime is None:
            built = self._build_runtime()
            # Two first messages may build at once (two event loops): the first result built is kept.
            if self._runtime is None:
                self._runtime = built
        return self._runtime

    async def get_graph(self) -> CompiledStateGraph:
        if self._graph is None:
            built = await self.build_graph()
            if self._graph is None:
                self._graph = built
        return self._graph

    async def build_graph(self) -> CompiledStateGraph:
        runtime = self._get_runtime()
        settings, translator = runtime.settings, runtime.translator

        def reply(text: str) -> dict:
            return {"messages": [AIMessage(content=text)]}

        async def call_model(state: MessagesState):
            messages = state["messages"]
            last = messages[-1] if messages else None
            if isinstance(last, ToolMessage):
                # After the tools: a technical error is displayed translated, without calling the LLM.
                results = trailing_tool_results(messages)
                language = next((r["language"] for r in results if isinstance(r.get("language"), str)), None)
                error = next((r["error"] for r in results if r.get("error") in TECHNICAL_ERRORS), None)
                if error:
                    return reply(translator.text(language or settings.default_language, TECHNICAL_ERRORS[error]))
            elif isinstance(last, HumanMessage):
                # Step 1: the input control, before any LLM call.
                text = message_text(last)
                if not text.strip():
                    return reply(translator.text(settings.default_language, "input.empty"))
                if len(text) > settings.max_message_chars:
                    return reply(translator.text(settings.default_language, "input.too_long",
                                                 max_chars=settings.max_message_chars))
            try:
                answer = await runtime.llm_with_tools.ainvoke([SystemMessage(content=runtime.system_prompt), *messages])
            except ValueError:
                logger.warning("The language model returned an empty answer three times")
                language = turn_language(messages) or settings.default_language
                return reply(translator.text(language, "errors.llm_empty_response"))
            except openai.APIError as exc:
                logger.warning("The language model is unavailable: %s", exc)
                language = turn_language(messages) or settings.default_language
                return reply(translator.text(language, "errors.llm_unavailable"))
            return {"messages": [answer]}

        def should_continue(state: MessagesState):
            if state["messages"][-1].tool_calls:
                return self.tools_node
            return END

        graph = StateGraph(MessagesState)
        graph.add_node(self.agent_node, call_model)
        graph.add_node(self.tools_node, ToolNode(runtime.tools))
        graph.set_entry_point(self.agent_node)
        graph.add_conditional_edges(self.agent_node, should_continue)
        graph.add_edge(self.tools_node, self.agent_node)
        return graph.compile()

    async def astream(self, messages: list[BaseMessage]) -> AsyncIterator[AgentEvent]:
        await self.get_graph()
        toolbox = self._runtime.toolbox
        if hasattr(toolbox, "acquire"):
            try:
                await toolbox.acquire()
            except GraphUnavailableError:
                # The tools report it to the user, translated, if the turn needs the graph.
                logger.warning("MCP toolbox unreachable at the start of the turn")
        try:
            async for event in super().astream(messages):
                yield event
        finally:
            if hasattr(toolbox, "release"):
                await toolbox.release()

    async def ainvoke(self, messages: list[BaseMessage]) -> AIMessage:
        return await BaseAgent.ainvoke(self, messages)

    async def aclose(self) -> None:
        if self._runtime is not None and hasattr(self._runtime.toolbox, "aclose"):
            await self._runtime.toolbox.aclose()


def create_agent(llm: BaseChatModel | None = None, settings: SorbobotSettings | None = None, taxi=None,
                 toolbox=None) -> SorbobotAgent:
    return SorbobotAgent(llm=llm, settings=settings, taxi=taxi, toolbox=toolbox)
