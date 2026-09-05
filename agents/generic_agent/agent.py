import os
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import ToolMessage

from agents.generic_agent.schema_postprocessor import compact_schema
from common.mcp_toolbox_agent import MCPToolboxAgent

# Must match the tool name as registered by the MCP toolbox server.
# Check the printed tool list on startup if the name needs adjustment.
_SCHEMA_TOOL_NAME = "get-crisalid-schema"

_PROMPT_DIR = Path(__file__).resolve().parent

_TOOLSET_PROMPTS: dict[str, str] = {
    "crisalid-restricted": "mcp_toolbox_restricted_prompt.md",
    "crisalid-unrestricted": "mcp_toolbox_unrestricted_prompt.md",
}
_DEFAULT_TOOLSET = "crisalid-restricted"


class GenericAgent(MCPToolboxAgent):
    def __init__(self, llm: BaseChatModel | None = None):
        toolset = os.getenv("CRISALID_MCP_TOOLBOX_TOOLSET", _DEFAULT_TOOLSET)
        prompt_file = _TOOLSET_PROMPTS.get(toolset, _TOOLSET_PROMPTS[_DEFAULT_TOOLSET])
        super().__init__(
            name="generic_agent",
            display_name="Generic agent",
            description="Answers questions about the CRISalid institutional knowledge graph "
                        "(people, research units, publications) through the MCP Toolbox tools.",
            system_prompt=(_PROMPT_DIR / prompt_file).read_text(encoding="utf-8"),
            toolset_name=toolset,
            llm=llm,
        )

    def postprocess_tool_message(self, message: ToolMessage) -> ToolMessage | None:
        # The raw graph schema is a large JSON document: replace it with the compact
        # Markdown summary so the LLM (and the UI) get something readable.
        if getattr(message, "name", None) != _SCHEMA_TOOL_NAME:
            return None
        try:
            compact = compact_schema(message.content)
        except Exception:  # noqa: BLE001 — keep the raw output rather than fail the turn
            return None
        return ToolMessage(content=compact, tool_call_id=message.tool_call_id, name=message.name, id=message.id)


def create_agent(llm: BaseChatModel | None = None) -> GenericAgent:
    return GenericAgent(llm=llm)
