"""Ingestion against an in-memory fake of the OpenSearch client: idempotence, resumability, stale sweep, report."""

import json
from pathlib import Path

import pytest

from common.embedding import EmbeddingProvider, EmbeddingServiceError
from common.horizon import ingest as ingest_module
from common.horizon.ingest import HorizonIngester
from common.horizon.models import ParseResult, Passage, Topic
from common.horizon.wp_parser import PARSER_VERSION


class FakeEmbedder(EmbeddingProvider):
    def __init__(self, fail_for: set[str] | None = None):
        self.calls = 0
        self.fail_for = fail_for or set()

    async def embed_text(self, text: str) -> list[float]:
        return (await self.embed_texts([text]))[0]

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.calls += len(texts)
        for text in texts:
            if any(marker in text for marker in self.fail_for):
                raise EmbeddingServiceError("boom")
        return [[float(len(text) % 7), 1.0] for text in texts]


class _Indices:
    def __init__(self, client):
        self.client = client

    async def exists(self, index):
        return index in self.client.mappings

    async def create(self, index, body):
        self.client.mappings[index] = body
        self.client.docs.setdefault(index, {})

    async def delete(self, index):
        self.client.mappings.pop(index, None)
        self.client.docs.pop(index, None)

    async def refresh(self, index):
        return None


class _Pipelines:
    def __init__(self, client):
        self.client = client

    async def put(self, id, body):
        self.client.pipelines[id] = body


class FakeOpenSearch:
    """The subset of the async client used by HorizonIngester, over in-memory dicts."""

    def __init__(self):
        self.mappings: dict[str, dict] = {}
        self.pipelines: dict[str, dict] = {}
        self.docs: dict[str, dict[str, dict]] = {}
        self.bulk_calls = 0
        self.indices = _Indices(self)
        self.search_pipeline = _Pipelines(self)

    async def mget(self, index, body, params=None):
        store = self.docs.get(index, {})
        return {"docs": [{"_id": i, "found": i in store, "_source": store.get(i, {})} for i in body["ids"]]}

    async def bulk(self, body, params=None):
        self.bulk_calls += 1
        items = []
        for action, payload in zip(body[::2], body[1::2]):
            (op, meta), = action.items()
            store = self.docs.setdefault(meta["_index"], {})
            if op == "index":
                store[meta["_id"]] = json.loads(json.dumps(payload))
            elif op == "update":
                store[meta["_id"]].update(payload["doc"])
            items.append({op: {"_id": meta["_id"], "status": 200}})
        return {"errors": False, "items": items}

    async def search(self, index, body, params=None):
        docs = self.docs.get(index, {})
        if "aggs" in body:
            counts: dict[str, int] = {}
            for d in docs.values():
                counts[d["cluster"]] = counts.get(d["cluster"], 0) + 1
            return {"aggregations": {"clusters": {"buckets": [{"key": k, "doc_count": v} for k, v in counts.items()]}}}
        run_id = body["query"]["bool"]["must_not"]["term"]["run_id"]
        return {"hits": {"hits": [{"_id": i} for i, d in docs.items() if d.get("run_id") != run_id]}}

    async def delete_by_query(self, index, body, params=None):
        run_id = body["query"]["bool"]["must_not"]["term"]["run_id"]
        store = self.docs[index]
        for i in [i for i, d in store.items() if d.get("run_id") != run_id]:
            del store[i]


def _topic(topic_id: str, cluster: str = "CL2", source_file: str = "wp-5.pdf") -> Topic:
    t = Topic(
        topic_id=topic_id, title=f"Title {topic_id}", cluster=cluster, cluster_label="Label", work_programme="2026-2027",
        part=5, source_file=source_file, source_pages=(1, 2), call_id="HORIZON-CL2-2026-01",
        expected_outcome="Outcome text.", scope="Scope text.",
    )
    t.passages = [Passage("expected_outcome", 0, "Outcome text."), Passage("scope", 1, "Scope text.")]
    return t


@pytest.fixture
def wp_dir(tmp_path, monkeypatch):
    """Two fake PDF files; parsing is stubbed to return fixed topics per file name."""
    (tmp_path / "wp-5-a.pdf").write_bytes(b"file a v1")
    (tmp_path / "wp-6-b.pdf").write_bytes(b"file b v1")
    topics = {
        "wp-5-a.pdf": [_topic("HORIZON-CL2-2026-01-A-01"), _topic("HORIZON-CL2-2026-01-A-02")],
        "wp-6-b.pdf": [_topic("HORIZON-CL3-2026-01-B-01", "CL3", "wp-6-b.pdf")],
    }
    monkeypatch.setattr(ingest_module, "read_pdf_pages", lambda path: [path.name])
    monkeypatch.setattr(
        ingest_module, "parse_pages",
        lambda pages, name: ParseResult(name, 5, "CL2", "Label", "2026-2027", topics[name], []),
    )
    return tmp_path


