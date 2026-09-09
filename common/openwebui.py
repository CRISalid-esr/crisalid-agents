"""OpenWebUI Pipelines adapter, generic over any registered agent.

A pipeline file in ``openwebui_pipelines/`` is a two-line stub::

    from common.openwebui import make_pipeline
    Pipeline = make_pipeline("my_agent")

This module lives in ``common/`` (not in ``openwebui_pipelines/``) because the Pipelines
server loads every ``*.py`` file of its pipelines directory as a pipeline.
"""

import html
import json
from collections.abc import Generator, Iterator

from common.agent import ToolResult
from common.messages import to_langchain_messages
from common.registry import registry

# Each <details> block is JSON-encoded twice (html.escape then json.dumps for the SSE line).
# Entities like &lt; expand further under json.dumps, so truncate early to stay well
# under OpenWebUI's 131 072-byte SSE line limit.
# https://github.com/NousResearch/hermes-agent/issues/18021
MAX_FIELD_CHARS = 600
MAX_TOOL_RESULT_CHARS = 20_000


def _truncate_fields(obj):
    if isinstance(obj, str):
        return obj[:MAX_FIELD_CHARS] + "…" if len(obj) > MAX_FIELD_CHARS else obj
    if isinstance(obj, dict):
        return {k: _truncate_fields(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_truncate_fields(item) for item in obj]
    return obj


def tool_result_block(item: ToolResult) -> str:
    result = item.result
    if not isinstance(result, str):
        result = json.dumps(result, ensure_ascii=False)
    try:
        result = json.dumps(_truncate_fields(json.loads(result)), ensure_ascii=False)
    except (json.JSONDecodeError, ValueError):
        pass
    if len(result) > MAX_TOOL_RESULT_CHARS:
        result = result[:MAX_TOOL_RESULT_CHARS] + f"\n… [truncated — {len(result)} chars total]"
    return (
        f'<details type="tool_calls" done="true" '
        f'id="{item.id}" name="{item.name}" '
        f'arguments="{html.escape(json.dumps(item.args))}">\n'
        f"<summary>Tool Executed</summary>\n"
        f"{html.escape(result)}\n"
        f"</details>\n"
    )


def _content_chunk(content: str) -> dict:
    return {"choices": [{"delta": {"content": content}, "finish_reason": None}]}


def _status_event(done: bool) -> dict:
    return {"event": {"type": "status", "data": {"description": "Thinking…", "done": done}}}


class AgentPipeline:
    agent_name: str = ""

    def __init__(self):
        self.agent = registry.get_agent(self.agent_name)
        self.name = self.agent.display_name

    async def on_shutdown(self):
        await self.agent.aclose()

    def pipe(
        self,
        user_message: str,
        model_id: str,
        messages: list[dict],
        body: dict,
    ) -> str | Generator | Iterator:
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
                    yield _status_event(done=True)
                    pending_status = False
                yield _content_chunk(item)

            elif isinstance(item, ToolResult):
                # Render the tool execution as an expandable <details> block, then
                # immediately re-open the spinner so the user knows processing continues
                # while the model digests the tool result.
                # "\n\n" prefix avoids https://github.com/open-webui/open-webui/issues/24634
                yield _content_chunk("\n\n" + tool_result_block(item))
                yield _status_event(done=False)
                pending_status = True

        yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}


def make_pipeline(agent_name: str) -> type[AgentPipeline]:
    return type("Pipeline", (AgentPipeline,), {"agent_name": agent_name})
