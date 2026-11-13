"""Rendering of tool executions inside an assistant message, for OpenAI-compatible chat clients.

OpenWebUI shows a ``<details type="tool_calls">`` block as a collapsible "Tool Executed" panel. The block is
embedded in the streamed content, so the result is truncated to keep every SSE line well under OpenWebUI's
131 072-byte limit (entities such as ``&lt;`` expand further when the line is JSON-encoded).
https://github.com/NousResearch/hermes-agent/issues/18021
"""

import html
import json

from common.agent import ToolResult

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


def status_event(done: bool, description: str = "Thinking…") -> dict:
    """OpenWebUI status event (spinner), accepted inside an OpenAI-compatible stream."""
    return {"event": {"type": "status", "data": {"description": description, "done": done}}}
