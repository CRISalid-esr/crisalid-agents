from typing import TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph import END, StateGraph

from neo4j_cypher_agent.cypher_qa_chain import cypher_qa_chain


class AgentState(TypedDict):
    messages: list[BaseMessage]


def _last_user_message(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return str(message.content)
    return ""


def build_lc_graph():
    chain = cypher_qa_chain()

    async def graph_cypher_qa_node(state: AgentState) -> AgentState:
        question = _last_user_message(state["messages"])

        result = await chain.ainvoke(
            {
                "query": question,
            }
        )

        answer = result.get("result", str(result))

        return {
            "messages": state["messages"] + [AIMessage(content=answer)]
        }

    graph_builder = StateGraph(AgentState)
    graph_builder.add_node("graph_cypher_qa", graph_cypher_qa_node)

    graph_builder.set_entry_point("graph_cypher_qa")
    graph_builder.add_edge("graph_cypher_qa", END)

    return graph_builder.compile()
