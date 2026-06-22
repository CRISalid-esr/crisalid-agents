"""LangGraph factory for the SorboBot agent."""

import logging
from typing import List, Optional

from langchain_core.messages import AIMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import MessagesState

from common.llm import build_chat_model
from sorbobot_agent import db_query_agent, handlers
from sorbobot_agent.config import AppConfig
from sorbobot_agent.intent_classifier import (
    classify_intent,
    contextualize_query,
    detect_language,
    last_human_message,
)
from sorbobot_agent.mcp_toolbox import McpToolboxClient

logger = logging.getLogger("sorbobot_agent.lc_graph")


class SorbobotState(MessagesState):
    query: str
    language: str
    intent: str
    keywords: List[str]
    person_name: Optional[str]


class SorboBotGraphFactory:
    def __init__(self):
        self.config = AppConfig()
        self.toolbox: Optional[McpToolboxClient] = None

    async def abuild_graph(self):
        llm = build_chat_model()
        toolbox = McpToolboxClient(self.config.mcp_toolbox)
        await toolbox.aopen()
        self.toolbox = toolbox
        config = self.config

        async def classify_intent_node(state: SorbobotState):
            query = await contextualize_query(llm, state["messages"])
            classification = await classify_intent(llm, query)
            return {
                "query": query,
                "language": detect_language(query),
                "intent": classification["intent"],
                "keywords": classification.get("keywords") or [],
                "person_name": classification.get("person_name"),
            }

        async def domain_experts_node(state: SorbobotState):
            query = state["query"]
            answer = await handlers.handle_domain_experts(
                toolbox,
                llm,
                config,
                state.get("keywords") or [],
                query,
                state["language"],
            )
            return {"messages": [AIMessage(content=answer)]}

        async def person_expertise_node(state: SorbobotState):
            query = state["query"]
            person_name = state.get("person_name") or query
            answer = await handlers.handle_person_expertise(
                toolbox, llm, config, person_name, query, state["language"]
            )
            return {"messages": [AIMessage(content=answer)]}

        async def general_question_node(state: SorbobotState):
            answer = await handlers.handle_general_question(
                llm, state["query"], state["language"]
            )
            return {"messages": [AIMessage(content=answer)]}

        db_agent_node, db_tools_node, db_should_continue = (
            db_query_agent.build_db_query_nodes(llm, toolbox.tools)
        )

        def route_intent(state: SorbobotState) -> str:
            intent = state.get("intent", "domain_experts")
            if intent == "person_expertise":
                return "person_expertise"
            if intent == "general_question":
                return "general_question"
            if intent == "database_query":
                return "db_agent"
            if intent != "domain_experts":
                logger.warning(
                    "Unknown intent '%s', defaulting to domain_experts", intent
                )
            return "domain_experts"

        graph = StateGraph(SorbobotState)
        graph.add_node("classify_intent", classify_intent_node)
        graph.add_node("domain_experts", domain_experts_node)
        graph.add_node("person_expertise", person_expertise_node)
        graph.add_node("general_question", general_question_node)
        graph.add_node("db_agent", db_agent_node)
        graph.add_node("db_tools", db_tools_node)

        graph.set_entry_point("classify_intent")
        graph.add_conditional_edges("classify_intent", route_intent)
        graph.add_conditional_edges("db_agent", db_should_continue)
        graph.add_edge("db_tools", "db_agent")
        graph.add_edge("domain_experts", END)
        graph.add_edge("person_expertise", END)
        graph.add_edge("general_question", END)

        logger.info(
            "SorboBot graph built (4 intents: domain_experts, person_expertise, "
            "general_question, database_query)."
        )
        return graph.compile()

    async def aclose(self):
        if self.toolbox is not None:
            await self.toolbox.aclose()
