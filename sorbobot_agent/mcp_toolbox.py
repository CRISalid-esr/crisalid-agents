"""Async wrapper around the crisalid-ai-skills MCP toolbox client.

Loads the `crisalid-sorbobot` MCP toolset and provides a simple `call(tool_name, **kwargs)` method to invoke tools by name.
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
        self.tools = await self._client.aload_toolset(self.config.toolset)
        self._by_name = {t.name: t for t in self.tools}
        logger.info(
            "Loaded MCP toolset '%s' (%d tools)", self.config.toolset, len(self.tools)
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
            parsed = []
        size = len(parsed) if isinstance(parsed, (list, dict)) else "n/a"
        logger.info("MCP tool result: %s -> %s item(s)", tool_name, size)
        return parsed
