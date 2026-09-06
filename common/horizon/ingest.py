"""Idempotent, resumable bulk ingestion of Horizon WP topics into OpenSearch.

The index mirrors the WP directory: every run stamps the topics it finds with a ``run_id``; at the end of a
complete run over the whole directory, documents carrying an older ``run_id`` (topics of a previous programme,
or of a file that was removed) are deleted. A topic is re-embedded only when its source file, the parser
version or the embedding model changed; otherwise only its ``run_id`` is refreshed.
"""

import asyncio
import hashlib
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from common.embedding import EmbeddingProvider, EmbeddingServiceError
from common.horizon.mapping import SEARCH_PIPELINES, index_body
from common.horizon.models import ParseIssue, ParseResult, Topic
from common.horizon.wp_parser import PARSER_VERSION, parse_pages, read_pdf_pages

logger = logging.getLogger(__name__)

BULK_CHUNK = 50


@dataclass
class FileReport:
    file: str
    sha256: str = ""
    parsed: int = 0
    indexed: int = 0
    unchanged: int = 0
    failed: int = 0
    skipped: int = 0
    warnings: int = 0
    errors: int = 0


@dataclass
class IngestReport:
    run_id: str
    index: str
    started_at: str
    dry_run: bool
    files: list[FileReport] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    embedding_failures: list[dict] = field(default_factory=list)
    clusters: dict[str, int] = field(default_factory=dict)
    duration_seconds: float = 0.0
    complete: bool = False

    @property
    def ok(self) -> bool:
        return not any(f.errors for f in self.files) and not self.embedding_failures

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        lines = [f"Run {self.run_id} on index {self.index}{' (dry run)' if self.dry_run else ''}"]
        for f in self.files:
            lines.append(
                f"  {f.file}: parsed={f.parsed} indexed={f.indexed} unchanged={f.unchanged} failed={f.failed} "
                f"skipped={f.skipped} warnings={f.warnings} errors={f.errors}"
            )
        if self.removed:
            lines.append(f"  removed {len(self.removed)} stale topic(s): {', '.join(self.removed[:10])}"
                         + (" …" if len(self.removed) > 10 else ""))
        if self.embedding_failures:
            lines.append(f"  embedding failures: {len(self.embedding_failures)}")
        if self.clusters:
            lines.append("  index: " + ", ".join(f"{c}={n}" for c, n in sorted(self.clusters.items())))
        lines.append(f"  duration: {self.duration_seconds:.1f}s, complete={self.complete}, ok={self.ok}")
        return "\n".join(lines)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _issue_dict(issue: ParseIssue) -> dict:
    return asdict(issue)


