import asyncio

import pytest
from langchain_core.messages import AIMessage

from agents.sorbobot.judges import PublicationJudge, TopicJudge, ask_scores, parse_scores
from tests.fake_llm import ScriptedChatModel
from tests.sorbobot_fakes import document


@pytest.mark.parametrize("raw, expected", [
    ('{"scores": [{"id": 1, "score": 0.91}, {"id": 2, "score": 0.2}]}', {1: 0.91, 2: 0.2}),
    ('{"scores": {"1": 0.91, "2": "0.2"}}', {1: 0.91, 2: 0.2}),
    ('Here you go:\n```json\n{"scores": [{"id": 1, "score": 0.5}]}\n```\nDone.', {1: 0.5}),
    ('{"scores": [{"id": 0, "score": 0.7}, {"id": 1, "score": 0.3}]}', {1: 0.7, 2: 0.3}),
    ('{"scores": [{"id": 1, "score": 1.7}, {"id": 2, "score": -3}]}', {1: 1.0, 2: 0.0}),
    ('{"scores": [{"id": 1, "score": 0.9}, {"id": 7, "score": 0.9}]}', {1: 0.9}),
    ('{"scores": [{"id": "two", "score": 0.9}, {"id": 2, "score": "high"}]}', None),
    ('{"other": 1}', None),
    ("not json at all", None),
    ("{ broken json {\"scores\": {\"1\": 0.4}}", {1: 0.4}),
])
def test_parse_scores_is_tolerant(raw, expected):
    assert parse_scores(raw, 3) == expected


async def test_ask_scores_sends_a_numbered_listing_and_reads_the_answer():
    llm = ScriptedChatModel(responses=[AIMessage(content='{"scores": [{"id": 1, "score": 0.8}, {"id": 2, "score": 0.1}]}')], calls=[])
    scores = await ask_scores(llm, "system", "who works on X?", ["path A", "path B"], 5.0)
    assert scores == {1: 0.8, 2: 0.1}
    system, human = llm.calls[0]
    assert system.content == "system" and "Question: who works on X?" in human.content
    assert "1. path A\n2. path B" in human.content


class SlowModel(ScriptedChatModel):
    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        await asyncio.sleep(0.5)
        return await super()._agenerate(messages, stop, run_manager, **kwargs)


class BrokenModel(ScriptedChatModel):
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise RuntimeError("model down")


async def test_a_slow_broken_or_unreadable_judge_gives_no_judgement():
    slow = SlowModel(responses=[AIMessage(content='{"scores": {"1": 0.9}}')], calls=[])
    assert await ask_scores(slow, "s", "q", ["a"], 0.05) is None
    assert await ask_scores(BrokenModel(responses=[], calls=[]), "s", "q", ["a"], 1.0) is None
    unreadable = ScriptedChatModel(responses=[AIMessage(content="I cannot decide.")], calls=[])
    assert await ask_scores(unreadable, "s", "q", ["a"], 1.0) is None


async def test_topic_judge_scores_nothing_without_a_call():
    llm = ScriptedChatModel(responses=[], calls=[])
    assert await TopicJudge(llm, "prompt", 1.0).score("q", []) == {}
    assert llm.calls == []


async def test_publication_judge_keeps_the_best_in_original_order():
    publications = [document("d1", roles=("aut",)), document("d2", roles=("ths",)), document("d3", roles=("spk", "aut")),
                    document("d4", roles=())]
    llm = ScriptedChatModel(
        responses=[AIMessage(content='{"scores": [{"id": 1, "score": 0.2}, {"id": 2, "score": 0.9}, {"id": 3, "score": 0.1}, {"id": 4, "score": 0.7}]}')],
        calls=[],
    )
    kept = await PublicationJudge(llm, "prompt", 1.0).select("summarise", publications, 2)
    assert [p.uid for p in kept] == ["d2", "d4"]
    human = llm.calls[0][1].content
    assert "1. Title of d1 (2021-05-01) [author]" in human
    assert "2. Title of d2 (2021-05-01) [thesis supervisor]" in human
    assert "3. Title of d3 (2021-05-01) [speaker, author]" in human and "[other]" in human


async def test_publication_judge_falls_back_to_the_first_publications():
    publications = [document("d1"), document("d2"), document("d3")]
    judge = PublicationJudge(BrokenModel(responses=[], calls=[]), "prompt", 1.0)
    assert [p.uid for p in await judge.select("q", publications, 2)] == ["d1", "d2"]
    llm = ScriptedChatModel(responses=[], calls=[])
    assert await PublicationJudge(llm, "prompt", 1.0).select("q", publications, 3) == publications and llm.calls == []
