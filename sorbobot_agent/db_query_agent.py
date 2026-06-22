import logging
from pathlib import Path
from typing import List, Tuple

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langchain_core.tools import BaseTool
from langgraph.graph.message import MessagesState
from langgraph.prebuilt import ToolNode

logger = logging.getLogger("sorbobot_agent.db_query_agent")

_PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
_DB_AGENT_PROMPT = (_PROMPT_DIR / "db_query_system.md").read_text(encoding="utf-8")


def build_db_query_nodes(llm: BaseChatModel, tools: List[BaseTool]) -> Tuple:
    """Return (db_agent_node, db_tools_node, should_continue) for lc_graph.py."""
    llm_with_tools = llm.bind_tools(tools)

    async def db_agent(state: MessagesState):
        messages = [SystemMessage(content=_DB_AGENT_PROMPT)] + state["messages"]
        return {"messages": [await llm_with_tools.ainvoke(messages)]}

    def should_continue(state: MessagesState) -> str:
        if state["messages"][-1].tool_calls:
            return "db_tools"
        return "__end__"

    db_tools = ToolNode(tools)

    return db_agent, db_tools, should_continue
