"""Run the TRM pipeline over the topics of one or several clusters and write the reports."""

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from common.embedding import EmbeddingProvider
from common.horizon.search import HorizonSearch
from trm.config import TRMSettings
from trm.llm import StructuredLLM
from trm.models import RunResult
from trm.pipeline import build_graph, run_topic
from trm.report import write_reports
from trm.sources import ResearcherSource

logger = logging.getLogger(__name__)


async def select_topics(search: HorizonSearch, cluster: str | None, topic_ids: list[str] | None) -> list[dict]:
    if topic_ids:
        topics = []
        for topic_id in topic_ids:
            topic = await search.get_topic(topic_id)
            if topic is None:
                raise ValueError(f"Unknown topic {topic_id!r}")
            if cluster and topic.get("cluster") != cluster:
                continue
            topics.append(topic)
        return topics
    return [topic async for topic in search.iter_topics(cluster)]


async def run_cluster(
    cluster: str,
    topics: list[dict],
    *,
    llm: StructuredLLM,
    embedder: EmbeddingProvider,
    source: ResearcherSource,
    settings: TRMSettings,
    output_dir: Path | None = None,
    pdf: bool = True,
    progress=None,
) -> tuple[RunResult, dict[str, Path]]:
    started = datetime.now(timezone.utc)
    run = RunResult(
        run_id=started.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6],
        cluster=cluster,
        started_at=started.isoformat(timespec="seconds"),
        settings=settings.to_dict(),
    )
    graph = build_graph(llm, embedder, source, settings)
    for index, topic in enumerate(topics, start=1):
        result = await run_topic(graph, topic)
        run.topics.append(result)
        if progress:
            progress(index, len(topics), result)
    run.duration_seconds = round((datetime.now(timezone.utc) - started).total_seconds(), 1)
    paths = write_reports(run, output_dir or settings.output_dir, pdf=pdf)
    return run, paths
