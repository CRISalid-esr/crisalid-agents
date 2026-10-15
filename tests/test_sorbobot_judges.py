import pytest
from langchain_core.messages import AIMessage

from agents.sorbobot.judges import ConceptJudge, PublicationJudge, parse_scores
from tests.fake_llm import ScriptedChatModel


class FailingChatModel:
    async def ainvoke(self, messages):
        raise RuntimeError("LLM endpoint unreachable")


def replying(reply: str) -> ScriptedChatModel:
    return ScriptedChatModel(responses=[AIMessage(content=reply)], calls=[])


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ('{"scores": [{"id": 1, "score": 0.91}, {"id": 2, "score": 0.12}]}', {1: 0.91, 2: 0.12}),
        ('{"scores": {"1": 0.5}} and {that} is my answer.', {1: 0.5}),
        ('Sure:\n```json\n{"scores": {"1": 0.5, "2": 0.2}}\n```', {1: 0.5, 2: 0.2}),
        ('{"scores": [{"id": 1, "score": 1.4}, {"id": 7, "score": 0.9}]}', {1: 1.0}),
        ("no json here", None),
        ('{"scores": []}', None),
        ("{broken", None),
    ],
)
def test_parse_scores(reply, expected):
    assert parse_scores(reply, 2) == expected


async def test_the_concept_judge_scores_the_numbered_paths():
    llm = replying('{"scores": [{"id": 1, "score": 0.8}]}')

    judge = ConceptJudge(llm, timeout_seconds=30.0)

    scores = await judge.score("apprentissage {profond}", ["Sciences > Deep Learning"])

    assert scores == {1: 0.8}
    assert "Question: apprentissage {profond}" in llm.calls[0][-1].content
    assert "1. Sciences > Deep Learning" in llm.calls[0][-1].content


async def test_a_judge_with_nothing_to_judge_calls_no_llm():
    llm = replying("{}")

    assert await ConceptJudge(llm, timeout_seconds=30.0).score("vision", []) is None
    assert llm.calls == []


async def test_the_publication_judge_keeps_the_best_in_their_order_or_the_first_ones():
    publications = [{"title": f"P{index}", "publication_date": "2020", "roles": ["aut"]} for index in range(4)]
    few = replying("{}")

    judged = await PublicationJudge(replying('{"scores": {"1": 0.1, "2": 0.9, "3": 0.2, "4": 0.8}}'), 30.0).select(
        "methods", publications, 2
    )

    assert [p["title"] for p in judged] == ["P1", "P3"]
    assert await PublicationJudge(FailingChatModel(), 30.0).select("methods", publications, 2) == publications[:2]
    assert await PublicationJudge(few, 30.0).select("methods", publications[:2], 2) == publications[:2]
    assert few.calls == []


def test_a_judge_is_built_with_the_instructions_of_its_prompt_file():
    assert ConceptJudge(replying("{}"), 30.0)._prompt
    assert PublicationJudge(replying("{}"), 30.0)._prompt


def test_an_answer_numbered_from_zero_is_realigned_on_the_listing():
    assert parse_scores('{"scores": [{"id": 0, "score": 0.9}, {"id": 1, "score": 0.1}]}', 2) == {1: 0.9, 2: 0.1}
    # Numbered from 1 and incomplete: nothing is shifted, the missing item stays missing.
    assert parse_scores('{"scores": [{"id": 1, "score": 0.9}]}', 2) == {1: 0.9}
    # Numbered from 0 and incomplete: the whole answer is realigned, so no score lands on the wrong item.
    assert parse_scores('{"scores": [{"id": 0, "score": 0.9}, {"id": 2, "score": 0.3}]}', 4) == {1: 0.9, 3: 0.3}
