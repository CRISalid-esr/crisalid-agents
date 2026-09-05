"""$display_name: a ReAct agent over the tools of an MCP Toolbox toolset.

``MCPToolboxAgent`` (``common/mcp_toolbox_agent.py``) builds the LangGraph loop, loads the
toolset, embeds ``semantic_*`` parameters and handles provider quirks. This module only
declares the prompt, the toolset and optional tool-output post-processing.
"""

import os
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import ToolMessage

from common.mcp_toolbox_agent import MCPToolboxAgent

_SYSTEM_PROMPT = (Path(__file__).resolve().parent / "system_prompt.md").read_text(encoding="utf-8")


class $class_name(MCPToolboxAgent):
    def __init__(self, llm: BaseChatModel | None = None):
        super().__init__(
            name="$name",
            display_name="$display_name",
            description="$description",
            system_prompt=_SYSTEM_PROMPT,
            # Per-agent settings; when unset, the shared CRISALID_MCP_TOOLBOX_URL /
            # CRISALID_MCP_TOOLBOX_TOOLSET variables apply.
            toolbox_url=os.getenv("${NAME}_MCP_TOOLBOX_URL"),
            toolset_name=os.getenv("${NAME}_MCP_TOOLBOX_TOOLSET"),
            llm=llm,
        )

    def postprocess_tool_message(self, message: ToolMessage) -> ToolMessage | None:
        # Optional hook: return a replacement ToolMessage (same tool_call_id) to rewrite
        # a tool output before the LLM reads it, or None to keep it unchanged.
        return None


def create_agent(llm: BaseChatModel | None = None) -> $class_name:
    return $class_name(llm=llm)
