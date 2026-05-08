from typing import Generator, Iterator, Union

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from neo4j_mcp_toolbox_agent.neo4j_mcp_toolbox_agent import Neo4jMCPToolboxAgent


def to_langchain_messages(messages: list[dict]) -> list[BaseMessage]:
    result: list[BaseMessage] = []

    for message in messages:
        role = message.get("role")
        content = message.get("content", "")

        if role == "system":
            result.append(SystemMessage(content=content))
        elif role == "user":
            result.append(HumanMessage(content=content))
        elif role == "assistant":
            result.append(AIMessage(content=content))

    return result


class Pipeline:
    def __init__(self):
        self.name = "CRISalid MCP Toolbox agent"
        self.agent = Neo4jMCPToolboxAgent()

    async def on_shutdown(self):
        await self.agent.aclose()

    def pipe(
        self,
        user_message: str,
        model_id: str,
        messages: list[dict],
        body: dict,
    ) -> Union[str, Generator, Iterator]:
        langchain_messages = to_langchain_messages(messages)

        response = self.agent.invoke(langchain_messages)

        return str(response.content)