def _ingester(client, embedder=None):
    return HorizonIngester(client, embedder or FakeEmbedder(), "horizon-topics", embedding_model="bge-m3", dimensions=2)


async def test_first_run_indexes_everything_and_creates_index_and_pipelines(wp_dir):
    client = FakeOpenSearch()
    embedder = FakeEmbedder()
    report = await _ingester(client, embedder).run(wp_dir)
    assert "horizon-topics" in client.mappings
    assert set(client.pipelines) == {"horizon-hybrid-rrf", "horizon-hybrid-minmax"}
    assert [f.indexed for f in report.files] == [2, 1]
    assert embedder.calls == 3 * 3  # content + 2 passages per topic
    doc = client.docs["horizon-topics"]["HORIZON-CL2-2026-01-A-01"]
    assert doc["content_vector"] and doc["passages"][1]["vector"] and doc["run_id"] == report.run_id
    assert doc["parser_version"] == PARSER_VERSION and doc["embedding_model"] == "bge-m3"
    assert report.complete and report.ok and report.clusters == {"CL2": 2, "CL3": 1}


async def test_second_run_is_idempotent(wp_dir):
    client = FakeOpenSearch()
    await _ingester(client).run(wp_dir)
    embedder = FakeEmbedder()
    report = await _ingester(client, embedder).run(wp_dir)
    assert embedder.calls == 0
    assert [f.unchanged for f in report.files] == [2, 1] and [f.indexed for f in report.files] == [0, 0]
    assert all(d["run_id"] == report.run_id for d in client.docs["horizon-topics"].values())
    assert report.removed == []


async def test_changed_file_or_embedding_model_triggers_reindex(wp_dir):
    client = FakeOpenSearch()
    await _ingester(client).run(wp_dir)
    (wp_dir / "wp-5-a.pdf").write_bytes(b"file a v2")
    embedder = FakeEmbedder()
    report = await _ingester(client, embedder).run(wp_dir)
    assert [f.indexed for f in report.files] == [2, 0] and embedder.calls == 6
    embedder = FakeEmbedder()
    other_model = HorizonIngester(client, embedder, "horizon-topics", embedding_model="other", dimensions=2)
    report = await other_model.run(wp_dir)
    assert [f.indexed for f in report.files] == [2, 1]


async def test_stale_topics_are_swept_only_after_a_complete_run(wp_dir):
    client = FakeOpenSearch()
    await _ingester(client).run(wp_dir)
    (wp_dir / "wp-6-b.pdf").unlink()
    # Single-file run: no sweep.
    report = await _ingester(client).run(wp_dir, files=[wp_dir / "wp-5-a.pdf"])
    assert report.removed == [] and not report.complete
    assert "HORIZON-CL3-2026-01-B-01" in client.docs["horizon-topics"]
    # Full run: the topic of the removed file disappears and is reported.
    report = await _ingester(client).run(wp_dir)
    assert report.removed == ["HORIZON-CL3-2026-01-B-01"] and report.complete
    assert set(client.docs["horizon-topics"]) == {"HORIZON-CL2-2026-01-A-01", "HORIZON-CL2-2026-01-A-02"}


async def test_embedding_failure_is_reported_and_blocks_the_sweep(wp_dir):
    client = FakeOpenSearch()
    await _ingester(client).run(wp_dir)
    (wp_dir / "wp-5-a.pdf").write_bytes(b"file a v2")
    report = await _ingester(client, FakeEmbedder(fail_for={"A-02"})).run(wp_dir)
    assert report.files[0].indexed == 1 and report.files[0].failed == 1
    assert report.embedding_failures == [{"topic_id": "HORIZON-CL2-2026-01-A-02", "error": "boom"}]
    assert not report.ok and not report.complete and report.removed == []
    # The failed topic keeps its previous version, so a rerun completes the work.
    assert client.docs["horizon-topics"]["HORIZON-CL2-2026-01-A-02"]["source_sha256"] != report.files[0].sha256
    report = await _ingester(client).run(wp_dir)
    assert report.files[0].indexed == 1 and report.files[0].unchanged == 1 and report.ok


async def test_dry_run_parses_without_a_client(wp_dir):
    ingester = HorizonIngester(None, None, "horizon-topics")
    report = await ingester.run(wp_dir, dry_run=True)
    assert [f.parsed for f in report.files] == [2, 1] and report.dry_run and report.ok
    assert "dry run" in report.summary()
    assert json.dumps(report.to_dict())


async def test_recreate_drops_the_index(wp_dir):
    client = FakeOpenSearch()
    await _ingester(client).run(wp_dir)
    client.docs["horizon-topics"]["STALE"] = {"cluster": "CLX"}
    await _ingester(client).run(wp_dir, recreate=True)
    assert "STALE" not in client.docs["horizon-topics"]
