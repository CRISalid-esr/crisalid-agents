"""LLM judges: one call scores a numbered list of items against the user's question (§6.1, §6.2 of the spec)."""

import asyncio
import json
import logging

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from agents.sorbobot.models import ROLES, PersonDocument

logger = logging.getLogger(__name__)


def first_json_object(raw: str) -> dict | None:
    """The first JSON object of the reply, whatever the model wrote around it (sentences, code fences)."""
    start = raw.find("{")
    while start >= 0:
        try:
            parsed, _ = json.JSONDecoder().raw_decode(raw[start:])
        except ValueError:
            start = raw.find("{", start + 1)
            continue
        return parsed if isinstance(parsed, dict) else None
    return None


def parse_scores(raw: str, count: int) -> dict[int, float] | None:
    """Read ``{"scores": [{"id": 1, "score": 0.9}]}`` or ``{"scores": {"1": 0.9}}``; None when unreadable."""
    answer = first_json_object(raw or "")
    if answer is None:
        return None
    entries = answer.get("scores")
    if isinstance(entries, dict):
        pairs = list(entries.items())
    elif isinstance(entries, list):
        pairs = [(entry.get("id"), entry.get("score")) for entry in entries if isinstance(entry, dict)]
    else:
        return None
    answered: dict[int, float] = {}
    for key, value in pairs:
        try:
            number, score = int(key), float(value)
        except (TypeError, ValueError):
            continue
        answered[number] = min(max(score, 0.0), 1.0)
    if not answered:
        return None
    if min(answered) == 0:
        # The listing starts at 1: a model that numbered from 0 is shifted by one.
        answered = {number + 1: score for number, score in answered.items()}
    scores = {number: score for number, score in answered.items() if 1 <= number <= count}
    return scores or None


async def ask_scores(llm: BaseChatModel, system_prompt: str, question: str, items: list[str],
                     timeout_seconds: float) -> dict[int, float] | None:
    """Item number (from 1) -> score, or None when the judge fails, is too slow or answers something unreadable."""
    listing = "\n".join(f"{number}. {item}" for number, item in enumerate(items, 1))
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"Question: {question}\n\nItems:\n{listing}\n\nJSON:"),
    ]
    try:
        async with asyncio.timeout(timeout_seconds):
            response = await llm.ainvoke(messages)
    except TimeoutError:
        logger.warning("Judge timed out after %.0fs", timeout_seconds)
        return None
    except Exception:  # noqa: BLE001 — the search must never fail because of the judge
        logger.warning("Judge call failed", exc_info=True)
        return None
    scores = parse_scores(response.text if isinstance(response.text, str) else str(response.content), len(items))
    if scores is None:
        logger.warning("Judge answered something unreadable")
    elif len(scores) < len(items):
        logger.warning("Judge scored %d of %d items", len(scores), len(items))
    return scores


class TopicJudge:
    """Scores taxonomy topics, each described by its path, against the question."""

    def __init__(self, llm: BaseChatModel, prompt: str, timeout_seconds: float):
        self._llm = llm
        self._prompt = prompt
        self._timeout_seconds = timeout_seconds

    async def score(self, question: str, paths: list[str]) -> dict[int, float] | None:
        if not paths:
            return {}
        return await ask_scores(self._llm, self._prompt, question, paths, self._timeout_seconds)


class PublicationJudge:
    """Picks the publications of a researcher that help most to answer the question."""

    def __init__(self, llm: BaseChatModel, prompt: str, timeout_seconds: float):
        self._llm = llm
        self._prompt = prompt
        self._timeout_seconds = timeout_seconds

    async def select(self, question: str, publications: list[PersonDocument], limit: int) -> list[PersonDocument]:
        """The ``limit`` best publications in their original order; the first ones when the judge fails."""
        if len(publications) <= limit:
            return list(publications)
        items = [
            f"{p.title or '?'} ({p.publication_date or '?'}) "
            f"[{', '.join(ROLES[code].replace('_', ' ') for code in p.roles if code in ROLES) or 'other'}]"
            for p in publications
        ]
        scores = await ask_scores(self._llm, self._prompt, question, items, self._timeout_seconds)
        if scores is None:
            return publications[:limit]
        best = sorted(range(len(publications)), key=lambda index: (-scores.get(index + 1, 0.0), index))[:limit]
        return [publications[index] for index in sorted(best)]
