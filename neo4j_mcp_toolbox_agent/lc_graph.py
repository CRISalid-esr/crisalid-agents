import json
import re
import uuid
from pathlib import Path

from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.runnables import RunnableLambda
from langgraph.graph import StateGraph
from langgraph.graph.message import MessagesState
from langgraph.prebuilt import ToolNode

from common.llm import build_chat_model
from neo4j_mcp_toolbox_agent.mcp_toolbox_client import MCPToolboxClient

PROMPT_PATH = Path(__file__).resolve().parent / "mcp_toolbox_prompt.txt"


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
        system_prompt = PROMPT_PATH.read_text(encoding="utf-8")

        llm_with_tools = llm.bind_tools(tools) | RunnableLambda(_fix_raw_tool_calls)
        tool_node = ToolNode(tools)

        async def call_model(state: MessagesState):
            messages = [SystemMessage(content=system_prompt)] + state["messages"]
            return {"messages": [await llm_with_tools.ainvoke(messages)]}

        def should_continue(state: MessagesState):
            if state["messages"][-1].tool_calls:
                return "tools"
            return "__end__"

        graph = StateGraph(MessagesState)
        graph.add_node("agent", call_model)
        graph.add_node("tools", tool_node)
        graph.set_entry_point("agent")
        graph.add_conditional_edges("agent", should_continue)
        graph.add_edge("tools", "agent")

        return graph.compile()

    async def aclose(self):
        await self.toolbox_client.aclose()