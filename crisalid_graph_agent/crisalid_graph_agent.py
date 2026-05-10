import asyncio
from collections.abc import AsyncGenerator, Generator

from langchain_core.messages import BaseMessage

from crisalid_graph_agent.lc_graph import MCPToolboxGraphFactory


class CrisalidGraphAgent:
    def __init__(self):
        self._factory = MCPToolboxGraphFactory()
        self._graph = None

    async def _get_graph(self):
        if self._graph is None:
            self._graph = await self._factory.abuild_graph()

        return self._graph

    async def ainvoke(self, messages: list[BaseMessage]) -> BaseMessage:
        graph = await self._get_graph()

        result = await graph.ainvoke({"messages": messages})

        return result["messages"][-1]

    def invoke(self, messages: list[BaseMessage]) -> BaseMessage:
        return asyncio.run(self.ainvoke(messages))

    async def astream(self, messages: list[BaseMessage]) -> AsyncGenerator[str | dict, None]:
        # Yields two kinds of items:
        #   - str          : one LLM token destined for the user (final answer only)
        #   - dict         : a tool_result event carrying id/name/args/result,
        #                    emitted once per tool call after the tool node completes
        #
        # The graph runs a ReAct loop: agent → tools → agent → … → agent (final answer).
        # "messages" parts stream tokens as they are generated; "updates" parts carry
        # the complete output of each node once it finishes.
        graph = await self._get_graph()

        # When True, LLM tokens from the current agent step are suppressed because
        # the model is generating a tool call rather than a final answer.
        suppress_agent_tokens = False

        # Accumulates tool-call metadata (name + args) from the agent node so it can
        # be paired with the matching tool result from the tools node.
        pending_tool_calls: dict[str, dict] = {}

        async for part in graph.astream(
            {"messages": messages},
            stream_mode=["messages", "updates"],
            version="v2",
        ):
            # ── Node-completion events ──────────────────────────────────────────
            if part["type"] == "updates":
                data = part["data"]

                if "agent" in data:
                    # The agent node just finished. If its output contains tool_calls,
                    # register them in pending_tool_calls so the tools node result can
                    # be matched back by call id later.
                    msg = data["agent"]["messages"][-1]
                    for tc in getattr(msg, "tool_calls", []):
                        pending_tool_calls[tc["id"]] = {
                            "name": tc["name"],
                            "args": tc.get("args", {}),
                        }

                elif "tools" in data:
                    # The tools node just finished. Pair each ToolMessage with its
                    # pending call and emit a tool_result dict, then re-enable token
                    # streaming for the next agent step (which is the final answer).
                    suppress_agent_tokens = False
                    for tool_msg in data["tools"]["messages"]:
                        pending = pending_tool_calls.pop(tool_msg.tool_call_id, {})
                        yield {
                            "type": "tool_result",
                            "id": tool_msg.tool_call_id,
                            "name": pending.get("name", getattr(tool_msg, "name", "")),
                            "args": pending.get("args", {}),
                            "result": tool_msg.content,
                        }

            # ── Token-streaming events ──────────────────────────────────────────
            elif part["type"] == "messages":
                chunk, metadata = part["data"]
                if metadata.get("langgraph_node") == "agent" and chunk.content:
                    # Mistral models served via vLLM without --tool-call-parser emit raw
                    # tool calls as content (e.g. "[TOOL_CALLS]search-person{...}") instead
                    # of structured tool_calls fields. Suppress all tokens once that prefix
                    # is detected; the flag resets when the tools node completes.
                    if str(chunk.content).startswith("[TOOL_CALLS]"):
                        suppress_agent_tokens = True
                    if not suppress_agent_tokens:
                        yield chunk.content

    def stream(self, messages: list[BaseMessage]) -> Generator[str | dict, None, None]:
        # Synchronous bridge over astream() for callers that cannot use async
        # (e.g. OpenWebUI pipe(), which runs in a threadpool thread with no event loop).
        loop = asyncio.new_event_loop()
        gen = self.astream(messages)
        try:
            while True:
                try:
                    yield loop.run_until_complete(gen.__anext__())
                except StopAsyncIteration:
                    break
        finally:
            loop.close()

    async def aclose(self):
        await self._factory.aclose()
