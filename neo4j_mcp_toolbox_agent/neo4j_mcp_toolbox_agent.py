import asyncio
from collections.abc import AsyncGenerator, Generator

from langchain_core.messages import BaseMessage

from neo4j_mcp_toolbox_agent.lc_graph import MCPToolboxGraphFactory


class Neo4jMCPToolboxAgent:
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
        graph = await self._get_graph()
        suppress_agent_tokens = False

        async for stream_type, data in graph.astream(
            {"messages": messages},
            stream_mode=["messages", "updates"],
        ):
            if stream_type == "updates":
                if "agent" in data:
                    msg = data["agent"]["messages"][-1]
                    if getattr(msg, "tool_calls", None):
                        for tc in msg.tool_calls:
                            yield {"type": "tool_call", "name": tc["name"]}
                elif "tools" in data:
                    suppress_agent_tokens = False
                    yield {"type": "tools_done"}

            elif stream_type == "messages":
                chunk, metadata = data
                if metadata.get("langgraph_node") == "agent" and chunk.content:
                    if str(chunk.content).startswith("[TOOL_CALLS]"):
                        suppress_agent_tokens = True
                    if not suppress_agent_tokens:
                        yield chunk.content

    def stream(self, messages: list[BaseMessage]) -> Generator[str | dict, None, None]:
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
