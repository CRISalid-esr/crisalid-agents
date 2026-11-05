import pytest

from agents.sorbobot.researchers import ResearcherSearch, clean_name, merge_duplicates
from tests.sorbobot_fakes import FakePublicationJudge, FakeToolbox, concept, document, external, internal, make_settings, summary


def search(toolbox, judge=None, **settings) -> ResearcherSearch:
    return ResearcherSearch(toolbox, judge, make_settings(**settings))


@pytest.mark.parametrize("raw, cleaned", [
    ("Joe DUPONT", "joe dupont"), ("Dupont AND Martin", "dupont and martin"), ("O'Neil (Jr.)", "o'neil jr."),
    ("  a  +b  ", "a b"), ('"Smith" ~2 /x\\ [y] {z} ^!|&*?:', "smith 2 x y z"), ("", ""), (None, ""),
])
def test_clean_name(raw, cleaned):
    assert clean_name(raw) == cleaned


async def test_the_first_internal_candidate_is_taken():
    toolbox = FakeToolbox(persons=[external("x", "Joe Dupont", 5.0), internal("p1", "Joe Dupont", 4.0), internal("p2", "Jo Dupond", 3.0)])
    person = await search(toolbox).find_person("Dupont, Joe")
    assert person.uid == "p1"
    assert toolbox.calls == [("search_persons", "dupont, joe", 10)]


async def test_person_not_found():
    assert (await search(FakeToolbox(persons=[external("x", "Someone")])).expertise("someone"))["hint"] == "person_not_found"
    toolbox = FakeToolbox()
    assert (await search(toolbox).profile("***", "q")) == {"person_name": "***", "hint": "person_not_found"}
    assert toolbox.calls == []  # an empty cleaned name is not searched


async def test_expertise_lists_domains_and_authored_publications_without_duplicates():
    toolbox = FakeToolbox(
        persons=[internal("p1", "Joe Dupont")], topics={"t1", "t2"},
        person_concepts=[concept("t1", 7), concept("t2", 3)],
        person_documents=[
            document("d1", ("t1",), ("aut",), "2024-03-01", "Deep Learning"),
            document("d2", ("t2",), ("aut",), "2023", "deep  learning!"),  # the same work in another catalogue
            document("d3", ("t2",), ("ths",), "2022"),                      # supervised: not an authored publication
            document("d4", ("t2", "t1"), ("spk", "aut"), "2020"),
        ],
    )
    result = await search(toolbox, show_max_domains=5, show_max_person_publications=20).expertise("Joe Dupont")
    assert result["person_uid"] == "p1" and result["name"] == "Joe Dupont"
    assert [(d["tag"], d["uid"], d["nb_publications"]) for d in result["domains"]] == [("#1", "t1", 7), ("#2", "t2", 3)]
    assert result["domains"][0]["path"].endswith("T1 [Topic]") and result["domains"][0]["definition"]
    assert result["publications"] == [
        {"title": "Deep Learning", "year": 2024, "tags": ["#1", "#2"]},
        {"title": "Title of d4", "year": 2020, "tags": ["#1", "#2"]},
    ]
    assert ("list_person_concepts", "p1", 0.57, 5) in toolbox.calls
    assert ("list_person_documents", "p1", ["t1", "t2"], 0.57, 20) in toolbox.calls


def test_merge_duplicates_gathers_roles_and_topics_in_order():
    merged = merge_duplicates([
        document("d1", ("t1",), ("aut",), None, "Same Work"),
        document("d2", ("t2", "t1"), ("ths", "aut"), "2019", "same work"),
        document("d3", ("t3",), ("spk",), "2018", "Other"),
    ])
    assert [(d.uid, d.concept_uids, d.roles, d.publication_date) for d in merged] == [
        ("d1", ["t1", "t2"], ["aut", "ths"], "2019"), ("d3", ["t3"], ["spk"], "2018"),
    ]


async def test_profile_roles_judge_summaries_and_numbering():
    documents = [document(f"d{i}", ("t1",), ("aut",), f"20{10 + i}") for i in range(1, 5)]
    documents[1].roles = ["ths"]                      # d2 supervised
    documents[2].roles = ["spk", "aut"]               # d3 authored and presented: author wins
    documents[3].roles = ["pbd"]                      # d4 unknown role: other
    documents.append(document("d5", ("t1",), ("aut",), "2021", "Title of d1"))  # duplicate of d1
    toolbox = FakeToolbox(
        persons=[internal("p1", "Joe Dupont")], topics={"t1"}, person_concepts=[concept("t1", 7)],
        person_documents=documents,
        summaries={"d1": summary("d1", "word " * 100, ("alpha", "beta")), "d2": summary("d2", None, ())},
    )
    judge = FakePublicationJudge(keep=[3, 0, 1])  # d4, d1, d2 among the 4 merged works
    result = await search(toolbox, judge, profile_max_publications=3).profile("Joe Dupont", "Summarise his work")
    assert judge.calls == [("Summarise his work", ["d1", "d2", "d3", "d4"], 3)]
    assert result["question"] == "Summarise his work" and [d["tag"] for d in result["domains"]] == ["#1"]
    assert [(p["reference"], p["role"], p["title"]) for p in result["publications"]] == [
        ("[1]", "author", "Title of d1"), ("[2]", "thesis_supervisor", "Title of d2"), ("[3]", "other", "Title of d4"),
    ]
    first = result["publications"][0]
    assert first["keywords"] == ["alpha", "beta"] and first["abstract"].endswith("…") and len(first["abstract"]) <= 301
    assert first["tags"] == ["#1"] and first["year"] == 2011
    assert result["publications"][1]["abstract"] == "" and result["publications"][2]["keywords"] == []
    assert ("list_person_documents", "p1", ["t1"], 0.57, 60) in toolbox.calls
    assert ("get_document_summaries", ["d4", "d1", "d2"]) in toolbox.calls or ("get_document_summaries", ["d1", "d2", "d4"]) in toolbox.calls


async def test_profile_without_judge_keeps_the_first_publications():
    documents = [document(f"d{i}") for i in range(1, 6)]
    toolbox = FakeToolbox(persons=[internal("p1", "Joe")], topics={"t1"}, person_concepts=[concept("t1", 1)], person_documents=documents)
    result = await search(toolbox, None, profile_max_publications=2).profile("Joe", "q")
    assert [p["title"] for p in result["publications"]] == ["Title of d1", "Title of d2"]
    few = await search(toolbox, FakePublicationJudge(keep=[0]), profile_max_publications=10).profile("Joe", "q")
    assert len(few["publications"]) == 5  # under the limit: the judge is not asked
