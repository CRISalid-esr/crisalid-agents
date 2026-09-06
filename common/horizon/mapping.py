"""OpenSearch index mapping and search pipelines of the Horizon topic index."""

import os

DEFAULT_INDEX = "horizon-topics"
RRF_PIPELINE = "horizon-hybrid-rrf"
MINMAX_PIPELINE = "horizon-hybrid-minmax"
RRF_RANK_CONSTANT = 60


def embedding_dimensions() -> int:
    return int(os.environ.get("EMBEDDING_DIMENSIONS", "1024"))


def _vector(dimensions: int) -> dict:
    return {
        "type": "knn_vector",
        "dimension": dimensions,
        "method": {"name": "hnsw", "space_type": "cosinesimil", "engine": "lucene"},
    }


def index_body(dimensions: int | None = None) -> dict:
    dims = dimensions or embedding_dimensions()
    text = {"type": "text", "analyzer": "english"}
    return {
        "settings": {"index": {"knn": True, "number_of_shards": 1, "number_of_replicas": 0}},
        "mappings": {
            "dynamic": "strict",
            "properties": {
                "topic_id": {"type": "keyword"},
                "call_id": {"type": "keyword"},
                "call_name": {"type": "keyword"},
                "cluster": {"type": "keyword"},
                "cluster_label": {"type": "keyword"},
                "work_programme": {"type": "keyword"},
                "part": {"type": "integer"},
                "destination": {"type": "text", "fields": {"keyword": {"type": "keyword", "ignore_above": 512}}},
                "title": text,
                "type_of_action": {"type": "keyword"},
                "type_of_action_label": {"type": "keyword"},
                "stage": {"type": "keyword"},
                "contribution_min_eur": {"type": "long"},
                "contribution_max_eur": {"type": "long"},
                "indicative_budget_eur": {"type": "long"},
                "expected_projects": {"type": "integer"},
                "opening_date": {"type": "date"},
                "deadlines": {"type": "date"},
                "trl": {"type": "text"},
                "conditions_text": {"type": "text", "index": False},
                "expected_outcome": {"type": "text"},
                "scope": {"type": "text"},
                "content": text,
                "content_vector": _vector(dims),
                "passages": {
                    "type": "nested",
                    "properties": {
                        "section": {"type": "keyword"},
                        "order": {"type": "integer"},
                        "text": {"type": "text"},
                        "vector": _vector(dims),
                    },
                },
                "source_file": {"type": "keyword"},
                "source_pages": {"type": "integer"},
                "source_sha256": {"type": "keyword"},
                "parser_version": {"type": "keyword"},
                "embedding_model": {"type": "keyword"},
                "run_id": {"type": "keyword"},
                "indexed_at": {"type": "date"},
            },
        },
    }


def rrf_pipeline_body() -> dict:
    return {
        "description": "Hybrid BM25 + vector search over Horizon topics, reciprocal rank fusion",
        "phase_results_processors": [
            {
                "score-ranker-processor": {
                    "combination": {"technique": "rrf", "rank_constant": RRF_RANK_CONSTANT},
                }
            }
        ],
    }


def minmax_pipeline_body() -> dict:
    return {
        "description": "Hybrid BM25 + vector search over Horizon topics, min-max normalisation",
        "phase_results_processors": [
            {
                "normalization-processor": {
                    "normalization": {"technique": "min_max"},
                    "combination": {"technique": "arithmetic_mean"},
                }
            }
        ],
    }


SEARCH_PIPELINES = {RRF_PIPELINE: rrf_pipeline_body, MINMAX_PIPELINE: minmax_pipeline_body}
