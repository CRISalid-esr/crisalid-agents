import html
import json
from collections.abc import Generator
from typing import Iterator, Union

_MAX_FIELD_CHARS = 600
_MAX_TOOL_RESULT_CHARS = 20_000

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


def _truncate_fields(obj):
    if isinstance(obj, str):
        return obj[:_MAX_FIELD_CHARS] + "…" if len(obj) > _MAX_FIELD_CHARS else obj
    if isinstance(obj, dict):
        return {k: _truncate_fields(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_truncate_fields(item) for item in obj]
    return obj


def _tool_result_block(item: dict) -> str:
    result = item["result"]
    if not isinstance(result, str):
        result = json.dumps(result, ensure_ascii=False)
    try:
        result = json.dumps(_truncate_fields(json.loads(result)), ensure_ascii=False)
    except (json.JSONDecodeError, ValueError):
        pass
    if len(result) > _MAX_TOOL_RESULT_CHARS:
        result = result[:_MAX_TOOL_RESULT_CHARS] + f"\n… [truncated — {len(result)} chars total]"
    return (
        f'<details type="tool_calls" done="true" '
        f'id="{item["id"]}" name="{item["name"]}" '
        f'arguments="{html.escape(json.dumps(item["args"]))}">\n'
        f"<summary>Tool Executed</summary>\n"
        f"{html.escape(result)}\n"
        f"</details>\n"
    )


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
                yield {"choices": [{"delta": {"content": "\n\n" + _tool_result_block(item)},
                                    "finish_reason": None}]}
                yield {"event": {"type": "status",
                                 "data": {"description": "Thinking…", "done": False}}}
                pending_status = True

        yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}
