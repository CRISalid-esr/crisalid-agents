"""$display_name: a ReAct loop over the tools of an MCP Toolbox toolset.

The whole LangGraph logic lives here: tool loading, the LLM chain (retry, raw tool-call
recovery, semantic parameter embedding), the agent/tools loop and the post-processing of
tool outputs. ``LangGraphAgent`` only adds the event streaming consumed by the adapters.
Started from ``agents/generic_agent``; adapt freely.
"""

import os
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableLambda
from langgraph.graph import END, StateGraph
from langgraph.graph.message import MessagesState
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode

from common.langgraph_agent import LangGraphAgent
from common.llm import build_chat_model
from common.mcp_toolbox_client import MCPToolboxClient
from common.semantic_params import embed_semantic_params, strip_vector_args
from common.tool_calls import fix_raw_tool_calls

_SYSTEM_PROMPT = (Path(__file__).resolve().parent / "system_prompt.md").read_text(encoding="utf-8")


class $class_name(LangGraphAgent):
    # Tool results are emitted to the UIs after this node has rewritten them.
    tool_result_postprocess_node = "postprocess_tools"
    # Embedding vectors never reach the UIs.
    hidden_arg_suffixes = ("_vector",)

    def __init__(self, llm: BaseChatModel | None = None):
        super().__init__(
            name="$name",
            display_name="$display_name",
            description="$description",
        )
        self.system_prompt = _SYSTEM_PROMPT
        # Per-agent settings; when unset, the shared CRISALID_MCP_TOOLBOX_URL /
        # CRISALID_MCP_TOOLBOX_TOOLSET variables apply.
        self.toolbox_client = MCPToolboxClient(
            toolbox_url=os.getenv("${NAME}_MCP_TOOLBOX_URL"),
            toolset_name=os.getenv("${NAME}_MCP_TOOLBOX_TOOLSET"),
        )
        # An injected model allows offline tests with a fake chat model.
        self._llm = llm

    def postprocess_tool_message(self, message: ToolMessage) -> ToolMessage | None:
        # Return a replacement ToolMessage (same tool_call_id) to rewrite a tool output
        # before the LLM reads it, or None to keep it unchanged.
        return None

    async def build_graph(self) -> CompiledStateGraph:
        llm = self._llm or build_chat_model()
        tools = await self.toolbox_client.aload_tools()

        llm_with_tools = (
            llm.bind_tools(tools).with_retry(
                retry_if_exception_type=(ValueError,),
                stop_after_attempt=3,
            )
            | RunnableLambda(fix_raw_tool_calls)
            | RunnableLambda(embed_semantic_params)
        )

        async def call_model(state: MessagesState):
            messages = strip_vector_args(
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
            # Rewrite the tool messages of the last tools step (they sit at the end of the
            # state, after the AIMessage that requested them).
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


def create_agent(llm: BaseChatModel | None = None) -> $class_name:
    return $class_name(llm=llm)
