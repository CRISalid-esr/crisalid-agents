import pytest

from agents.sorbobot.experts import ExpertSearch
from agents.sorbobot.topics import TopicFinder
from tests.sorbobot_fakes import FakeTaxi, FakeToolbox, FakeTopicJudge, evidence, make_settings


def search(taxi, toolbox, judge=None, **settings) -> ExpertSearch:
    values = make_settings(**settings)
    finder = TopicFinder(taxi, toolbox, judge, values)
    return ExpertSearch(finder, toolbox, values)


TOPIC = "machine learning for medical imaging"


async def test_experts_domains_tags_and_recent_publications():
    toolbox = FakeToolbox(
        topics={"t1", "t2", "t3"}, counts={"t3": 0},
        evidence=[
            evidence("alice", "d1", "t1", 0.9, "2025-01-01"), evidence("alice", "d2", "t2", 0.8, "2019-01-01"),
            evidence("alice", "d3", "t1", 0.7, "2022-06-01"), evidence("bob", "d4", "t1", 0.6, "2010-01-01"),
        ],
        totals={"alice": 5, "bob": 40},
    )
    judge = FakeTopicJudge({1: 0.9, 2: 0.7})
    result = await search(FakeTaxi(["t1", "t2", "t3"]), toolbox, judge).run(TOPIC)

    assert result["topic"] == TOPIC and result["ranking_method"] == "weighted_relevance" and "hint" not in result
    assert [d["tag"] for d in result["domains"]] == ["#1", "#2"]
    assert result["domains"][0] == {
        "tag": "#1", "uid": "t1", "name": "T1", "path": "Science [Domain] > Field [Field] > Sub [SubField] > T1 [Topic]",
        "nb_publications": 2, "definition": result["domains"][0]["definition"],
    }
    assert result["domains"][0]["definition"].endswith("…") and len(result["domains"][0]["definition"]) <= 301
    # t3 has no publication: judged out before the judge, which saw only two paths.
    assert judge.calls == [(TOPIC, [toolbox_path("t1"), toolbox_path("t2")])]
    alice, bob = result["experts"]
    assert alice["name"] == "Alice" and alice["score"] == 100 and alice["tags"] == ["#1", "#2"]
    assert alice["nb_publications_on_topic"] == 3 and alice["nb_publications_total"] == 5
    assert alice["recent_publications"] == [{"title": "Title of d1", "year": 2025}, {"title": "Title of d3", "year": 2022}]
    assert bob["tags"] == ["#1"] and 0 <= bob["score"] < alice["score"]
    # Two researchers: no widening to siblings.
    assert not any(call[0] == "list_siblings" for call in toolbox.calls)


def toolbox_path(uid: str) -> str:
    return f"Science [Domain] > Field [Field] > Sub [SubField] > {uid.upper()} [Topic]"


async def test_judge_threshold_and_unscored_topics():
    toolbox = FakeToolbox(topics={"t1", "t2", "t3"}, evidence=[evidence("a", "d1", "t1"), evidence("b", "d2", "t2"),
                                                                 evidence("c", "d3", "t3")])
    judge = FakeTopicJudge({1: 0.95, 2: 0.59})  # t3 unscored
    result = await search(FakeTaxi(["t1", "t2", "t3"]), toolbox, judge).run(TOPIC)
    assert [d["uid"] for d in result["domains"]] == ["t1"]
    assert [e["person_uid"] for e in result["experts"]] == ["a"]


async def test_a_failing_judge_keeps_every_documented_topic_and_adds_no_sibling():
    toolbox = FakeToolbox(topics={"t1", "t2", "s1"}, siblings={"t1": ["s1"]},
                          evidence=[evidence("a", "d1", "t1"), evidence("x", "d9", "s1")])
    judge = FakeTopicJudge(None)
    result = await search(FakeTaxi(["t1", "t2"]), toolbox, judge).run(TOPIC)
    assert [d["uid"] for d in result["domains"]] == ["t1", "t2"]
    assert [e["person_uid"] for e in result["experts"]] == ["a"]
    assert judge.calls[0][1] == [toolbox_path("t1"), toolbox_path("t2")]
    assert len(judge.calls) == 2 and judge.calls[1][1] == [toolbox_path("s1")]  # siblings judged, judgement failed


async def test_topics_without_publications_give_no_document():
    toolbox = FakeToolbox(topics={"t1"}, counts={"t1": 0})
    result = await search(FakeTaxi(["t1"]), toolbox, FakeTopicJudge({1: 1.0})).run(TOPIC)
    assert result == {"topic": TOPIC, "experts": [], "hint": "no_document"}


