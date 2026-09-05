"""Provider quirk: recover tool calls that a model emitted as raw text.

Mistral models served via vLLM without --tool-call-parser (mistral-medium-250523) emit
tool calls as content prefixed with "[TOOL_CALLS]" instead of using the structured
tool-call protocol. Does not apply to Mistral 4+ — removal planned once that model is
retired from production. Opt in by piping the LLM through ``RunnableLambda(fix_raw_tool_calls)``.
"""

import json
import re
import uuid

from langchain_core.messages import AIMessage

RAW_TOOL_CALL_PREFIX = "[TOOL_CALLS]"


def parse_raw_tool_calls(content: str) -> list[dict] | None:
    if not content.startswith(RAW_TOOL_CALL_PREFIX):
        return None

    rest = content[len(RAW_TOOL_CALL_PREFIX):].strip()

    # Format: [{"name": "...", "arguments": {...}, "id": "..."}]
    if rest.startswith("["):
        try:
            calls = json.loads(rest)
            if isinstance(calls, list):
                return [
                    {
                        "name": c["name"],
                        "args": c.get("arguments", {}),
                        "id": c.get("id", str(uuid.uuid4())),
                        "type": "tool_call",
                    }
                    for c in calls
                ]
        except (json.JSONDecodeError, KeyError):
            pass

    # Format: tool-name{"key": "val"}
    m = re.match(r"^([\w-]+)(\{.*\})$", rest, re.DOTALL)
    if m:
        try:
            return [
                {
                    "name": m.group(1),
                    "args": json.loads(m.group(2)),
                    "id": str(uuid.uuid4()),
                    "type": "tool_call",
                }
            ]
        except json.JSONDecodeError:
            pass

    return None


def fix_raw_tool_calls(message: AIMessage) -> AIMessage:
    if message.tool_calls or not isinstance(message.content, str):
        return message

    tool_calls = parse_raw_tool_calls(message.content.strip())
    if tool_calls is None:
        return message

    return AIMessage(content="", tool_calls=tool_calls)
