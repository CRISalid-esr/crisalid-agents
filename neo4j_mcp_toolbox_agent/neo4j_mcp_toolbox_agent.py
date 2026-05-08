import asyncio

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

        result = await graph.ainvoke(
            {
                "messages": messages,
            }
        )

        return result["messages"][-1]

    def invoke(self, messages: list[BaseMessage]) -> BaseMessage:
        return asyncio.run(self.ainvoke(messages))

    async def aclose(self):
        await self._factory.aclose()