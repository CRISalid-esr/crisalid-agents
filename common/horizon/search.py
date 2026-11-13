"""Hybrid search over the Horizon topic index.

One ``hybrid`` query with three sub-queries — BM25 on title and content, k-NN on the whole-topic vector, and
k-NN on the nested passage vectors — fused by the index's search pipeline (reciprocal rank fusion by default).
``search_topics_multi`` runs several queries and fuses their rankings client-side, so a topic hit by several
queries ranks first.
"""

from collections import defaultdict
from dataclasses import dataclass

from common.embedding import EmbeddingProvider
from common.horizon.client import HorizonSettings
from common.horizon.mapping import RRF_RANK_CONSTANT
from common.horizon.models import TopicHit

SOURCE_FIELDS = [
    "topic_id", "title", "call_id", "call_name", "cluster", "destination", "type_of_action", "deadlines",
    "opening_date", "contribution_min_eur", "contribution_max_eur", "indicative_budget_eur",
]
FULL_FIELDS = SOURCE_FIELDS + ["cluster_label", "work_programme", "stage", "expected_projects", "trl",
                               "expected_outcome", "scope", "conditions_text", "type_of_action_label"]


def _filters(cluster: str | None, type_of_action: str | None) -> list[dict]:
    filters = []
    if cluster:
        filters.append({"term": {"cluster": cluster}})
    if type_of_action:
        filters.append({"term": {"type_of_action": type_of_action}})
    return filters


def hybrid_query(query: str, vector: list[float], *, top_k: int, cluster: str | None = None,
                 type_of_action: str | None = None) -> dict:
    filters = _filters(cluster, type_of_action)
    k = top_k * 4
    bm25 = {"multi_match": {"query": query, "fields": ["title^3", "content"]}}
    knn_content = {"knn": {"content_vector": {"vector": vector, "k": k}}}
    knn_passages = {
        "nested": {
            "path": "passages",
            "score_mode": "max",
            "query": {"knn": {"passages.vector": {"vector": vector, "k": k}}},
        }
    }
    if filters:
        bm25 = {"bool": {"must": [bm25], "filter": filters}}
        knn_content["knn"]["content_vector"]["filter"] = {"bool": {"filter": filters}}
        knn_passages = {"bool": {"must": [knn_passages], "filter": filters}}
    return {
        "size": top_k,
        "_source": SOURCE_FIELDS,
        "query": {"hybrid": {"queries": [bm25, knn_content, knn_passages]}},
    }


def best_passage_query(vector: list[float], topic_ids: list[str]) -> dict:
    """Best-matching passage of each given topic (the hybrid query does not return nested inner hits)."""
    return {
        "size": len(topic_ids),
        "_source": False,
        "query": {
            "bool": {
                "filter": [{"ids": {"values": topic_ids}}],
                "must": [{
                    "nested": {
                        "path": "passages",
                        "score_mode": "max",
                        "query": {"knn": {"passages.vector": {"vector": vector, "k": max(len(topic_ids) * 4, 10)}}},
                        "inner_hits": {"size": 1, "_source": ["passages.section", "passages.text"]},
                    }
                }],
            }
        },
    }


def best_passages(response: dict) -> dict[str, tuple[str | None, str | None]]:
    result = {}
    for hit in response.get("hits", {}).get("hits", []):
        passages = hit.get("inner_hits", {}).get("passages", {}).get("hits", {}).get("hits", [])
        if passages:
            p = passages[0].get("_source", {})
            result[hit["_id"]] = (p.get("section"), p.get("text"))
    return result


def hit_to_topic_hit(hit: dict, passage: tuple[str | None, str | None] = (None, None)) -> TopicHit:
    src = hit.get("_source", {})
    best_section, best = passage
    return TopicHit(
        topic_id=src.get("topic_id", hit.get("_id")),
        score=float(hit.get("_score") or 0.0),
        title=src.get("title", ""),
        call_id=src.get("call_id"),
        call_name=src.get("call_name"),
        cluster=src.get("cluster", ""),
        destination=src.get("destination"),
        type_of_action=src.get("type_of_action"),
        deadlines=list(src.get("deadlines") or []),
        opening_date=src.get("opening_date"),
        contribution_min_eur=src.get("contribution_min_eur"),
        contribution_max_eur=src.get("contribution_max_eur"),
        indicative_budget_eur=src.get("indicative_budget_eur"),
        best_passage=best,
        best_passage_section=best_section,
    )


