"""Per-topic LangGraph pipeline: expand → retrieve → verify → score.

Every LLM call is a single-shot structured prompt; the graph exists for observability and testing, not for a
ReAct loop. Services (LLM, embedder, researcher source) are bound once per run through ``build_graph``.
"""

import asyncio
import logging
import time
from typing import TypedDict

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from common.embedding import EmbeddingProvider
from trm.config import TRMSettings
from trm.llm import StructuredLLM, expand_topic, verify_candidate
from trm.models import Candidate, Expansion, Publication, TopicResult, Verification
from trm.scoring import build_candidates, final_matches, is_retained, rank_publications
from trm.sources import ResearcherSource

logger = logging.getLogger(__name__)


class TopicState(TypedDict, total=False):
    topic: dict
    expansion: Expansion
    publications: dict[str, Publication]
    candidates: list[Candidate]
    verified: list[tuple[Candidate, Verification]]
    result: TopicResult
    warnings: list[str]


def _result_shell(topic: dict) -> TopicResult:
    return TopicResult(
        topic_id=topic.get("topic_id", ""),
        title=topic.get("title", ""),
        cluster=topic.get("cluster", ""),
        call_id=topic.get("call_id"),
        deadlines=list(topic.get("deadlines") or []),
        destination=topic.get("destination"),
    )


def build_graph(
    llm: StructuredLLM, embedder: EmbeddingProvider, source: ResearcherSource, settings: TRMSettings
) -> CompiledStateGraph:
    semaphore = asyncio.Semaphore(settings.concurrency)

    async def expand(state: TopicState) -> dict:
        expansion = await expand_topic(llm, state["topic"], settings)
        return {"expansion": expansion, "warnings": []}

    async def retrieve(state: TopicState) -> dict:
        queries = state["expansion"].queries
        vectors = await embedder.embed_texts(queries)

        async def one(query: str, vector: list[float]) -> tuple[str, list[dict]]:
            async with semaphore:
                return query, await source.search_publications(query, vector, limit=100)

        results = dict(await asyncio.gather(*(one(q, v) for q, v in zip(queries, vectors))))
        publications = rank_publications(results, settings.rank_constant)
        candidates = build_candidates(publications, settings)
        warnings = list(state.get("warnings") or [])
        if not publications:
            warnings.append("No publication returned for any query")
        return {"publications": publications, "candidates": candidates, "warnings": warnings}

    async def verify(state: TopicState) -> dict:
        summary = state["expansion"].summary
        warnings = list(state.get("warnings") or [])

        async def one(candidate: Candidate) -> tuple[Candidate, Verification]:
            async with semaphore:
                try:
                    return candidate, await verify_candidate(llm, state["topic"], summary, candidate, settings)
                except Exception as exc:  # noqa: BLE001 — one failed verification must not fail the topic
                    logger.warning("Verification failed for %s: %s", candidate.person_uid, exc)
                    return candidate, Verification([], "none", "", error=str(exc))

        verified = list(await asyncio.gather(*(one(c) for c in state["candidates"])))
        failures = [c.person_uid for c, v in verified if v.error]
        if failures:
            warnings.append(f"Verification failed for {len(failures)} candidate(s): {', '.join(failures[:5])}")
        return {"verified": verified, "warnings": warnings}

    async def score(state: TopicState) -> dict:
        retained = [(c, v) for c, v in state["verified"] if is_retained(v)]
        units = {}
        for candidate, _ in retained:
            try:
                units[candidate.person_uid] = await source.memberships(candidate.person_uid)
            except Exception as exc:  # noqa: BLE001 — units are informative only
                logger.warning("Memberships lookup failed for %s: %s", candidate.person_uid, exc)
                units[candidate.person_uid] = []
        result = _result_shell(state["topic"])
        result.summary = state["expansion"].summary
        result.queries = state["expansion"].queries
        result.publications_retrieved = len(state["publications"])
        result.candidates_considered = len(state["candidates"])
        result.candidates_verified = sum(1 for _, v in state["verified"] if not v.error)
        result.researchers = final_matches(result.topic_id, retained, units, settings)
        result.warnings = list(state.get("warnings") or [])
        return {"result": result}

    graph = StateGraph(TopicState)
    graph.add_node("expand", expand)
    graph.add_node("retrieve", retrieve)
    graph.add_node("verify", verify)
    graph.add_node("score", score)
    graph.set_entry_point("expand")
    graph.add_edge("expand", "retrieve")
    graph.add_edge("retrieve", "verify")
    graph.add_edge("verify", "score")
    graph.add_edge("score", END)
    return graph.compile()


async def run_topic(graph: CompiledStateGraph, topic: dict) -> TopicResult:
    started = time.monotonic()
    try:
        state = await graph.ainvoke({"topic": topic})
        result = state["result"]
    except Exception as exc:  # noqa: BLE001 — a topic failure is recorded, never propagated
        logger.exception("Topic %s failed", topic.get("topic_id"))
        result = _result_shell(topic)
        result.error = f"{type(exc).__name__}: {exc}"
    result.duration_seconds = round(time.monotonic() - started, 1)
    return result
