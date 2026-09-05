import html
import json

from common.agent import ToolResult
from common.openwebui import MAX_FIELD_CHARS, make_pipeline, tool_result_block

MESSAGES = [{"role": "user", "content": "How many words in: the quick brown fox?"}]


def test_pipe_streams_tool_block_status_and_tokens(registered_dummy_agent):
    Pipeline = make_pipeline("dummy_agent")
    pipeline = Pipeline()
    assert pipeline.name == "Dummy agent"

    chunks = list(pipeline.pipe(MESSAGES[0]["content"], "dummy_agent_pipeline", MESSAGES, {}))

    contents = [c["choices"][0]["delta"].get("content", "") for c in chunks if "choices" in c]
    statuses = [c["event"]["data"]["done"] for c in chunks if "event" in c]

    assert '<details type="tool_calls" done="true"' in contents[0]
    assert 'name="count_words"' in contents[0]
    assert statuses == [False, True]
    assert "".join(contents) .endswith("There are 4 words.")
    assert chunks[-1] == {"choices": [{"delta": {}, "finish_reason": "stop"}]}


def test_tool_result_block_truncates_long_fields():
    long_value = "x" * (MAX_FIELD_CHARS + 50)
    item = ToolResult(id="c1", name="search", args={"q": "a<b"}, result=json.dumps({"text": long_value}))

    block = tool_result_block(item)

    assert html.escape(json.dumps({"q": "a<b"})) in block
    assert long_value not in block
    assert ("x" * MAX_FIELD_CHARS + "…") in html.unescape(block)
