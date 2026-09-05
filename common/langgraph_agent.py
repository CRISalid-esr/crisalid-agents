"""Base class for agents implemented as a LangGraph ``MessagesState`` graph.

Subclasses implement ``build_graph()`` and get for free:

- lazy graph construction on first use,
- ``ainvoke()`` returning the final AI message,
- ``astream()`` translating LangGraph stream parts into ``AgentEvent`` items
  (tokens of the final answer, ``ToolCall`` and ``ToolResult``).

The translation assumes the usual ReAct shape: an LLM node (``agent_node``) that may
emit tool calls, a ``ToolNode`` (``tools_node``), and optionally one node that rewrites
tool results after the tools node (``tool_result_postprocess_node``). Node names are
class attributes so a subclass with a different layout can adjust them.
"""

from collections.abc import AsyncIterator

from langchain_core.messages import AIMessage, BaseMessage
from langgraph.graph.state import CompiledStateGraph

from common.agent import AgentEvent, BaseAgent, ToolCall, ToolResult

# Mistral models served via vLLM without --tool-call-parser emit raw tool calls as content
# (e.g. "[TOOL_CALLS]search-person{...}") instead of structured tool_calls fields.
_RAW_TOOL_CALL_PREFIX = "[TOOL_CALLS]"


class LangGraphAgent(BaseAgent):
    agent_node = "agent"
    tools_node = "tools"
    # When set, ToolResult events are held back after the tools node and emitted after
    # this node completes, with their content replaced by that node's output.
    tool_result_postprocess_node: str | None = None
    # Tool-call argument keys ending with one of these suffixes are never forwarded to UIs
    # (e.g. embedding vectors injected server-side).
    hidden_arg_suffixes: tuple[str, ...] = ()

    def __init__(self, name: str, display_name: str, description: str = ""):
        super().__init__(name, display_name, description)
        self._graph: CompiledStateGraph | None = None

    async def build_graph(self) -> CompiledStateGraph:
        raise NotImplementedError

    async def get_graph(self) -> CompiledStateGraph:
        if self._graph is None:
            self._graph = await self.build_graph()
        return self._graph

    async def ainvoke(self, messages: list[BaseMessage]) -> AIMessage:
        graph = await self.get_graph()
        result = await graph.ainvoke({"messages": messages})
        return result["messages"][-1]

    def _visible_args(self, args: dict) -> dict:
        return {k: v for k, v in args.items() if not k.endswith(self.hidden_arg_suffixes)}

    async def astream(self, messages: list[BaseMessage]) -> AsyncIterator[AgentEvent]:
        # The graph runs a ReAct loop: agent → tools → agent → … → agent (final answer).
        # "messages" parts stream tokens as they are generated; "updates" parts carry
        # the complete output of each node once it finishes.
        graph = await self.get_graph()

        # When True, LLM tokens from the current agent step are suppressed because the
        # model is generating a raw-text tool call rather than a final answer.
        suppress_agent_tokens = False

        # Tool-call metadata from the agent node, keyed by call id, so the matching tool
        # result from the tools node can be paired with its name and arguments.
        pending_tool_calls: dict[str, ToolCall] = {}

        # ToolResult events held until the postprocess node (if any) has run.
        buffered_tool_results: dict[str, ToolResult] = {}

        async for part in graph.astream(
            {"messages": messages},
            stream_mode=["messages", "updates"],
            version="v2",
        ):
            if part["type"] == "updates":
                data = part["data"]

                if self.agent_node in data:
                    msg = data[self.agent_node]["messages"][-1]
                    for tc in getattr(msg, "tool_calls", []):
                        call = ToolCall(id=tc["id"], name=tc["name"], args=self._visible_args(tc.get("args", {})))
                        pending_tool_calls[call.id] = call
                        # Surface the call immediately: the tool is about to run and may take seconds.
                        yield call

                elif self.tools_node in data:
                    suppress_agent_tokens = False
                    for tool_msg in data[self.tools_node]["messages"]:
                        pending = pending_tool_calls.pop(tool_msg.tool_call_id, None)
                        buffered_tool_results[tool_msg.tool_call_id] = ToolResult(
                            id=tool_msg.tool_call_id,
                            name=pending.name if pending else getattr(tool_msg, "name", ""),
                            args=pending.args if pending else {},
                            result=tool_msg.content,
                        )
                    if self.tool_result_postprocess_node is None:
                        for item in buffered_tool_results.values():
                            yield item
                        buffered_tool_results = {}

                elif self.tool_result_postprocess_node is not None and self.tool_result_postprocess_node in data:
                    for msg in (data[self.tool_result_postprocess_node] or {}).get("messages", []):
                        if msg.tool_call_id in buffered_tool_results:
                            buffered_tool_results[msg.tool_call_id].result = msg.content
                    for item in buffered_tool_results.values():
                        yield item
                    buffered_tool_results = {}

            elif part["type"] == "messages":
                chunk, metadata = part["data"]
                if metadata.get("langgraph_node") == self.agent_node and chunk.content:
                    if str(chunk.content).startswith(_RAW_TOOL_CALL_PREFIX):
                        suppress_agent_tokens = True
                    if not suppress_agent_tokens:
                        yield chunk.content
