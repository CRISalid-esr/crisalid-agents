"""LLM judges: one call scores numbered items (topics or publications) against the user's question."""

import asyncio
import json
import logging
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from agents.sorbobot.models import ROLES

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent


def load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8").strip()


def first_json_object(raw: str) -> dict | None:
    """The first JSON object of the reply, whatever the model wrote around it."""
    start = raw.find("{")
    if start < 0:
        return None
    try:
        parsed, _ = json.JSONDecoder().raw_decode(raw[start:])
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def parse_scores(raw: str, count: int) -> dict[int, float] | None:
    """Read ``{"scores": [{"id": 1, "score": 0.9}]}`` or ``{"scores": {"1": 0.9}}``."""
    answer = first_json_object(raw)
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
        # The listing starts at 1, so an item 0 means the model answered from 0
        answered = {number + 1: score for number, score in answered.items()}
    scores = {number: score for number, score in answered.items() if 1 <= number <= count}
    return scores or None


async def ask_scores(
    llm: BaseChatModel,
    system_prompt: str,
    question: str,
    items: list[str],
    timeout_seconds: float,
) -> dict[int, float] | None:
    """Item number (from 1) -> score, or None when the call fails or the reply is unusable."""
    listing = "\n".join(f"{number}. {item}" for number, item in enumerate(items, 1))
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"Question: {question}\n\nItems:\n{listing}\n\nJSON:"),
    ]
    try:
        async with asyncio.timeout(timeout_seconds):
            response = await llm.ainvoke(messages)
    except TimeoutError:
        # A failing or slow judge must not fail the search
        logger.warning("Judge call timed out after %.1fs", timeout_seconds)
        return None
    except Exception:
        # Any failure of the judge is absorbed: the search must go on without its opinion.
        logger.warning("Judge call failed", exc_info=True)
        return None
    scores = parse_scores(response.text, len(items))
    if scores is None:
        logger.warning("Judge gave no usable score")
    return scores


class ConceptJudge:
    """Scores taxonomy topics, described by their path, against the user's query."""

    def __init__(self, llm: BaseChatModel, timeout_seconds: float):
        self._llm = llm
        self._prompt = load_prompt("concept_judge_prompt.md")
        self._timeout_seconds = timeout_seconds

    async def score(self, query: str, paths: list[str]) -> dict[int, float] | None:
        """None, without any LLM call, when there is nothing to judge."""
        if not paths:
            return None
        return await ask_scores(self._llm, self._prompt, query, paths, self._timeout_seconds)


class PublicationJudge:
    """Picks the publications of a researcher that best answer a question about their expertise."""

    def __init__(self, llm: BaseChatModel, timeout_seconds: float):
        self._llm = llm
        self._prompt = load_prompt("publication_judge_prompt.md")
        self._timeout_seconds = timeout_seconds

    async def select(self, question: str, publications: list[dict], limit: int) -> list[dict]:
        """The ``limit`` best publications in their original order; the first ones when the judge fails."""
        if len(publications) <= limit:
            return publications
        items = [
            f"{p.get('title') or '?'} ({p.get('publication_date') or '?'}) "
            f"[{', '.join(ROLES[code].replace('_', ' ') for code in p.get('roles') or [] if code in ROLES)}]"
            for p in publications
        ]
        scores = await ask_scores(self._llm, self._prompt, question, items, self._timeout_seconds)
        if scores is None:
            return publications[:limit]
        best = sorted(range(len(publications)), key=lambda index: -scores.get(index + 1, 0.0))[:limit]
        return [publications[index] for index in sorted(best)]
