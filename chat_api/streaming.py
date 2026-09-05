"""Translate an agent event stream into MUI X Chat NDJSON message chunks.

One JSON-encoded chunk per line: start / text-start / text-delta / text-end /
tool-input-available / tool-output-available / finish. The frontend adapter pipes these
lines straight into the ChatBox runtime.
"""

import json
import uuid
from collections.abc import AsyncGenerator

from langchain_core.messages import BaseMessage

from common.agent import BaseAgent, ToolCall, ToolResult


def _line(chunk: dict) -> str:
    return json.dumps(chunk, ensure_ascii=False) + "\n"


def _tool_input(call_id: str, name: str, args: dict) -> str:
    return _line({"type": "tool-input-available", "toolCallId": call_id, "toolName": name, "input": args})


async def ndjson_chunks(agent: BaseAgent, messages: list[BaseMessage]) -> AsyncGenerator[str, None]:
    message_id = f"msg-{uuid.uuid4()}"
    text_id: str | None = None
    text_block_count = 0
    emitted_tool_inputs: set[str] = set()

    def close_text() -> str | None:
        nonlocal text_id
        if text_id is None:
            return None
        line = _line({"type": "text-end", "id": text_id})
        text_id = None
        return line

    yield _line({"type": "start", "messageId": message_id})
    try:
        async for item in agent.astream(messages):
            if isinstance(item, str):
                if text_id is None:
                    text_block_count += 1
                    text_id = f"{message_id}-text-{text_block_count}"
                    yield _line({"type": "text-start", "id": text_id})
                yield _line({"type": "text-delta", "id": text_id, "delta": item})

            elif isinstance(item, ToolCall):
                # The agent just decided to call a tool; the tool has not run yet.
                # Emit the input immediately so the UI shows a pending invocation
                # instead of dead silence while the tool executes.
                if (end := close_text()) is not None:
                    yield end
                yield _tool_input(item.id, item.name, item.args)
                emitted_tool_inputs.add(item.id)

            elif isinstance(item, ToolResult):
                # Tool activity interrupts any open text block: close it so the
                # tool invocation renders as its own message part.
                if (end := close_text()) is not None:
                    yield end
                if item.id not in emitted_tool_inputs:
                    # Fallback for a result whose tool_call event was never seen.
                    yield _tool_input(item.id, item.name, item.args)
                    emitted_tool_inputs.add(item.id)
                yield _line({"type": "tool-output-available", "toolCallId": item.id, "output": item.result})

        if (end := close_text()) is not None:
            yield end
        yield _line({"type": "finish", "messageId": message_id})

    except Exception as exc:  # noqa: BLE001 — surface any agent failure in the chat
        if (end := close_text()) is not None:
            yield end
        error_id = f"{message_id}-error"
        yield _line({"type": "text-start", "id": error_id})
        yield _line({"type": "text-delta", "id": error_id, "delta": f"⚠️ Agent error: {exc}"})
        yield _line({"type": "text-end", "id": error_id})
        yield _line({"type": "finish", "messageId": message_id, "finishReason": "error"})
