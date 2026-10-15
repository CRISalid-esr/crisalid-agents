import pytest

from agents.sorbobot.experts import find_domain_experts
from agents.sorbobot.topics import NoDocumentedTopicError
from tests.sorbobot_fakes import FakeGraph, FakeJudge, FakeTaxi, evidence, make_services


async def test_topics_without_documents_are_neither_judged_nor_searched():
    graph = FakeGraph(counts={"t1": 0})
    judge = FakeJudge(scores=None)

    await find_domain_experts(make_services(graph, FakeTaxi(["t1", "t2"]), concept_judge=judge), "vision")

    assert judge.calls == [["Science [Domain] > T2 [Topic]"]]
    assert graph.evidence_requests == [["t2"]]


async def test_topics_matched_without_any_document_are_reported_rather_than_searched():
    graph = FakeGraph(counts={"t1": 0, "t2": 0})

    with pytest.raises(NoDocumentedTopicError):
        await find_domain_experts(make_services(graph, FakeTaxi(["t1", "t2"])), "vision")

    assert graph.evidence_requests == []


async def test_the_judge_keeps_relevant_topics_and_retries_once_at_the_lower_threshold():
    graph = FakeGraph(
        evidence={
            frozenset({"t1"}): [evidence("alice", "d1", "t1")],
            frozenset({"t1", "t2"}): [evidence("alice", "d1", "t1"), evidence("bob", "d2", "t2")],
        }
    )
    judge = FakeJudge(scores={1: 0.9, 2: 0.55, 3: 0.2})
    services = make_services(graph, FakeTaxi(["t1", "t2", "t3"]), concept_judge=judge, min_authors=2)

    result = await find_domain_experts(services, "machine learning")

    assert graph.evidence_requests == [["t1"], ["t1", "t2"]]
    assert {expert.person_uid for expert in result.experts} == {"alice", "bob"}


async def test_the_walk_widens_to_siblings_but_never_to_an_empty_answer():
    graph = FakeGraph(evidence={frozenset({"t1"}): [evidence("alice", "d1", "t1")]}, siblings={"t1": ["t2"]})

    result = await find_domain_experts(make_services(graph, min_authors=2), "machine learning")

    assert graph.evidence_requests == [["t1"], ["t1", "t2"]]
    assert [expert.person_uid for expert in result.experts] == ["alice"]


async def test_the_walk_narrows_to_children_when_researchers_are_too_many():
    many = [evidence(f"p{index}", f"d{index}", "sf") for index in range(4)]
    children = [evidence("p0", "d0", "t1"), evidence("p1", "d1", "t2")]
    graph = FakeGraph(
        evidence={frozenset({"sf"}): many, frozenset({"t1", "t2"}): children},
        children={"sf": ["t1", "t2"]},
    )

    result = await find_domain_experts(make_services(graph, FakeTaxi(["sf"]), max_authors=3), "artificial intelligence")

    assert graph.evidence_requests == [["sf"], ["t1", "t2"]]
    assert len(result.experts) == 2
    # The walk left the judged topic: the children it used are shown, and the experts are tagged with them.
    assert [domain.uid for domain in result.domains] == ["t1", "t2"]
    assert {expert.person_uid: expert.tags for expert in result.experts} == {"p0": ("#1",), "p1": ("#2",)}


async def test_experts_are_ranked_and_tagged_with_the_domains_shown():
    rows = [evidence("alice", "d1", "t1"), evidence("alice", "d2", "t1", date="2020"), evidence("bob", "d3", "t2")]
    graph = FakeGraph(evidence={frozenset({"t1", "t2"}): rows})
    services = make_services(graph, FakeTaxi(["t1", "t2"]), ranking_method="count")

    result = await find_domain_experts(services, "machine learning")

    assert [(d.tag, d.uid, d.name, d.path, d.nb_publications) for d in result.domains] == [
        ("#1", "t1", "T1", "Science [Domain] > T1 [Topic]", 2),
        ("#2", "t2", "T2", "Science [Domain] > T2 [Topic]", 2),
    ]
    alice, bob = result.experts
    assert (alice.person_uid, alice.score, alice.tags, alice.nb_publications_on_topic) == ("alice", 100.0, ("#1",), 2)
    assert [publication.year for publication in alice.recent_publications] == [2024, 2020]
    assert (bob.person_uid, bob.score, bob.tags) == ("bob", 50.0, ("#2",))
    assert result.ranking_method == "count"


async def test_a_topic_the_judge_skipped_is_set_aside_then_used_if_the_answer_would_be_too_thin():
    graph = FakeGraph(
        evidence={
            frozenset({"t1"}): [evidence("alice", "d1", "t1")],
            frozenset({"t1", "t3"}): [evidence("alice", "d1", "t1"), evidence("bob", "d2", "t3")],
        }
    )
    judge = FakeJudge(scores={1: 0.9, 2: 0.2})
    services = make_services(graph, FakeTaxi(["t1", "t2", "t3"]), concept_judge=judge, min_authors=2)

    result = await find_domain_experts(services, "machine learning")

    # t3 was never scored: it is left out of the first pass and only joins the fallback one.
    assert graph.evidence_requests == [["t1"], ["t1", "t3"]]
    assert [expert.person_uid for expert in result.experts] == ["alice", "bob"]
