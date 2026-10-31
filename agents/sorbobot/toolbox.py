"""Access to the MCP tools of the ``sorbobot`` toolset (§2.1 of the spec).

``SorbobotToolbox`` turns the JSON answers of the tools into typed rows. ``ToolboxConnection`` opens one toolbox
client per running event loop (OpenWebUI runs every turn on a new loop) and releases it when the last turn using
that loop ends.
"""

import asyncio
import json
import logging
from collections import defaultdict

from agents.sorbobot.models import (
    Concept,
    DocumentSummary,
    EvidenceRow,
    PersonConcept,
    PersonDocument,
    PersonHit,
    as_int,
    as_number,
)
from common.mcp_toolbox_client import MCPToolboxClient

logger = logging.getLogger(__name__)

REQUIRED_TOOLS = (
    "get-concepts-by-uid",
    "get-concept-hierarchy",
    "list-concept-siblings",
    "search-concept-by-name",
    "list-concept-expert-evidence",
    "get-person-publication-counts",
    "search-person-by-name",
    "list-person-research-concepts",
    "list-person-top-documents",
    "list-document-summaries",
)


class GraphUnavailableError(Exception):
    """The toolbox could not be reached, was too slow or answered something unreadable."""


class MissingToolsError(Exception):
    """A deployment error: the toolset lacks tools Sorbobot needs."""


def _joined(uids) -> str:
    return ",".join(str(uid).strip() for uid in uids if str(uid).strip())


class SorbobotToolbox:
    def __init__(self, tools, timeout_seconds: float, topic_sources: str = ""):
        by_name = {tool.name: tool for tool in tools}
        missing = [name for name in REQUIRED_TOOLS if name not in by_name]
        if missing:
            raise MissingToolsError(f"The MCP toolset lacks the tool(s) {missing}; Sorbobot needs {list(REQUIRED_TOOLS)}")
        self._tools = by_name
        self.timeout_seconds = timeout_seconds
        # Comma-separated HAS_TOPIC sources passed to every tool filtering publication-to-topic links; "" keeps all.
        self.topic_sources = topic_sources

    async def _rows(self, tool_name: str, **arguments) -> list[dict]:
        try:
            async with asyncio.timeout(self.timeout_seconds):
                raw = await self._tools[tool_name].ainvoke(arguments)
            data = json.loads(raw) if isinstance(raw, str) else raw
        except TimeoutError as exc:
            logger.warning("MCP tool %s timed out after %.0fs", tool_name, self.timeout_seconds)
            raise GraphUnavailableError(f"MCP tool {tool_name} timed out") from exc
        except Exception as exc:  # noqa: BLE001 — any failure of the toolbox is one error for the user
            logger.warning("MCP tool %s failed: %r", tool_name, exc)
            raise GraphUnavailableError(f"MCP tool {tool_name} failed: {exc!r}") from exc
        if data is None:
            return []
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            raise GraphUnavailableError(f"MCP tool {tool_name} answered something unreadable: {str(raw)[:120]!r}")
        return [row for row in data if isinstance(row, dict)]

    async def get_concepts(self, uids, similarity_threshold: float) -> dict[str, Concept]:
        if not uids:
            return {}
        rows = await self._rows("get-concepts-by-uid", uids=_joined(uids), similarity_threshold=similarity_threshold,
                                sources=self.topic_sources)
        concepts = {}
        for row in rows:
            uid = row.get("uid")
            if uid:
                concepts[str(uid)] = Concept(
                    uid=str(uid), name=str(row.get("name") or ""), description=str(row.get("description") or ""),
                    nb_docs=as_int(row.get("nb_docs")),
                )
        return concepts

    async def get_concept_paths(self, uids) -> dict[str, str]:
        """uid -> ``Domain [Domain] > Field [Field] > SubField [SubField] > Topic [Topic]``."""
        if not uids:
            return {}
        rows = await self._rows("get-concept-hierarchy", uids=_joined(uids))
        ancestors: dict[str, list[tuple[int, str]]] = defaultdict(list)
        for row in rows:
            uid, name = row.get("uid"), row.get("ancestor_name")
            if not uid or not name:
                continue
            kind = row.get("ancestor_type") or ""
            label = f"{name} [{kind}]" if kind else str(name)
            ancestors[str(uid)].append((as_int(row.get("dist")), label))
        return {uid: " > ".join(label for _, label in sorted(steps, key=lambda step: -step[0]))
                for uid, steps in ancestors.items()}

    async def list_siblings(self, uids) -> list[str]:
        if not uids:
            return []
        rows = await self._rows("list-concept-siblings", uids=_joined(uids))
        return [str(row["uid"]) for row in rows if row.get("uid")]

    async def search_concepts_by_name(self, name: str) -> list[dict]:
        rows = await self._rows("search-concept-by-name", name=name)
        return [{"uid": str(row["uid"]), "name": str(row.get("name") or ""), "type": str(row.get("type") or "")}
                for row in rows if row.get("uid")]

    async def list_expert_evidence(self, uids, similarity_threshold: float) -> list[EvidenceRow]:
        if not uids:
            return []
        rows = await self._rows("list-concept-expert-evidence", uids=_joined(uids),
                                similarity_threshold=similarity_threshold, sources=self.topic_sources)
        evidence = []
        for row in rows:
            similarity = as_number(row.get("similarity"))
            if not (row.get("person_uid") and row.get("doc_uid") and row.get("topic_uid")) or similarity is None:
                continue
            evidence.append(EvidenceRow(
                person_uid=str(row["person_uid"]), display_name=str(row.get("display_name") or row["person_uid"]),
                doc_uid=str(row["doc_uid"]), doc_title=str(row.get("doc_title") or ""),
                publication_date=row.get("publication_date"), topic_uid=str(row["topic_uid"]), similarity=similarity,
            ))
        return evidence

    async def get_publication_counts(self, person_uids) -> dict[str, int]:
        if not person_uids:
            return {}
        rows = await self._rows("get-person-publication-counts", person_uids=_joined(person_uids))
        return {str(row["person_uid"]): as_int(row.get("nb_publications_total"))
                for row in rows if row.get("person_uid")}

    async def search_persons(self, name: str, max_results: int) -> list[PersonHit]:
        rows = await self._rows("search-person-by-name", name=name, max_results=max_results)
        return [PersonHit(uid=str(row["uid"]), display_name=str(row.get("display_name") or ""),
                          external=bool(row.get("external")), score=as_number(row.get("score")) or 0.0)
                for row in rows if row.get("uid")]

    async def list_person_concepts(self, person_uid: str, similarity_threshold: float, limit: int) -> list[PersonConcept]:
        rows = await self._rows("list-person-research-concepts", person_uid=person_uid,
                                similarity_threshold=similarity_threshold, limit=limit, sources=self.topic_sources)
        return [PersonConcept(uid=str(row["domain_uid"]), name=str(row.get("domain_name") or ""),
                              nb_publications=as_int(row.get("nb_publications")))
                for row in rows if row.get("domain_uid")]

    async def list_person_documents(self, person_uid: str, concept_uids, similarity_threshold: float,
                                    limit: int) -> list[PersonDocument]:
        if not concept_uids:
            return []
        rows = await self._rows("list-person-top-documents", person_uid=person_uid, concept_uids=_joined(concept_uids),
                                similarity_threshold=similarity_threshold, limit=limit, sources=self.topic_sources)
        documents = []
        for row in rows:
            if not row.get("doc_uid"):
                continue
            concepts = row.get("concept_uids") or []
            roles = row.get("roles") or []
            documents.append(PersonDocument(
                uid=str(row["doc_uid"]), title=str(row.get("title") or ""), publication_date=row.get("publication_date"),
                concept_uids=[str(uid) for uid in concepts] if isinstance(concepts, list) else [],
                roles=[str(role) for role in roles] if isinstance(roles, list) else [],
            ))
        return documents

    async def get_document_summaries(self, uids) -> dict[str, DocumentSummary]:
        if not uids:
            return {}
        rows = await self._rows("list-document-summaries", uids=_joined(uids))
        summaries = {}
        for row in rows:
            if not row.get("uid"):
                continue
            keywords = row.get("keywords") or []
            summaries[str(row["uid"])] = DocumentSummary(
                uid=str(row["uid"]), abstract=row.get("abstract") or None,
                keywords=tuple(str(k) for k in keywords if k) if isinstance(keywords, list) else (),
            )
        return summaries


