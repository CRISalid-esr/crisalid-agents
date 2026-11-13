import html
import json

from common.agent import ToolResult
from common.tool_rendering import MAX_FIELD_CHARS, status_event, tool_result_block


def test_tool_result_block_truncates_long_fields():
    long_value = "x" * (MAX_FIELD_CHARS + 50)
    item = ToolResult(id="c1", name="search", args={"q": "a<b"}, result=json.dumps({"text": long_value}))

    block = tool_result_block(item)

    assert block.startswith('<details type="tool_calls" done="true" id="c1" name="search"')
    assert html.escape(json.dumps({"q": "a<b"})) in block
    assert long_value not in block
    assert ("x" * MAX_FIELD_CHARS + "…") in html.unescape(block)


def test_status_event():
    assert status_event(done=False) == {"event": {"type": "status", "data": {"description": "Thinking…", "done": False}}}
    assert status_event(done=True, description="x")["event"]["data"] == {"description": "x", "done": True}
