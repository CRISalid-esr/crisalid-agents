"""A scripted chat model for offline tests.

Each call returns the next AIMessage of ``responses``. Text answers are streamed one
word at a time; tool-call answers are streamed as a single tool-call chunk, which is how
LangGraph's "messages" stream mode sees a real provider.
"""

import json
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult


def tool_call_message(name: str, args: dict, call_id: str = "call-1") -> AIMessage:
    return AIMessage(content="", tool_calls=[{"id": call_id, "name": name, "args": args, "type": "tool_call"}])


class ScriptedChatModel(BaseChatModel):
    responses: list[AIMessage]
    calls: list[list[BaseMessage]] = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedChatModel":
        return self

    def _next(self, messages: list[BaseMessage]) -> AIMessage:
        self.calls.append(list(messages))
        index = len(self.calls) - 1
        if index >= len(self.responses):
            raise AssertionError(f"ScriptedChatModel: no scripted response for call #{index + 1}")
        return self.responses[index]

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._next(messages))])

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        message = self._next(messages)
        if message.tool_calls:
            chunks = [
                {"name": tc["name"], "args": json.dumps(tc["args"]), "id": tc["id"], "index": i}
                for i, tc in enumerate(message.tool_calls)
            ]
            yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=chunks))
            return
        words = str(message.content).split(" ")
        for i, word in enumerate(words):
            token = word if i == len(words) - 1 else word + " "
            yield ChatGenerationChunk(message=AIMessageChunk(content=token))