class ToolboxConnection:
    """One ``SorbobotToolbox`` per running event loop, released when the last turn using the loop ends.

    Every method of ``SorbobotToolbox`` is available on the connection: it is resolved against the toolbox of the
    current loop, loading the toolset on first use.
    """

    def __init__(self, toolbox_url: str | None, toolset: str, timeout_seconds: float, factory=None,
                 topic_sources: str = ""):
        self._factory = factory or (lambda: MCPToolboxClient(toolbox_url=toolbox_url, toolset_name=toolset))
        self.timeout_seconds = timeout_seconds
        self.topic_sources = topic_sources
        self._clients: dict[asyncio.AbstractEventLoop, MCPToolboxClient] = {}
        self._toolboxes: dict[asyncio.AbstractEventLoop, SorbobotToolbox] = {}
        self._leases: dict[asyncio.AbstractEventLoop, int] = defaultdict(int)

    async def current(self) -> SorbobotToolbox:
        loop = asyncio.get_running_loop()
        toolbox = self._toolboxes.get(loop)
        if toolbox is None:
            self._forget_closed_loops()
            client = self._factory()
            try:
                tools = await client.aload_tools()
            except MissingToolsError:
                raise
            except Exception as exc:  # noqa: BLE001 — the toolbox is unreachable: one error for the user
                logger.warning("MCP toolbox unavailable: %r", exc)
                raise GraphUnavailableError(f"MCP toolbox unavailable: {exc!r}") from exc
            toolbox = SorbobotToolbox(tools, self.timeout_seconds, self.topic_sources)
            self._clients[loop] = client
            self._toolboxes[loop] = toolbox
        return toolbox

    async def acquire(self) -> SorbobotToolbox:
        """Mark the start of a turn on the current loop; the toolset is loaded and checked here."""
        self._leases[asyncio.get_running_loop()] += 1
        try:
            return await self.current()
        except BaseException:
            await self.release()
            raise

    async def release(self) -> None:
        loop = asyncio.get_running_loop()
        self._leases[loop] = max(0, self._leases[loop] - 1)
        if self._leases[loop] == 0:
            await self.aclose()

    async def aclose(self) -> None:
        loop = asyncio.get_running_loop()
        self._leases.pop(loop, None)
        self._toolboxes.pop(loop, None)
        client = self._clients.pop(loop, None)
        if client is not None:
            await client.aclose()

    def _forget_closed_loops(self) -> None:
        for loop in [loop for loop in self._clients if loop.is_closed()]:
            self._clients.pop(loop, None)
            self._toolboxes.pop(loop, None)
            self._leases.pop(loop, None)

    def __getattr__(self, name: str):
        if name.startswith("_") or not hasattr(SorbobotToolbox, name):
            raise AttributeError(name)

        async def call(*args, **kwargs):
            return await getattr(await self.current(), name)(*args, **kwargs)

        return call
