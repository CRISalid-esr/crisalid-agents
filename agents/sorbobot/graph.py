"""Typed calls to the ``sorbobot`` MCP Toolbox toolset.

The tools taking several concepts read them as one comma-separated string, which no OpenAlex identifier contains.
"""

import asyncio
import json

from langchain_core.tools import BaseTool

from agents.sorbobot.models import as_number

REQUIRED_TOOLS = (
    "get-concepts-by-uid",
    "get-concept-hierarchy",
    "list-concept-children",
    "list-concept-siblings",
    "list-concept-expert-evidence",
    "get-person-publication-counts",
    "list-person-research-concepts",
    "list-person-top-documents",
    "list-document-summaries",
    "search-person-by-name",
)


class MissingToolsError(RuntimeError):
    """The toolset lacks tools Sorbobot needs: wrong toolset name or outdated toolbox."""


class GraphUnavailableError(RuntimeError):
    """A toolbox call failed: server unreachable, authentication refused or unreadable answer."""


def _ancestor(row: dict) -> str:
    name = row.get("ancestor_name") or row["ancestor_uid"]
    level = row.get("ancestor_type")
    return f"{name} [{level}]" if level else name


class SorbobotGraph:
    def __init__(self, tools: list[BaseTool], timeout_seconds: float):
        self._tools = {tool.name: tool for tool in tools}
        self._timeout_seconds = timeout_seconds
        missing = [name for name in REQUIRED_TOOLS if name not in self._tools]
        if missing:
            raise MissingToolsError(f"The MCP toolset is missing: {', '.join(missing)}")

    async def _rows(self, tool_name: str, **arguments) -> list[dict]:
        tool = self._tools[tool_name]
        try:
            async with asyncio.timeout(self._timeout_seconds):
                raw = await tool.ainvoke(arguments)
        except TimeoutError as exc:
            raise GraphUnavailableError(
                f"MCP Toolbox call {tool_name!r} timed out after {self._timeout_seconds}s"
            ) from exc
        except Exception as exc:
            raise GraphUnavailableError(f"MCP Toolbox call {tool_name!r} failed: {exc!r}") from exc
        try:
            rows = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError as exc:
            raise GraphUnavailableError(f"MCP Toolbox call {tool_name!r} answered unreadable JSON: {exc}") from exc
        if rows is None:
            return []
        if not isinstance(rows, list):
            raise GraphUnavailableError(f"MCP Toolbox call {tool_name!r} answered {type(rows).__name__}, not rows")
        return rows

    async def get_concepts(self, uids: list[str], similarity_threshold: float) -> dict[str, dict]:
        """uid -> ``name``, ``description`` and ``nb_docs`` (documents of the concept and its descendants)."""
        if not uids:
            return {}
        rows = await self._rows("get-concepts-by-uid", uids=",".join(uids), similarity_threshold=similarity_threshold)
        return {row["uid"]: row for row in rows if row.get("uid")}

    async def get_concept_paths(self, uids: list[str]) -> dict[str, str]:
        """uid -> "Physical Sciences [Domain] > Computer Science [Field] > … > Neural Networks [Topic]"."""
        if not uids:
            return {}
        chains: dict[str, list[dict]] = {}
        for row in await self._rows("get-concept-hierarchy", uids=",".join(uids)):
            if row.get("uid") and (row.get("ancestor_name") or row.get("ancestor_uid")):
                chains.setdefault(row["uid"], []).append(row)
        return {
            uid: " > ".join(_ancestor(row) for row in sorted(chain, key=lambda r: -(as_number(r.get("dist")) or 0.0)))
            for uid, chain in chains.items()
        }

    async def list_children(self, uids: list[str]) -> list[str]:
        if not uids:
            return []
        return [row["uid"] for row in await self._rows("list-concept-children", uids=",".join(uids)) if row.get("uid")]

    async def list_siblings(self, uids: list[str]) -> list[str]:
        """The concepts sharing a parent with the given ones, the given ones excluded."""
        if not uids:
            return []
        rows = await self._rows("list-concept-siblings", uids=",".join(uids))
        return [row["uid"] for row in rows if row.get("uid")]

    async def list_expert_evidence(self, uids: list[str], similarity_threshold: float) -> list[dict]:
        """One row per (internal person, document, topic) on the given concepts."""
        if not uids:
            return []
        return await self._rows(
            "list-concept-expert-evidence", uids=",".join(uids), similarity_threshold=similarity_threshold
        )

    async def get_publication_counts(self, person_uids: list[str]) -> dict[str, int]:
        if not person_uids:
            return {}
        rows = await self._rows("get-person-publication-counts", person_uids=",".join(person_uids))
        counts = ((row.get("person_uid"), as_number(row.get("nb_publications_total"))) for row in rows)
        return {uid: int(total) for uid, total in counts if uid and total is not None}

    async def search_persons(self, name: str, max_results: int) -> list[dict]:
        """Typo-tolerant name search, best score first, external people included. The name must be cleaned."""
        return await self._rows("search-person-by-name", name=name, max_results=max_results)

    async def list_person_concepts(self, person_uid: str, similarity_threshold: float, limit: int) -> list[dict]:
        return await self._rows(
            "list-person-research-concepts",
            person_uid=person_uid,
            similarity_threshold=similarity_threshold,
            limit=limit,
        )

    async def list_person_documents(
        self, person_uid: str, concept_uids: list[str], similarity_threshold: float, limit: int
    ) -> list[dict]:
        """The person's publications on the concepts, strongest topic link first, with their roles."""
        if not concept_uids:
            return []
        return await self._rows(
            "list-person-top-documents",
            person_uid=person_uid,
            concept_uids=",".join(concept_uids),
            similarity_threshold=similarity_threshold,
            limit=limit,
        )

    async def get_document_summaries(self, uids: list[str]) -> dict[str, dict]:
        """uid -> ``abstract`` (None when missing) and ``keywords``."""
        if not uids:
            return {}
        rows = await self._rows("list-document-summaries", uids=",".join(uids))
        return {row["uid"]: row for row in rows if row.get("uid")}
