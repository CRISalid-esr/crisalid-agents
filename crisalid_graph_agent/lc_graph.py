import json
import re
import uuid
from pathlib import Path

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableLambda
from langgraph.graph import StateGraph
from langgraph.graph.message import MessagesState
from langgraph.prebuilt import ToolNode

from common.llm import build_chat_model
from crisalid_graph_agent.mcp_toolbox_client import MCPToolboxClient
from crisalid_graph_agent.schema_postprocessor import compact_schema

# Must match the tool name as registered by the MCP toolbox server.
# Check the printed tool list on startup if the name needs adjustment.
_SCHEMA_TOOL_NAME = "get-crisalid-schema"

_PROMPT_DIR = Path(__file__).resolve().parent

_TOOLSET_PROMPTS: dict[str, str] = {
    "crisalid-restricted": "mcp_toolbox_restricted_prompt.txt",
    "crisalid-unrestricted": "mcp_toolbox_unrestricted_prompt.txt",
}


def _parse_raw_tool_calls(content: str) -> list[dict] | None:
    if not content.startswith("[TOOL_CALLS]"):
        return None

    rest = content[len("[TOOL_CALLS]"):].strip()

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


def _fix_raw_tool_calls(message: AIMessage) -> AIMessage:
    if message.tool_calls or not isinstance(message.content, str):
        return message

    tool_calls = _parse_raw_tool_calls(message.content.strip())
    if tool_calls is None:
        return message

    return AIMessage(content="", tool_calls=tool_calls)


class MCPToolboxGraphFactory:
    def __init__(self):
        self.toolbox_client = MCPToolboxClient()

    async def abuild_graph(self):
        llm = build_chat_model()
        tools = await self.toolbox_client.aload_tools()
        toolset = self.toolbox_client.toolset_name
        prompt_file = _TOOLSET_PROMPTS.get(toolset, "mcp_toolbox_restricted_prompt.txt")
        system_prompt = (_PROMPT_DIR / prompt_file).read_text(encoding="utf-8")

        llm_with_tools = (
            llm.bind_tools(tools).with_retry(
                retry_if_exception_type=(ValueError,),
                stop_after_attempt=3,
            )
            | RunnableLambda(_fix_raw_tool_calls)
        )
        tool_node = ToolNode(tools)

        async def call_model(state: MessagesState):
            messages = [SystemMessage(content=system_prompt)] + state["messages"]
            try:
                return {"messages": [await llm_with_tools.ainvoke(messages)]}
            except ValueError:
                # The model returned an empty stream (e.g. transient API error).
                # Return a plain AIMessage so the graph exits cleanly instead of crashing.
                return {"messages": [AIMessage(content="The model returned an empty response. Please try again.")]}

        def should_continue(state: MessagesState):
            if state["messages"][-1].tool_calls:
                return "tools"
            return "__end__"

        async def postprocess_schema(state: MessagesState):
            updated = []
            for msg in state["messages"]:
                if isinstance(msg, ToolMessage) and getattr(msg, "name", None) == _SCHEMA_TOOL_NAME:
                    try:
                        compact = compact_schema(msg.content)
                        updated.append(
                            ToolMessage(content=compact, tool_call_id=msg.tool_call_id,
                                        name=msg.name, id=msg.id)
                        )
                    except Exception:
                        pass
            return {"messages": updated} if updated else {}

        graph = StateGraph(MessagesState)
        graph.add_node("agent", call_model)
        graph.add_node("tools", tool_node)
        graph.add_node("postprocess_schema", postprocess_schema)
        graph.set_entry_point("agent")
        graph.add_conditional_edges("agent", should_continue)
        graph.add_edge("tools", "postprocess_schema")
        graph.add_edge("postprocess_schema", "agent")

        return graph.compile()

    async def aclose(self):
        await self.toolbox_client.aclose()