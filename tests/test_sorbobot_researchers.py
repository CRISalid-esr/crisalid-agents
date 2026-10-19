import pytest

from agents.sorbobot.researchers import clean_person_name, get_person_expertise, get_researcher_profile
from tests.sorbobot_fakes import FakeGraph, FakePublicationJudge, make_services

PERSONS = [
    {"uid": "ext", "display_name": "Jeannette Durand", "external": True},
    {"uid": "jd", "display_name": "Jeannette Durand", "external": False},
    {"uid": "jd2", "display_name": "Jeanette Durant", "external": False},
]
CONCEPTS = [
    {"domain_uid": "t1", "domain_name": "Logic", "domain_type": "Topic", "nb_publications": 4},
    {"domain_uid": "t2", "domain_name": "Topic Modeling", "domain_type": "Topic", "nb_publications": 2},
]


def document(uid: str, roles: list[str], concept_uids: list[str], date: str = "2023-05-01") -> dict:
    return {"doc_uid": uid, "title": f"Title of {uid}", "publication_date": date, "concept_uids": concept_uids,
            "roles": roles}


@pytest.mark.parametrize(
    ("raw", "cleaned"),
    [
        ("Hugo Bertrand-Vasseur", "hugo bertrand vasseur"),
        ('NOT Fontaine" OR display_name:*', "not fontaine or display_name"),
        ("  -  ", ""),
    ],
)
def test_clean_person_name(raw, cleaned):
    assert clean_person_name(raw) == cleaned


async def test_expertise_keeps_the_first_internal_candidate_and_tags_publications():
    documents = [document("d1", ["aut"], ["t2", "t1"])]
    graph = FakeGraph(persons=PERSONS, person_concepts=CONCEPTS, person_documents=documents)

    result = await get_person_expertise(make_services(graph), "Jeannette Durand")

    assert graph.person_searches == ["jeannette durand"]
    assert (result.person_uid, result.other_candidates) == ("jd", ("Jeanette Durant",))
    assert [(d.tag, d.name, d.path, d.nb_publications) for d in result.domains] == [
        ("#1", "Logic", "Science [Domain] > T1 [Topic]", 4),
        ("#2", "Topic Modeling", "Science [Domain] > T2 [Topic]", 2),
    ]
    assert [(p.title, p.year, p.tags, p.role) for p in result.publications] == [
        ("Title of d1", 2023, ("#1", "#2"), "author")
    ]


async def test_expertise_tells_a_supervised_thesis_from_the_researcher_s_own_work():
    # The graph links a supervisor to the thesis by a ths contribution: it is not one of their publications.
    documents = [document("d1", ["aut"], ["t1"]), document("d2", ["ths"], ["t1"]), document("d3", ["spk"], ["t2"])]
    graph = FakeGraph(persons=PERSONS, person_concepts=CONCEPTS, person_documents=documents)

    result = await get_person_expertise(make_services(graph), "Jeannette Durand")

    assert [(p.title, p.role) for p in result.publications] == [
        ("Title of d1", "author"),
        ("Title of d2", "thesis_supervisor"),
        ("Title of d3", "speaker"),
    ]


async def test_an_unknown_or_empty_name_gives_nothing():
    graph = FakeGraph(persons=PERSONS[:1])

    assert await get_person_expertise(make_services(graph), "Jeannette Durand") is None
    assert await get_researcher_profile(make_services(graph), ' :"* ', "summary") is None
    assert graph.person_searches == ["jeannette durand"]


async def test_the_profile_cites_the_judged_publications_grouped_by_role():
    documents = [
        document("d1", ["spk"], ["t1"]),
        document("d2", ["aut"], ["t1", "t2"]),
        document("d3", ["ths", "aut"], ["t2"]),
        document("d4", ["aut"], ["t1"]),
    ]
    summaries = {"d2": {"uid": "d2", "abstract": "word " * 100, "keywords": ["graphs", "logic"]}}
    graph = FakeGraph(persons=PERSONS, person_concepts=CONCEPTS, person_documents=documents, summaries=summaries)
    services = make_services(graph, publication_judge=FakePublicationJudge(keep=[0, 1, 2]))

    result = await get_researcher_profile(services, "Jeannette Durand", "Summarise her research")

    assert [(p.reference, p.title, p.role, p.tags) for p in result.publications] == [
        ("[1]", "Title of d2", "author", ("#1", "#2")),
        ("[2]", "Title of d3", "author", ("#2",)),
        ("[3]", "Title of d1", "speaker", ("#1",)),
    ]
    first = result.publications[0]
    assert first.keywords == ("graphs", "logic")
    assert len(first.abstract) <= 301 and first.abstract.endswith("…")
