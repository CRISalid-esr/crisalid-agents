from typing import Generator, Iterator, Union

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from neo4j_cypher_agent.neo4j_cypher_agent import Neo4jCypherAgent


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
        self.name = "CRISalid Neo4j Cypher QA agent"
        self.agent = Neo4jCypherAgent()

    def pipe(
            self,
            user_message: str,
            model_id: str,
            messages: list[dict],
            body: dict,
    ) -> Union[str, Generator, Iterator]:
        langchain_messages = to_langchain_messages(messages)

        response = self.agent.invoke(langchain_messages)
        return response.content
