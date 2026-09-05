"""Generic agent: a ReAct loop over the tools of the CRISalid MCP Toolbox.

The whole LangGraph logic lives here: tool loading, the LLM chain (retry, raw tool-call
recovery, semantic parameter embedding), the agent/tools loop and the post-processing of
tool outputs. ``LangGraphAgent`` only adds the event streaming consumed by the adapters.
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

from agents.generic_agent.schema_postprocessor import compact_schema
from common.langgraph_agent import LangGraphAgent
from common.llm import build_chat_model
from common.mcp_toolbox_client import MCPToolboxClient
from common.semantic_params import embed_semantic_params, strip_vector_args
from common.tool_calls import fix_raw_tool_calls

# Must match the tool name as registered by the MCP toolbox server.
# Check the printed tool list on startup if the name needs adjustment.
_SCHEMA_TOOL_NAME = "get-crisalid-schema"

_PROMPT_DIR = Path(__file__).resolve().parent

_TOOLSET_PROMPTS: dict[str, str] = {
    "crisalid-restricted": "mcp_toolbox_restricted_prompt.md",
    "crisalid-unrestricted": "mcp_toolbox_unrestricted_prompt.md",
}
_DEFAULT_TOOLSET = "crisalid-restricted"


class GenericAgent(LangGraphAgent):
    # Tool results are emitted to the UIs after this node has rewritten them.
    tool_result_postprocess_node = "postprocess_tools"
    # Embedding vectors never reach the UIs.
    hidden_arg_suffixes = ("_vector",)

    def __init__(self, llm: BaseChatModel | None = None):
        super().__init__(
            name="generic_agent",
            display_name="Generic agent",
            description="Answers questions about the CRISalid institutional knowledge graph "
                        "(people, research units, publications) through the MCP Toolbox tools.",
        )
        toolset = os.getenv("CRISALID_MCP_TOOLBOX_TOOLSET", _DEFAULT_TOOLSET)
        prompt_file = _TOOLSET_PROMPTS.get(toolset, _TOOLSET_PROMPTS[_DEFAULT_TOOLSET])
        self.system_prompt = (_PROMPT_DIR / prompt_file).read_text(encoding="utf-8")
        self.toolbox_client = MCPToolboxClient(toolset_name=toolset)
        # An injected model allows offline tests with a fake chat model.
        self._llm = llm

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
            # state, after the AIMessage that requested them): the raw graph schema is a
            # large JSON document, replaced by a compact Markdown summary.
            updated = []
            for msg in reversed(state["messages"]):
                if not isinstance(msg, ToolMessage):
                    break
                if getattr(msg, "name", None) != _SCHEMA_TOOL_NAME:
                    continue
                try:
                    compact = compact_schema(msg.content)
                except Exception:  # noqa: BLE001 — keep the raw output rather than fail the turn
                    continue
                updated.append(ToolMessage(content=compact, tool_call_id=msg.tool_call_id, name=msg.name, id=msg.id))
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


def create_agent(llm: BaseChatModel | None = None) -> GenericAgent:
    return GenericAgent(llm=llm)
