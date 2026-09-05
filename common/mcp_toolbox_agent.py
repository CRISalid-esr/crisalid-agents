"""ReAct agent over the tools of an MCP Toolbox toolset.

Subclass it (or instantiate it directly) with a system prompt and a toolset; override
``postprocess_tool_message()`` to rewrite specific tool outputs before the LLM sees them.
"""

import json
import re
import uuid

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableLambda
from langgraph.graph import END, StateGraph
from langgraph.graph.message import MessagesState
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode

from common.embedding import EmbeddingServiceError, get_embedding_provider
from common.langgraph_agent import LangGraphAgent
from common.llm import build_chat_model
from common.mcp_toolbox_client import MCPToolboxClient


# Workaround for mistral-medium-250523 served via vLLM without --tool-call-parser:
# the model emits tool calls as raw text prefixed with [TOOL_CALLS] instead of using
# the structured tool-call protocol. Does not apply to Mistral 4+ — removal planned
# once mistral-medium-250523 is retired from production.
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


def _strip_vector_args(messages):
    result = []
    for msg in messages:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            cleaned = [
                {**tc, "args": {k: v for k, v in tc["args"].items() if not k.endswith("_vector")}}
                for tc in msg.tool_calls
            ]
            result.append(AIMessage(content=msg.content, tool_calls=cleaned))
        else:
            result.append(msg)
    return result


# Convention of the CRISalid toolbox: a semantic_xxx parameter carries a natural-language
# string supplied by the LLM; it is embedded here and the vector is passed in the paired
# semantic_xxx_vector parameter (see MCPToolboxClient._validate_semantic_params).
async def _embed_semantic_params(message: AIMessage) -> AIMessage:
    if not message.tool_calls:
        return message

    needs_embedding = any(
        any(k.startswith("semantic_") and not k.endswith("_vector") and isinstance(v, str)
            for k, v in tc["args"].items())
        for tc in message.tool_calls
    )
    if not needs_embedding:
        return message

    try:
        provider = get_embedding_provider()
        new_tool_calls = []
        for tc in message.tool_calls:
            new_args = dict(tc["args"])
            for key, value in list(tc["args"].items()):
                if key.startswith("semantic_") and not key.endswith("_vector") and isinstance(value, str):
                    new_args[f"{key}_vector"] = await provider.embed_text(value)
            new_tool_calls.append({**tc, "args": new_args})
        return AIMessage(content=message.content, tool_calls=new_tool_calls)
    except EmbeddingServiceError as exc:
        return AIMessage(
            content=f"Error: the embedding service is currently unavailable ({exc}). Please try again later.",
            tool_calls=[],
        )


class MCPToolboxAgent(LangGraphAgent):
    tool_result_postprocess_node = "postprocess_tools"
    hidden_arg_suffixes = ("_vector",)

    def __init__(
        self,
        name: str,
        display_name: str,
        description: str = "",
        *,
        system_prompt: str,
        toolbox_url: str | None = None,
        toolset_name: str | None = None,
        llm: BaseChatModel | None = None,
    ):
        super().__init__(name, display_name, description)
        self.system_prompt = system_prompt
        self.toolbox_client = MCPToolboxClient(toolbox_url=toolbox_url, toolset_name=toolset_name)
        self._llm = llm

    def postprocess_tool_message(self, message: ToolMessage) -> ToolMessage | None:
        # Hook: return a replacement ToolMessage (same tool_call_id) to rewrite a tool
        # output before the LLM reads it, or None to keep it unchanged.
        return None

    async def build_graph(self) -> CompiledStateGraph:
        llm = self._llm or build_chat_model()
        tools = await self.toolbox_client.aload_tools()

        llm_with_tools = (
            llm.bind_tools(tools).with_retry(
                retry_if_exception_type=(ValueError,),
                stop_after_attempt=3,
            )
            | RunnableLambda(_fix_raw_tool_calls)
            | RunnableLambda(_embed_semantic_params)
        )

        async def call_model(state: MessagesState):
            messages = _strip_vector_args(
                [SystemMessage(content=self.system_prompt)] + state["messages"]
            )
            try:
                return {"messages": [await llm_with_tools.ainvoke(messages)]}
            except ValueError:
                # The model returned an empty stream (e.g. transient API error).
                # Return a plain AIMessage so the graph exits cleanly instead of crashing.
                return {"messages": [AIMessage(content="The model returned an empty response. Please try again.")]}

        def should_continue(state: MessagesState):
            if state["messages"][-1].tool_calls:
                return self.tools_node
            return END

        def postprocess_tools(state: MessagesState):
            # Only the tool messages produced by the last tools step: they sit at the end
            # of the state, after the AIMessage that requested them.
            updated = []
            for msg in reversed(state["messages"]):
                if not isinstance(msg, ToolMessage):
                    break
                replacement = self.postprocess_tool_message(msg)
                if replacement is not None:
                    updated.append(replacement)
            return {"messages": updated} if updated else {}

        graph = StateGraph(MessagesState)
        graph.add_node(self.agent_node, call_model)
        graph.add_node(self.tools_node, ToolNode(tools))
        graph.add_node(self.tool_result_postprocess_node, postprocess_tools)
        graph.set_entry_point(self.agent_node)
        graph.add_conditional_edges(self.agent_node, should_continue)
        graph.add_edge(self.tools_node, self.tool_result_postprocess_node)
        graph.add_edge(self.tool_result_postprocess_node, self.agent_node)

        return graph.compile()

    async def aclose(self) -> None:
        await self.toolbox_client.aclose()
