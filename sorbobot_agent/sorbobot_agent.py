import asyncio
import logging
from collections.abc import AsyncGenerator, Generator
from typing import List

from langchain_core.messages import AIMessage, BaseMessage

from sorbobot_agent.input_safety import is_safe_input
from sorbobot_agent.intent_classifier import detect_language, last_human_message
from sorbobot_agent.lc_graph import SorboBotGraphFactory
from sorbobot_agent.logging_config import configure_logging

logger = logging.getLogger("sorbobot_agent.sorbobot_agent")

_REFUSAL = {
    "fr": "Désolé, je ne peux pas traiter cette demande.",
    "en": "Sorry, I can't process this request.",
}

_MAX_LOGGED_RESULT_CHARS = 500


def _truncate(text: str, max_chars: int = _MAX_LOGGED_RESULT_CHARS) -> str:
    return text if len(text) <= max_chars else text[:max_chars] + f"… [{len(text)} chars total]"


def _refusal_message(query: str) -> AIMessage:
    language = detect_language(query)
    return AIMessage(content=_REFUSAL[language])


class SorboBotAgent:

    def __init__(self):
        self._factory = SorboBotGraphFactory()
        configure_logging(self._factory.config.logging)

    def _unsafe_refusal(self, messages: List[BaseMessage]) -> AIMessage | None:
        """Return a refusal message if the last human message is unsafe, else None."""
        query = last_human_message(messages)
        max_length = self._factory.config.validation.max_input_length
        if query and not is_safe_input(query, max_length):
            return _refusal_message(query)
        return None

    async def ainvoke(self, messages: List[BaseMessage]) -> BaseMessage:
        logger.info("ainvoke: query=%r", last_human_message(messages))
        refusal = self._unsafe_refusal(messages)
        if refusal:
            logger.info("ainvoke: input rejected by input_safety — returning refusal")
            return refusal

        graph = await self._factory.abuild_graph()
        try:
            result = await graph.ainvoke({"messages": messages})
            return result["messages"][-1]
        finally:
            await self._factory.aclose()

    def invoke(self, messages: List[BaseMessage]) -> BaseMessage:
        return asyncio.run(self.ainvoke(messages))

    async def astream(
        self, messages: List[BaseMessage]
    ) -> AsyncGenerator[str | dict, None]:

        logger.info("astream: query=%r", last_human_message(messages))
        refusal = self._unsafe_refusal(messages)
        if refusal:
            logger.info("astream: input rejected by input_safety — returning refusal")
            yield refusal.content
            return

        graph = await self._factory.abuild_graph()

        pending_tool_calls: dict[str, dict] = {}

        try:
            async for mode, data in graph.astream(
                {"messages": messages},
                stream_mode=["messages", "updates"],
            ):
                if mode == "updates":
                    if "domain_experts" in data:
                        msg = data["domain_experts"]["messages"][-1]
                        if msg.content:
                            yield msg.content

                    elif "person_expertise" in data:
                        msg = data["person_expertise"]["messages"][-1]
                        if msg.content:
                            yield msg.content

                    elif "db_agent" in data:
                        msg = data["db_agent"]["messages"][-1]
                        for tc in getattr(msg, "tool_calls", []):
                            pending_tool_calls[tc["id"]] = {
                                "name": tc["name"],
                                "args": tc.get("args", {}),
                            }

                    elif "db_tools" in data:
                        for tool_msg in data["db_tools"]["messages"]:
                            pending = pending_tool_calls.pop(tool_msg.tool_call_id, {})
                            logger.info(
                                "db_agent tool call: %s(%s) -> %s",
                                pending.get("name", getattr(tool_msg, "name", "")),
                                pending.get("args", {}),
                                _truncate(str(tool_msg.content)),
                            )
                            yield {
                                "type": "tool_result",
                                "id": tool_msg.tool_call_id,
                                "name": pending.get(
                                    "name", getattr(tool_msg, "name", "")
                                ),
                                "args": pending.get("args", {}),
                                "result": tool_msg.content,
                            }

                elif mode == "messages":
                    chunk, metadata = data
                    if (
                        metadata.get("langgraph_node")
                        in ("general_question", "db_agent")
                        and chunk.content
                    ):
                        yield chunk.content
        finally:
            await self._factory.aclose()

    def stream(self, messages: List[BaseMessage]) -> Generator[str | dict, None, None]:

        loop = asyncio.new_event_loop()
        gen = self.astream(messages)
        try:
            while True:
                try:
                    yield loop.run_until_complete(gen.__anext__())
                except StopAsyncIteration:
                    break
        finally:
            loop.close()

    async def aclose(self):
        await self._factory.aclose()
