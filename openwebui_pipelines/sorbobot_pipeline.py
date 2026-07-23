from collections.abc import Generator
from typing import Iterator, Union

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from sorbobot_agent.sorbobot_agent import SorboBotAgent


def to_langchain_messages(messages: list[dict]) -> list[BaseMessage]:
    result: list[BaseMessage] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content", "")
        if role == "system":
            result.append(SystemMessage(content=content))
        elif role == "user":
            result.append(HumanMessage(content=content))
        elif role == "assistant":
            result.append(AIMessage(content=content))
    return result


class Pipeline:
    def __init__(self):
        self.name = "SorboBot"
        self.agent = SorboBotAgent()

    async def on_shutdown(self):
        await self.agent.aclose()

    def pipe(
        self,
        user_message: str,
        model_id: str,
        messages: list[dict],
        body: dict,
    ) -> Union[str, Generator, Iterator]:
        langchain_messages = to_langchain_messages(messages)

        pending_status = False

        for item in self.agent.stream(langchain_messages):
            if isinstance(item, str):
                if pending_status:
                    yield {"event": {"type": "status",
                                     "data": {"description": "Thinking…", "done": True}}}
                    pending_status = False
                yield {"choices": [{"delta": {"content": item}, "finish_reason": None}]}

            elif isinstance(item, dict) and item.get("type") == "tool_result":
                # Internal db_agent tool calls (Cypher, raw results) are
                # intentionally not surfaced in the chat — only a transient
                # "thinking" status, never the tool name/args/result content.
                yield {"event": {"type": "status",
                                 "data": {"description": "Thinking…", "done": False}}}
                pending_status = True

        yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}