async def test_no_match_or_no_evidence_gives_no_expert():
    assert (await search(FakeTaxi([]), FakeToolbox(), FakeTopicJudge({})).run(TOPIC))["hint"] == "no_expert"
    result = await search(FakeTaxi(["t1"]), FakeToolbox(topics={"t1"}), FakeTopicJudge({1: 0.9})).run(TOPIC)
    assert result == {"topic": TOPIC, "experts": [], "hint": "no_expert"}
    judged_out = await search(FakeTaxi(["t1"]), FakeToolbox(topics={"t1"}, evidence=[evidence("a", "d", "t1")]),
                              FakeTopicJudge({1: 0.1})).run(TOPIC)
    assert judged_out["hint"] == "no_expert"


async def test_siblings_are_added_when_too_few_researchers():
    toolbox = FakeToolbox(
        topics={"t1", "s1", "s2", "s3"}, counts={"s1": 5, "s2": 9, "s3": 0}, siblings={"t1": ["s1", "s2", "s3"]},
        evidence=[evidence("a", "d1", "t1", 0.9), evidence("b", "d2", "s1", 0.8), evidence("c", "d3", "s2", 0.8)],
    )
    judge = FakeTopicJudge({1: 0.9}, {1: 0.4, 2: 0.8})  # siblings listed richest first: s2 (9), s1 (5)
    result = await search(FakeTaxi(["t1"]), toolbox, judge, min_authors=2).run(TOPIC)
    assert judge.calls[1][1] == [toolbox_path("s2"), toolbox_path("s1")]
    assert [d["uid"] for d in result["domains"]] == ["t1", "s1"]
    assert sorted(e["person_uid"] for e in result["experts"]) == ["a", "b"]
    evidence_calls = [call for call in toolbox.calls if call[0] == "list_expert_evidence"]
    assert evidence_calls == [("list_expert_evidence", ["t1"], 0.57), ("list_expert_evidence", ["s1", "t1"], 0.57)]


async def test_refused_siblings_change_nothing():
    toolbox = FakeToolbox(topics={"t1", "s1"}, siblings={"t1": ["s1"]},
                          evidence=[evidence("a", "d1", "t1"), evidence("b", "d2", "s1")])
    result = await search(FakeTaxi(["t1"]), toolbox, FakeTopicJudge({1: 0.9}, {1: 0.2})).run(TOPIC)
    assert [d["uid"] for d in result["domains"]] == ["t1"] and [e["person_uid"] for e in result["experts"]] == ["a"]


async def test_disabled_judge_keeps_every_topic_and_every_documented_sibling():
    toolbox = FakeToolbox(topics={"t1", "s1", "s2"}, counts={"s2": 0}, siblings={"t1": ["s1", "s2"]},
                          evidence=[evidence("a", "d1", "t1"), evidence("b", "d2", "s1")])
    result = await search(FakeTaxi(["t1"]), toolbox, None, judge_threshold=0).run(TOPIC)
    assert [d["uid"] for d in result["domains"]] == ["t1", "s1"]
    assert sorted(e["person_uid"] for e in result["experts"]) == ["a", "b"]


async def test_sibling_cap_and_display_limits():
    toolbox = FakeToolbox(topics={"t1", "s1", "s2"}, counts={"s1": 3, "s2": 8}, siblings={"t1": ["s1", "s2"]},
                          evidence=[evidence("a", "d1", "t1"), evidence("b", "d2", "s2"), evidence("c", "d3", "s1")])
    judge = FakeTopicJudge({1: 0.9}, {1: 0.9})
    result = await search(FakeTaxi(["t1"]), toolbox, judge, max_judged_siblings=1, show_max_domains=1, show_max_authors=1).run(TOPIC)
    assert judge.calls[1][1] == [toolbox_path("s2")]
    assert [d["uid"] for d in result["domains"]] == ["t1"] and len(result["experts"]) == 1
    assert result["experts"][0]["tags"] == ["#1"] or result["experts"][0]["tags"] == []


async def test_count_ranking_method():
    toolbox = FakeToolbox(topics={"t1"}, evidence=[evidence("a", "d1", "t1"), evidence("a", "d2", "t1"), evidence("b", "d3", "t1")])
    result = await search(FakeTaxi(["t1"]), toolbox, None, judge_threshold=0, ranking_method="count").run(TOPIC)
    assert result["ranking_method"] == "count"
    assert [(e["person_uid"], e["score"]) for e in result["experts"]] == [("a", 100), ("b", 50)]


async def test_taxi_errors_propagate():
    from agents.sorbobot.taxi import TaxiUnavailableError

    with pytest.raises(TaxiUnavailableError):
        await search(FakeTaxi(unavailable=True), FakeToolbox(), None, judge_threshold=0).run(TOPIC)
