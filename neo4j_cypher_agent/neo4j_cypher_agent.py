import asyncio

from langchain_core.messages import BaseMessage

from neo4j_cypher_agent.lc_graph import build_lc_graph


class Neo4jCypherAgent:
    def __init__(self):
        self.lc_graph = build_lc_graph()

    async def ainvoke(self, messages: list[BaseMessage]) -> BaseMessage:
        result = await self.lc_graph.ainvoke(
            {
                "messages": messages,
            }
        )

        return result["messages"][-1]

    def invoke(self, messages: list[BaseMessage]) -> BaseMessage:
        return asyncio.run(self.ainvoke(messages))