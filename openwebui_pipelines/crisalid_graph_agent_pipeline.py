import html
import json
from collections.abc import Generator
from typing import Iterator, Union

# Each <details> block is JSON-encoded twice (html.escape then json.dumps for the SSE line).
# Entities like &lt; expand further under json.dumps, so truncate early to stay well
# under OpenWebUI's 131 072-byte SSE line limit.
# https://github.com/NousResearch/hermes-agent/issues/18021
_MAX_FIELD_CHARS = 600
_MAX_TOOL_RESULT_CHARS = 20_000

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from crisalid_graph_agent.crisalid_graph_agent import CrisalidGraphAgent


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
        self.name = "CRISalid Graph agent"
        self.agent = CrisalidGraphAgent()

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

        # After a tool result the model is still running (processing the result and
        # generating its next response), but OpenWebUI stops the waiting animation as
        # soon as any content is yielded. pending_status tracks whether we need to
        # re-open the spinner before the next content token.
        pending_status = False

        for item in self.agent.stream(langchain_messages):
            if isinstance(item, str):
                # First token of the final answer: close the spinner that was opened
                # after the last tool result, then stream the token normally.
                if pending_status:
                    yield {"event": {"type": "status",
                                     "data": {"description": "Thinking…", "done": True}}}
                    pending_status = False
                yield {"choices": [{"delta": {"content":item}, "finish_reason": None}]}

            elif isinstance(item, dict) and item.get("type") == "tool_result":
                # Render the tool execution as an expandable <details> block, then
                # immediately re-open the spinner so the user knows processing continues
                # while the model digests the tool result.
                # add  "\n\n" to avoid https://github.com/open-webui/open-webui/issues/24634
                yield {"choices": [{"delta": {"content":  "\n\n" + _tool_result_block(item)},
                                    "finish_reason": None}]}
                #yield  "\n\n" + _tool_result_block(item)
                yield {"event": {"type": "status",
                                 "data": {"description": "Thinking…", "done": False}}}
                pending_status = True

        yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}
