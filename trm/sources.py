"""Access to the researchers' publications: the CRISalid MCP Toolbox tools, called programmatically."""

import asyncio
import json
from typing import Protocol

from common.mcp_toolbox_client import MCPToolboxClient

PUBLICATIONS_TOOL = "publications-by-theme"
MEMBERSHIPS_TOOL = "get-person-memberships"


class ResearcherSource(Protocol):
    async def search_publications(self, query: str, vector: list[float], limit: int) -> list[dict]: ...

    async def memberships(self, person_uid: str) -> list[str]: ...

    async def aclose(self) -> None: ...


def _parse(result) -> list | dict | None:
    if isinstance(result, str):
        result = result.strip()
        if not result:
            return None
        return json.loads(result)
    return result


def internal_only_filter(rows: list[dict]) -> list[dict]:
    """Client-side equivalent of the tool's ``internal_only`` parameter (toolbox versions without it)."""
    kept = []
    for row in rows:
        contributors = [c for c in row.get("contributors") or [] if c and c.get("external") is False]
        if contributors:
            kept.append({**row, "contributors": contributors})
    return kept


def unit_label(row: dict) -> str:
    short = next((s.get("value") for s in row.get("short_labels") or [] if s and s.get("value")), None)
    long_ = next((s.get("value") for s in row.get("long_labels") or [] if s and s.get("value")), None)
    if short and long_:
        return f"{long_} ({short})"
    return long_ or short or str(row.get("institution_uid") or "")


class ToolboxResearcherSource:
    def __init__(self, client: MCPToolboxClient | None = None):
        self.client = client or MCPToolboxClient()
        self._tools: dict | None = None
        self._load_lock = asyncio.Lock()
        self.supports_internal_only = False
        self._membership_cache: dict[str, list[str]] = {}

    async def _tool(self, name: str):
        async with self._load_lock:  # concurrent searches must not load the toolset several times
            if self._tools is None:
                self._tools = {t.name: t for t in await self.client.aload_tools()}
                schema = getattr(self._tools.get(PUBLICATIONS_TOOL), "args_schema", None)
                self.supports_internal_only = bool(schema and "internal_only" in schema.model_fields)
        if name not in self._tools:
            raise RuntimeError(f"Tool {name!r} is not in the toolbox toolset")
        return self._tools[name]

    async def search_publications(self, query: str, vector: list[float], limit: int = 100) -> list[dict]:
        tool = await self._tool(PUBLICATIONS_TOOL)
        args = {"semantic_theme": query, "semantic_theme_vector": vector, "limit": limit, "use_abstract": True}
        if self.supports_internal_only:
            args["internal_only"] = True
        rows = _parse(await tool.ainvoke(args)) or []
        if not isinstance(rows, list):
            rows = [rows]
        # Members of our laboratories only: filtered by the tool when it supports it, here otherwise.
        return internal_only_filter(rows)

    async def memberships(self, person_uid: str) -> list[str]:
        if person_uid in self._membership_cache:
            return self._membership_cache[person_uid]
        tool = await self._tool(MEMBERSHIPS_TOOL)
        rows = _parse(await tool.ainvoke({"person_uid": person_uid})) or []
        if isinstance(rows, dict):
            rows = [rows]
        labels = [unit_label(r) for r in rows if r]
        self._membership_cache[person_uid] = labels
        return labels

    async def aclose(self) -> None:
        await self.client.aclose()
