from agents.sorbobot.domains import explore_domains
from tests.sorbobot_fakes import FakeGraph, FakeJudge, FakeTaxi, make_services


async def test_below_the_threshold_domains_are_kept_at_0_5_and_ranked_by_relevance():
    judge = FakeJudge(scores={1: 0.55, 2: 0.6, 3: 0.1})

    result = await explore_domains(make_services(taxi=FakeTaxi(["t1", "t2", "t3"]), concept_judge=judge), "vision")

    assert [(d.tag, d.uid, d.name, d.definition) for d in result.domains] == [
        ("#1", "t2", "T2", "About t2."),
        ("#2", "t1", "T1", "About t1."),
    ]


async def test_without_judgement_documented_domains_come_by_similarity():
    graph = FakeGraph(counts={"t1": 0})

    result = await explore_domains(make_services(graph, FakeTaxi(["t1", "t2", "t3"]), explore_max_domains=1), "vision")

    assert [domain.uid for domain in result.domains] == ["t2"]


async def test_a_topic_the_judge_skipped_is_left_out_when_judged_topics_answer_the_question():
    judge = FakeJudge(scores={1: 0.8, 3: 0.95})
    services = make_services(FakeGraph(), FakeTaxi(["t1", "t2", "t3"]), concept_judge=judge)

    result = await explore_domains(services, "machine learning")

    assert [domain.uid for domain in result.domains] == ["t3", "t1"]


async def test_topics_the_judge_skipped_come_back_when_no_judged_topic_reaches_the_bar():
    judge = FakeJudge(scores={1: 0.2})
    services = make_services(FakeGraph(), FakeTaxi(["t1", "t2"]), concept_judge=judge)

    result = await explore_domains(services, "machine learning")

    assert [domain.uid for domain in result.domains] == ["t2"]
