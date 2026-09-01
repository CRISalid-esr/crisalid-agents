"""Async wrapper around the crisalid-ai-skills MCP toolbox client.

Loads the `sorbobot` toolset (SorboBot-specific tools, `SORBOBOT_TOOLSET`)
and the shared toolset also used by other consumers (`MCP_TOOLBOX_TOOLSET`,
e.g. `crisalid-unrestricted`), merging them into a single tool set — and
exposes both a typed `call(tool_name, **kwargs)` helper for
handlers/domain_tools and the raw LangChain `tools` list for binding to the
db_agent ReAct loop.
"""

import json
import logging
from typing import Any, Optional

from toolbox_core.protocol import Protocol
from toolbox_langchain import ToolboxClient

from sorbobot_agent.config import MCPToolboxConfig

logger = logging.getLogger("sorbobot_agent.mcp_toolbox")


class McpToolboxClient:
    """Manual open/close lifecycle (`aopen()` / `aclose()`)."""

    def __init__(self, config: MCPToolboxConfig):
        self.config = config
        self.tools: list = []
        self._client: Optional[ToolboxClient] = None
        self._by_name: dict = {}

    async def aopen(self) -> None:
        self._client = ToolboxClient(self.config.url, protocol=Protocol.MCP_LATEST)
        await self._client.__aenter__()

        self.tools = []
        self._by_name = {}
        for toolset in (self.config.sorbobot_toolset, self.config.toolset):
            toolset_tools = await self._client.aload_toolset(toolset)
            new_tools = [t for t in toolset_tools if t.name not in self._by_name]
            self.tools.extend(new_tools)
            self._by_name.update({t.name: t for t in new_tools})
            logger.info(
                "Loaded MCP toolset '%s' (%d tools, %d new)",
                toolset,
                len(toolset_tools),
                len(new_tools),
            )

        logger.info(
            "MCP toolbox ready: %d unique tool(s) across 2 toolset(s)", len(self.tools)
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.__aexit__(None, None, None)
            self._client = None

    async def call(self, tool_name: str, **kwargs: Any) -> Any:
        logger.info("MCP tool call: %s(%s)", tool_name, kwargs)
        result = await self._by_name[tool_name].ainvoke(kwargs)
        parsed = json.loads(result) if isinstance(result, str) else result
        if parsed is None:
            # Some Cypher-backed tools return JSON null (rather than []) when
            # a query matches zero rows — normalise so callers can always
            # iterate the result without a None check.
            parsed = []
        size = len(parsed) if isinstance(parsed, (list, dict)) else "n/a"
        logger.info("MCP tool result: %s -> %s item(s)", tool_name, size)
        return parsed