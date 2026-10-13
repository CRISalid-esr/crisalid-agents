"""Single-shot structured (JSON) LLM calls of the TRM pipeline: topic expansion and candidate verification."""

import json
import re
from pathlib import Path
from string import Template

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from trm.config import TRMSettings
from trm.models import Candidate, Expansion, PublicationVerdict, Verification

_PROMPT_DIR = Path(__file__).resolve().parent
EXPANSION_PROMPT = Template((_PROMPT_DIR / "expansion_prompt.md").read_text(encoding="utf-8"))
VERIFICATION_PROMPT = Template((_PROMPT_DIR / "verification_prompt.md").read_text(encoding="utf-8"))
_SYSTEM = "You are a careful research-office analyst. You always answer with valid JSON only."
_OVERALL = {"strong", "plausible", "weak", "none"}


class LLMJsonError(ValueError):
    pass


def parse_json_object(text: str) -> dict:
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise LLMJsonError(f"No JSON object in model answer: {text[:120]!r}")
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise LLMJsonError(f"Invalid JSON in model answer: {exc}") from exc
    if not isinstance(data, dict):
        raise LLMJsonError("Model answer is not a JSON object")
    return data


class StructuredLLM:
    def __init__(self, llm: BaseChatModel, retries: int = 2):
        self.llm = llm
        self.retries = retries
        self.calls = 0

    async def json(self, prompt: str) -> dict:
        messages = [SystemMessage(content=_SYSTEM), HumanMessage(content=prompt)]
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            self.calls += 1
            answer = await self.llm.ainvoke(messages)
            try:
                return parse_json_object(str(answer.content))
            except LLMJsonError as exc:
                last = exc
                messages = messages[:2] + [
                    answer,
                    HumanMessage(content="Your answer was not a valid JSON object. Answer again with the JSON object only."),
                ]
        raise LLMJsonError(f"No valid JSON after {self.retries + 1} attempts: {last}")


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [v.strip() for v in value if isinstance(v, str) and v.strip()]
    return []


def flatten_queries(data: dict, topic: dict, max_queries: int) -> list[str]:
    """Order: the topic query (always present, built locally as a fallback), summary, variations, EN then FR terms."""
    queries = data.get("queries") or {}
    terms = data.get("researcher_terms") or {}
    fallback_topic = f"{topic.get('title', '')}. {(topic.get('scope') or '')[:300]}".strip()
    ordered = (
        (_strings(queries.get("topic")) or [fallback_topic])
        + _strings(queries.get("summary"))
        + _strings(queries.get("variations"))
        + _strings(terms.get("en"))
        + _strings(terms.get("fr"))
    )
    seen: set[str] = set()
    result = []
    for q in ordered:
        key = q.lower()
        if key not in seen:
            seen.add(key)
            result.append(q)
    return result[:max_queries]


async def expand_topic(llm: StructuredLLM, topic: dict, settings: TRMSettings) -> Expansion:
    prompt = EXPANSION_PROMPT.substitute(
        topic_id=topic.get("topic_id", ""),
        title=topic.get("title", ""),
        destination=topic.get("destination") or "",
        expected_outcome=(topic.get("expected_outcome") or "")[:settings.scope_chars],
        scope=(topic.get("scope") or "")[:settings.scope_chars],
    )
    data = await llm.json(prompt)
    summary = str(data.get("summary") or "").strip()
    return Expansion(summary=summary, queries=flatten_queries(data, topic, settings.max_queries), raw=data)


def _publication_lines(candidate: Candidate, settings: TRMSettings) -> str:
    lines = []
    for pub in candidate.publications[:settings.publications_per_candidate]:
        year = f" ({pub.year})" if pub.year else ""
        lines.append(f"- uid: {pub.uid}\n  title: {pub.title}{year}")
        if pub.abstract:
            lines.append(f"  abstract: {pub.abstract[:settings.abstract_chars]}")
    return "\n".join(lines)


async def verify_candidate(
    llm: StructuredLLM, topic: dict, summary: str, candidate: Candidate, settings: TRMSettings
) -> Verification:
    prompt = VERIFICATION_PROMPT.substitute(
        topic_id=topic.get("topic_id", ""),
        title=topic.get("title", ""),
        summary=summary,
        expected_outcome=(topic.get("expected_outcome") or "")[:2500],
        name=candidate.name,
        publications=_publication_lines(candidate, settings),
    )
    data = await llm.json(prompt)
    known = {p.uid for p in candidate.publications}
    verdicts = []
    for item in data.get("publications") or []:
        if not isinstance(item, dict) or item.get("uid") not in known:
            continue
        verdicts.append(PublicationVerdict(
            uid=str(item["uid"]), relevant=bool(item.get("relevant")), reason=str(item.get("reason") or "").strip()
        ))
    overall = str(data.get("overall") or "none").strip().lower()
    if overall not in _OVERALL:
        overall = "none"
    return Verification(verdicts=verdicts, overall=overall, justification=str(data.get("justification") or "").strip())
