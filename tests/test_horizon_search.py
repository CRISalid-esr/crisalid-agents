from common.horizon.client import HorizonSettings
from common.horizon.models import TopicHit
from common.horizon.search import (
    HorizonSearch, best_passage_query, best_passages, hit_to_topic_hit, hybrid_query, rrf_fuse,
)

VECTOR = [0.1, 0.2]


def _hit(topic_id: str, score: float = 1.0, **fields) -> dict:
    src = {"topic_id": topic_id, "title": f"Title {topic_id}", "cluster": "CL2", "deadlines": ["2026-09-23"]}
    src.update(fields)
    return {"_id": topic_id, "_score": score, "_source": src}


def test_hybrid_query_has_three_sub_queries_and_applies_filters_to_each():
    body = hybrid_query("insects", VECTOR, top_k=5)
    queries = body["query"]["hybrid"]["queries"]
    assert body["size"] == 5 and len(queries) == 3
    assert queries[0] == {"multi_match": {"query": "insects", "fields": ["title^3", "content"]}}
    assert queries[1] == {"knn": {"content_vector": {"vector": VECTOR, "k": 20}}}
    assert queries[2]["nested"]["path"] == "passages" and queries[2]["nested"]["score_mode"] == "max"

    body = hybrid_query("insects", VECTOR, top_k=5, cluster="CL6", type_of_action="RIA")
    queries = body["query"]["hybrid"]["queries"]
    filters = [{"term": {"cluster": "CL6"}}, {"term": {"type_of_action": "RIA"}}]
    assert queries[0]["bool"]["filter"] == filters
    assert queries[1]["knn"]["content_vector"]["filter"] == {"bool": {"filter": filters}}
    assert queries[2]["bool"]["filter"] == filters and "nested" in queries[2]["bool"]["must"][0]


def test_best_passage_query_restricts_to_the_given_ids():
    body = best_passage_query(VECTOR, ["A", "B"])
    assert body["query"]["bool"]["filter"] == [{"ids": {"values": ["A", "B"]}}]
    nested = body["query"]["bool"]["must"][0]["nested"]
    assert nested["inner_hits"]["size"] == 1 and body["size"] == 2
    response = {"hits": {"hits": [
        {"_id": "A", "inner_hits": {"passages": {"hits": {"hits": [{"_source": {"section": "scope", "text": "best"}}]}}}},
        {"_id": "B", "inner_hits": {"passages": {"hits": {"hits": []}}}},
    ]}}
    assert best_passages(response) == {"A": ("scope", "best")}


def test_hit_to_topic_hit():
    hit = hit_to_topic_hit(_hit("X", 0.5, call_id="C", contribution_max_eur=5), ("scope", "text"))
    assert (hit.topic_id, hit.score, hit.call_id, hit.contribution_max_eur) == ("X", 0.5, "C", 5)
    assert (hit.best_passage_section, hit.best_passage) == ("scope", "text")
    assert hit.deadlines == ["2026-09-23"]


def test_rrf_fuse_ranks_topics_hit_by_several_queries_first():
    def hits(*ids):
        return [TopicHit(topic_id=i, score=1.0, title=i, call_id=None, call_name=None, cluster="CL2", destination=None,
                         type_of_action=None, deadlines=[], opening_date=None, contribution_min_eur=None,
                         contribution_max_eur=None, indicative_budget_eur=None) for i in ids]
    fused = rrf_fuse({"q1": hits("A", "B", "C"), "q2": hits("C", "D")}, rank_constant=60, top_k=3)
    # C: ranks 3 and 1; A: rank 1; B and D tie at rank 2 (first seen wins).
    assert [h.topic_id for h in fused] == ["C", "A", "B"]
    assert fused[0].query_ranks == {"q1": 3, "q2": 1}
    assert abs(fused[0].score - (1 / 63 + 1 / 61)) < 1e-9
    assert rrf_fuse({}) == []


class FakeClient:
    def __init__(self):
        self.calls = []

    async def search(self, index, body, params=None):
        self.calls.append((index, body, params))
        if "aggs" in body:
            return {"aggregations": {"clusters": {"buckets": [
                {"key": "CL2", "doc_count": 54, "label": {"buckets": [{"key": "Culture"}]}}]}}}
        if "hybrid" in body["query"]:
            return {"hits": {"hits": [_hit("A", 0.03), _hit("B", 0.02)]}}
        if "ids" in str(body):
            return {"hits": {"hits": [{"_id": "B", "inner_hits": {"passages": {"hits": {"hits": [
                {"_source": {"section": "scope", "text": "B passage"}}]}}}}]}}
        return {"hits": {"hits": []}}

    async def get(self, index, id, params=None):
        return {"found": id == "A", "_source": {"topic_id": id, "scope": "full scope"}}


class FakeEmbedder:
    async def embed_text(self, text):
        return VECTOR


def _search():
    return HorizonSearch(FakeClient(), FakeEmbedder(), HorizonSettings("http://os", "idx", "horizon-hybrid-rrf"))


async def test_search_topics_uses_the_pipeline_and_attaches_best_passages():
    search = _search()
    hits = await search.search_topics("insects", cluster="CL6", top_k=2)
    assert [h.topic_id for h in hits] == ["A", "B"]
    assert hits[1].best_passage == "B passage" and hits[0].best_passage is None
    first_call = search.client.calls[0]
    assert first_call[0] == "idx" and first_call[2] == {"search_pipeline": "horizon-hybrid-rrf"}
    assert search.client.calls[1][1]["query"]["bool"]["filter"] == [{"ids": {"values": ["A", "B"]}}]


async def test_search_topics_multi_fuses_rankings():
    search = _search()
    fused = await search.search_topics_multi(["q1", "q2"], top_k=5)
    assert [h.topic_id for h in fused] == ["A", "B"]
    assert fused[0].query_ranks == {"q1": 1, "q2": 1}


async def test_get_topic_and_list_clusters():
    search = _search()
    assert (await search.get_topic("A"))["scope"] == "full scope"
    assert await search.get_topic("Z") is None
    assert await search.list_clusters() == [{"cluster": "CL2", "topics": 54, "label": "Culture"}]