class HorizonIngester:
    def __init__(
        self,
        client,
        embedder: EmbeddingProvider | None,
        index: str,
        *,
        embedding_model: str = "",
        dimensions: int | None = None,
        embedding_concurrency: int = 2,
    ):
        self.client = client
        self.embedder = embedder
        self.index = index
        self.embedding_model = embedding_model
        self.dimensions = dimensions
        self._semaphore = asyncio.Semaphore(embedding_concurrency)

    # ------------------------------------------------------------------ index management

    async def ensure_index(self, recreate: bool = False) -> None:
        exists = await self.client.indices.exists(index=self.index)
        if exists and recreate:
            await self.client.indices.delete(index=self.index)
            exists = False
        if not exists:
            await self.client.indices.create(index=self.index, body=index_body(self.dimensions))
        for name, body in SEARCH_PIPELINES.items():
            await self.client.search_pipeline.put(id=name, body=body())

    # ------------------------------------------------------------------ run

    async def run(
        self,
        wp_dir: Path,
        *,
        files: list[Path] | None = None,
        recreate: bool = False,
        dry_run: bool = False,
    ) -> IngestReport:
        started = datetime.now(timezone.utc)
        run_id = started.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
        report = IngestReport(run_id=run_id, index=self.index, started_at=started.isoformat(), dry_run=dry_run)
        selected = sorted(files) if files else sorted(wp_dir.glob("*.pdf"))
        full_run = not files

        if not dry_run:
            await self.ensure_index(recreate=recreate)

        for path in selected:
            file_report = FileReport(file=path.name)
            report.files.append(file_report)
            try:
                file_report.sha256 = _sha256(path)
                result = parse_pages(read_pdf_pages(path), path.name)
            except Exception as exc:  # noqa: BLE001 — one unreadable file must not stop the run
                file_report.errors += 1
                report.issues.append({"level": "error", "message": f"Cannot parse file: {exc}",
                                      "source_file": path.name, "page": None, "topic_id": None})
                continue
            self._record_issues(result, file_report, report)
            file_report.parsed = len(result.topics)
            if dry_run:
                continue
            await self._index_topics(result.topics, file_report, report, run_id)

        if not dry_run:
            if full_run and not report.embedding_failures and not any(f.errors for f in report.files):
                report.removed = await self._sweep(run_id)
                report.complete = True
            await self.client.indices.refresh(index=self.index)
            report.clusters = await self._cluster_counts()
        else:
            report.complete = full_run
        report.duration_seconds = (datetime.now(timezone.utc) - started).total_seconds()
        return report

    @staticmethod
    def _record_issues(result: ParseResult, file_report: FileReport, report: IngestReport) -> None:
        for issue in result.issues:
            report.issues.append(_issue_dict(issue))
            if issue.level == "error":
                file_report.errors += 1
                file_report.skipped += 1
            else:
                file_report.warnings += 1

    # ------------------------------------------------------------------ indexing

    async def _index_topics(self, topics: list[Topic], file_report: FileReport, report: IngestReport, run_id: str) -> None:
        stored = await self._stored_versions([t.topic_id for t in topics])
        to_embed: list[Topic] = []
        refresh_only: list[str] = []
        for topic in topics:
            current = stored.get(topic.topic_id)
            if current and current == (file_report.sha256, PARSER_VERSION, self.embedding_model):
                refresh_only.append(topic.topic_id)
            else:
                to_embed.append(topic)

        if refresh_only:
            await self._bulk([{"update": {"_index": self.index, "_id": tid}} for tid in refresh_only],
                             [{"doc": {"run_id": run_id}} for _ in refresh_only])
            file_report.unchanged += len(refresh_only)

        for start in range(0, len(to_embed), BULK_CHUNK):
            chunk = to_embed[start:start + BULK_CHUNK]
            documents = []
            for topic in chunk:
                try:
                    documents.append(await self._document(topic, file_report.sha256, run_id))
                except EmbeddingServiceError as exc:
                    file_report.failed += 1
                    report.embedding_failures.append({"topic_id": topic.topic_id, "error": str(exc)})
            if documents:
                actions = [{"index": {"_index": self.index, "_id": d["topic_id"]}} for d in documents]
                await self._bulk(actions, documents)
                file_report.indexed += len(documents)

    async def _stored_versions(self, topic_ids: list[str]) -> dict[str, tuple[str, str, str]]:
        if not topic_ids:
            return {}
        response = await self.client.mget(
            index=self.index,
            body={"ids": topic_ids},
            params={"_source": "source_sha256,parser_version,embedding_model"},
        )
        result = {}
        for doc in response.get("docs", []):
            if doc.get("found"):
                src = doc.get("_source", {})
                result[doc["_id"]] = (src.get("source_sha256", ""), src.get("parser_version", ""),
                                      src.get("embedding_model", ""))
        return result

    async def _document(self, topic: Topic, sha256: str, run_id: str) -> dict:
        doc = topic.to_document()
        texts = [topic.content] + [f"{topic.title}\n\n{p.text}" for p in topic.passages]
        async with self._semaphore:
            vectors = await self.embedder.embed_texts(texts)
        doc["content_vector"] = vectors[0]
        for passage, vector in zip(doc["passages"], vectors[1:]):
            passage["vector"] = vector
        doc.update(
            source_sha256=sha256,
            parser_version=PARSER_VERSION,
            embedding_model=self.embedding_model,
            run_id=run_id,
            indexed_at=datetime.now(timezone.utc).isoformat(),
        )
        return doc

    async def _bulk(self, actions: list[dict], payloads: list[dict]) -> None:
        body: list[dict] = []
        for action, payload in zip(actions, payloads):
            body.append(action)
            body.append(payload)
        response = await self.client.bulk(body=body, params={"refresh": "false"})
        if response.get("errors"):
            failed = [item for item in response.get("items", []) if list(item.values())[0].get("error")]
            raise RuntimeError(f"Bulk indexing failed for {len(failed)} document(s): {failed[:3]}")

    async def _sweep(self, run_id: str) -> list[str]:
        await self.client.indices.refresh(index=self.index)
        query = {"bool": {"must_not": {"term": {"run_id": run_id}}}}
        response = await self.client.search(
            index=self.index, body={"query": query, "size": 1000, "_source": False}
        )
        stale = [hit["_id"] for hit in response.get("hits", {}).get("hits", [])]
        if stale:
            await self.client.delete_by_query(index=self.index, body={"query": query}, params={"refresh": "true"})
        return stale

    async def _cluster_counts(self) -> dict[str, int]:
        response = await self.client.search(
            index=self.index,
            body={"size": 0, "aggs": {"clusters": {"terms": {"field": "cluster", "size": 50}}}},
        )
        buckets = response.get("aggregations", {}).get("clusters", {}).get("buckets", [])
        return {b["key"]: b["doc_count"] for b in buckets}