def rrf_fuse(rankings: dict[str, list[TopicHit]], *, rank_constant: int = RRF_RANK_CONSTANT,
             top_k: int | None = None) -> list[TopicHit]:
    """Fuse per-query rankings with reciprocal rank fusion; ``query_ranks`` records each query's rank."""
    scores: dict[str, float] = defaultdict(float)
    hits: dict[str, TopicHit] = {}
    ranks: dict[str, dict[str, int]] = defaultdict(dict)
    for query, ranking in rankings.items():
        for rank, hit in enumerate(ranking, start=1):
            scores[hit.topic_id] += 1.0 / (rank_constant + rank)
            ranks[hit.topic_id][query] = rank
            if hit.topic_id not in hits or hit.score > hits[hit.topic_id].score:
                hits[hit.topic_id] = hit
    fused = []
    for topic_id, score in sorted(scores.items(), key=lambda item: -item[1]):
        hit = hits[topic_id]
        fused.append(TopicHit(**{**hit.__dict__, "score": score, "query_ranks": dict(ranks[topic_id])}))
    return fused[:top_k] if top_k else fused


@dataclass
class HorizonSearch:
    client: object
    embedder: EmbeddingProvider
    settings: HorizonSettings

    async def search_topics(self, query: str, *, cluster: str | None = None, type_of_action: str | None = None,
                            top_k: int = 10) -> list[TopicHit]:
        vector = await self.embedder.embed_text(query)
        body = hybrid_query(query, vector, top_k=top_k, cluster=cluster, type_of_action=type_of_action)
        response = await self.client.search(
            index=self.settings.index, body=body, params={"search_pipeline": self.settings.search_pipeline}
        )
        hits = response.get("hits", {}).get("hits", [])
        passages: dict[str, tuple[str | None, str | None]] = {}
        if hits:
            ids = [hit["_id"] for hit in hits]
            passages = best_passages(await self.client.search(index=self.settings.index, body=best_passage_query(vector, ids)))
        return [hit_to_topic_hit(hit, passages.get(hit["_id"], (None, None))) for hit in hits]

    async def search_topics_multi(self, queries: list[str], *, cluster: str | None = None,
                                  type_of_action: str | None = None, top_k: int = 10) -> list[TopicHit]:
        rankings = {}
        for query in queries:
            rankings[query] = await self.search_topics(query, cluster=cluster, type_of_action=type_of_action,
                                                       top_k=top_k)
        return rrf_fuse(rankings, top_k=top_k)

    async def get_topic(self, topic_id: str) -> dict | None:
        response = await self.client.get(index=self.settings.index, id=topic_id,
                                         params={"_source": ",".join(FULL_FIELDS)})
        if not response.get("found"):
            return None
        return response.get("_source")

    async def list_clusters(self) -> list[dict]:
        body = {
            "size": 0,
            "aggs": {"clusters": {"terms": {"field": "cluster", "size": 50, "order": {"_key": "asc"}},
                                  "aggs": {"label": {"terms": {"field": "cluster_label", "size": 1}}}}},
        }
        response = await self.client.search(index=self.settings.index, body=body)
        buckets = response.get("aggregations", {}).get("clusters", {}).get("buckets", [])
        return [
            {"cluster": b["key"], "topics": b["doc_count"],
             "label": (b.get("label", {}).get("buckets") or [{"key": ""}])[0]["key"]}
            for b in buckets
        ]

    async def iter_topics(self, cluster: str | None = None, page_size: int = 100):
        body = {
            "size": page_size,
            "_source": FULL_FIELDS,
            "sort": [{"topic_id": "asc"}],
            "query": {"term": {"cluster": cluster}} if cluster else {"match_all": {}},
        }
        search_after = None
        while True:
            if search_after:
                body["search_after"] = search_after
            response = await self.client.search(index=self.settings.index, body=body)
            hits = response.get("hits", {}).get("hits", [])
            if not hits:
                return
            for hit in hits:
                yield hit["_source"]
            search_after = hits[-1].get("sort")
            if len(hits) < page_size:
                return
