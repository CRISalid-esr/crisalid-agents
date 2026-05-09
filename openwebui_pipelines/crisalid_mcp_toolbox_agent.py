from collections.abc import Generator
from typing import Iterator, Union

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

        for item in self.agent.stream(langchain_messages):
            if isinstance(item, str):
                yield item
            elif item.get("type") == "tool_call":
                yield {
                    "event": {
                        "type": "status",
                        "data": {
                            "description": f"Calling tool: {item['name']}",
                            "done": False,
                        },
                    }
                }
            elif item.get("type") == "tools_done":
                yield {
                    "event": {
                        "type": "status",
                        "data": {
                            "description": "Tools done",
                            "done": True,
                        },
                    }
                }
