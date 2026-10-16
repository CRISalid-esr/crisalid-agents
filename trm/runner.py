"""Run the TRM pipeline over the topics of one or several clusters and write the reports."""

import json
import logging
import uuid
from dataclasses import dataclass
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


@dataclass
class TopicsFile:
    """Topics given as a file instead of the OpenSearch index: the calls of any programme, exported by Druid
    (`export_calls.cjs`, format ``druid-calls/1``) or written by hand, in the fields of ``Topic.to_document``."""

    label: str  # becomes the ``cluster`` of the run, hence the stem of its reports
    programme_label: str | None
    topics: list[dict]


def load_topics_file(path: Path) -> TopicsFile:
    """Reads a topics file: an object ``{subset, programme_label, topics: [...]}`` or a bare list of topics. A topic
    needs a ``topic_id`` and a ``title``; its ``cluster`` defaults to the label of the file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    meta = data if isinstance(data, dict) else {}
    rows = meta.get("topics") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise ValueError(f"{path}: no topic list")
    label = str(meta.get("subset") or meta.get("cluster") or path.stem)
    topics = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or not row.get("topic_id") or not row.get("title"):
            raise ValueError(f"{path}: topic {index} needs a topic_id and a title")
        topics.append({**row, "cluster": row.get("cluster") or label, "deadlines": list(row.get("deadlines") or [])})
    return TopicsFile(label=label, programme_label=meta.get("programme_label") or None, topics=topics)


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
