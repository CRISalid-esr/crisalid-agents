from agents.sorbobot.domains import DomainExploration
from agents.sorbobot.topics import TopicFinder
from tests.sorbobot_fakes import FakeTaxi, FakeToolbox, FakeTopicJudge, make_settings


def exploration(taxi, toolbox, judge=None, **settings) -> DomainExploration:
    values = make_settings(**settings)
    return DomainExploration(TopicFinder(taxi, toolbox, judge, values), toolbox, values)


async def test_domains_of_a_topic_sorted_by_judge_score_then_similarity():
    toolbox = FakeToolbox(topics={"t1", "t2", "t3", "t4"}, counts={"t4": 0})
    judge = FakeTopicJudge({1: 0.7, 2: 0.9, 3: 0.7})
    result = await exploration(FakeTaxi(["t1", "t2", "t3", "t4"]), toolbox, judge).run("deep learning for images", None)
    assert result["topic"] == "deep learning for images"
    assert [(d["tag"], d["uid"]) for d in result["domains"]] == [("#1", "t2"), ("#2", "t1"), ("#3", "t3")]
    assert result["domains"][0]["name"] == "T2" and result["domains"][0]["path"].endswith("T2 [Topic]")


async def test_display_limit_and_no_domain():
    toolbox = FakeToolbox(topics={"t1", "t2"})
    result = await exploration(FakeTaxi(["t1", "t2"]), toolbox, None, judge_threshold=0, explore_max_domains=1).run("x" * 30, None)
    assert [d["uid"] for d in result["domains"]] == ["t1"]
    judged_out = await exploration(FakeTaxi(["t1"]), toolbox, FakeTopicJudge({1: 0.1})).run("x" * 30, None)
    assert judged_out == {"topic": "x" * 30, "domains": [], "hint": "no_domain"}
    nothing = await exploration(FakeTaxi([]), toolbox, None, judge_threshold=0).run("x" * 30, None)
    assert nothing["hint"] == "no_domain"
    undocumented = await exploration(FakeTaxi(["t1"]), FakeToolbox(topics={"t1"}, counts={"t1": 0}), None, judge_threshold=0).run("x" * 30, None)
    assert undocumented["hint"] == "no_document"


async def test_displayed_names_are_resolved_without_taxi_or_judge():
    taxi, judge = FakeTaxi(["t9"]), FakeTopicJudge({1: 1.0})
    toolbox = FakeToolbox(
        topics={"t1", "t2"},
        names={"Topic Modeling": [{"uid": "t2", "name": "Topic Modeling", "type": "Topic"}],
               "Artificial Intelligence": [{"uid": "sf", "name": "Artificial Intelligence", "type": "SubField"}],
               "Logic, Reasoning, and Knowledge": [{"uid": "t1", "name": "Logic, Reasoning, and Knowledge", "type": "Topic"}]},
    )
    result = await exploration(taxi, toolbox, judge).run("ignored topic text of enough length", [
        "Topic Modeling", "Unknown Name", "Artificial Intelligence", " Logic, Reasoning, and Knowledge ", "",
    ])
    assert result["topic"] is None
    assert [(d["tag"], d["uid"]) for d in result["domains"]] == [("#1", "t2"), ("#2", "t1")]
    assert taxi.queries == [] and judge.calls == []
    nothing = await exploration(taxi, toolbox, judge).run(None, ["Unknown Name"])
    assert nothing == {"topic": None, "domains": [], "hint": "no_domain"}
