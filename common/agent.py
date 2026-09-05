"""Agent contract shared by every agent and every interface adapter.

An agent turns a list of LangChain messages into a stream of ``AgentEvent`` items:

- ``str``        one token of the final answer destined for the user
- ``ToolCall``   emitted as soon as the agent decides a tool call, before the tool runs,
                 so UIs can show a pending invocation
- ``ToolResult`` emitted once the tool has run, paired with the call by ``id``

Adapters (OpenWebUI pipelines, chat API) depend on this module only. Agents built with
LangGraph should subclass ``common.langgraph_agent.LangGraphAgent`` rather than this class.
"""

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field

from langchain_core.messages import AIMessage, BaseMessage


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict = field(default_factory=dict)


@dataclass
class ToolResult:
    id: str
    name: str
    args: dict
    result: str | dict | list


AgentEvent = str | ToolCall | ToolResult


class BaseAgent(ABC):
    def __init__(self, name: str, display_name: str, description: str = ""):
        # ``name`` is the registry key (agents/<name>/), the chat API path segment and
        # the OpenWebUI pipeline identifier. ``display_name`` is what users see.
        self.name = name
        self.display_name = display_name
        self.description = description

    @abstractmethod
    def astream(self, messages: list[BaseMessage]) -> AsyncIterator[AgentEvent]:
        ...

    async def ainvoke(self, messages: list[BaseMessage]) -> AIMessage:
        # Default: run the stream to completion and keep the text of the final answer.
        # Subclasses with a cheaper non-streaming path may override this.
        parts: list[str] = []
        async for event in self.astream(messages):
            if isinstance(event, str):
                parts.append(event)
        return AIMessage(content="".join(parts))

    def invoke(self, messages: list[BaseMessage]) -> AIMessage:
        return asyncio.run(self.ainvoke(messages))

    def stream(self, messages: list[BaseMessage]) -> Iterator[AgentEvent]:
        # Synchronous bridge over astream() for callers that cannot use async
        # (e.g. OpenWebUI pipe(), which runs in a threadpool thread with no event loop).
        loop = asyncio.new_event_loop()
        gen = self.astream(messages)
        try:
            while True:
                try:
                    yield loop.run_until_complete(gen.__anext__())
                except StopAsyncIteration:
                    break
        finally:
            loop.run_until_complete(gen.aclose())
            loop.close()

    async def aclose(self) -> None:
        # Release external resources (connections, clients). No-op by default.
        return None
