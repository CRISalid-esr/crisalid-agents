import os

from toolbox_core.protocol import Protocol
from toolbox_langchain import ToolboxClient


class MCPToolboxClient:
    def __init__(
        self,
        toolbox_url: str | None = None,
        toolset_name: str | None = None,
    ):
        self.toolbox_url = toolbox_url or os.getenv("CRISALID_MCP_TOOLBOX_URL", "http://127.0.0.1:5000")
        self.toolset_name = toolset_name or os.getenv("CRISALID_MCP_TOOLBOX_TOOLSET", "crisalid-restricted")

        self._client: ToolboxClient | None = None
        self._tools = None

    async def aload_tools(self):
        if self._tools is not None:
            return self._tools

        self._client = ToolboxClient(
            self.toolbox_url,
            protocol=Protocol.MCP_LATEST,
        )

        await self._client.__aenter__()

        self._tools = await self._client.aload_toolset(self.toolset_name)

        print(
            f"Loaded {len(self._tools)} Toolbox tool(s) "
            f"from toolset {self.toolset_name!r}: "
            f"{[tool.name for tool in self._tools]}"
        )

        return self._tools

    async def aclose(self):
        if self._client is not None:
            await self._client.__aexit__(None, None, None)
            self._client = None
            self._tools = None