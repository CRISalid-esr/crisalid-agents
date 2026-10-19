from datetime import datetime

import pytest

from agents.sorbobot.ranking import DocumentSignal, ResearcherSignals, build_signals, score_researchers
from tests.sorbobot_fakes import evidence

THIS_YEAR = datetime.now().year


def researcher(uid: str, years: list[int], total: int) -> ResearcherSignals:
    documents = tuple(DocumentSignal(f"{uid}{i}", f"Title {i}", year, (("t1", 0.8),)) for i, year in enumerate(years))
    return ResearcherSignals(uid, uid, documents, total)


def test_build_signals_groups_the_topic_links_of_each_document():
    rows = [
        evidence("alice", "d1", "t1", similarity=0.9, date="2020"),
        evidence("alice", "d1", "t2", similarity=0.7, date="2020"),
        evidence("alice", "d2", "t1", date="2021"),
    ]

    [alice] = build_signals(rows, total_counts={"alice": 10})

    assert [document.document_uid for document in alice.documents] == ["d1", "d2"]
    assert dict(alice.documents[0].topic_links) == {"t1": 0.9, "t2": 0.7}
    assert alice.total_publications == 10


def test_count_scores_relative_to_the_most_published():
    pool = [researcher("alice", [THIS_YEAR, THIS_YEAR], 2), researcher("bob", [THIS_YEAR], 1)]

    assert score_researchers(pool, {"t1": 0.9}, "count") == {"alice": 100.0, "bob": 50.0}
    with pytest.raises(ValueError, match="Unknown ranking method"):
        score_researchers(pool, {}, "popularity")


def test_weighted_relevance_prefers_focused_and_recent_work():
    specialist = researcher("specialist", [THIS_YEAR, THIS_YEAR], total=2)
    generalist = researcher("generalist", [THIS_YEAR, THIS_YEAR], total=52)
    veteran = researcher("veteran", [THIS_YEAR - 20, THIS_YEAR - 20], total=2)

    scores = score_researchers([specialist, generalist, veteran], {"t1": 0.9}, "weighted_relevance")

    assert scores["specialist"] == pytest.approx(100.0)
    assert scores["generalist"] < 100.0
    assert scores["veteran"] < 100.0
