"""The four LangGraph tools the LLM sees (§5 of the spec).

Each one validates its parameters, runs the business algorithm and answers JSON. Functional hints (``hint``) are
explained by the LLM; technical errors (``error``) carry a stable code that the agent node turns into a translated
message without calling the LLM again.
"""

import json
import logging

from langchain_core.tools import tool

from agents.sorbobot.config import SorbobotSettings
from agents.sorbobot.domains import DomainExploration
from agents.sorbobot.experts import ExpertSearch
from agents.sorbobot.i18n import Translator
from agents.sorbobot.researchers import ResearcherSearch
from agents.sorbobot.taxi import QueryTooShortError, TaxiUnavailableError
from agents.sorbobot.toolbox import GraphUnavailableError

logger = logging.getLogger(__name__)

# Technical error code -> translation key of the message displayed by the agent node.
TECHNICAL_ERRORS = {
    "taxi_unavailable": "errors.taxi_unavailable",
    "graph_unavailable": "errors.graph_unavailable",
}


def _json(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False)


def read_result(content) -> dict | None:
    """The JSON object of a tool result, or None when the content is not one."""
    if isinstance(content, list):
        content = "".join(part if isinstance(part, str) else str(part.get("text", "")) for part in content)
    try:
        data = json.loads(content)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def build_tools(experts: ExpertSearch, researchers: ResearcherSearch, domains: DomainExploration,
                translator: Translator, settings: SorbobotSettings) -> list:
    min_chars = settings.taxi_min_query_chars

    async def guarded(language, run) -> str:
        language = translator.resolve(language)
        try:
            data = await run()
        except TaxiUnavailableError:
            data = {"error": "taxi_unavailable"}
        except GraphUnavailableError:
            data = {"error": "graph_unavailable"}
        data["language"] = language
        return _json(data)

    def too_short(topic: str) -> dict:
        return {"topic": topic, "hint": "topic_too_short", "min_chars": min_chars}

    @tool
    async def find_domain_experts(topic: str, language: str | None = None) -> str:
        """Find the internal researchers who work on a research theme, ranked with a score out of 100, with the
        OpenAlex domains closest to the theme. `topic`: the theme in English, as a descriptive phrase of at least
        25 characters (expand acronyms, make vague subjects more specific). `language`: the ISO 639-1 code of the
        language to answer in (e.g. "en", "fr"), always provided."""

        async def run() -> dict:
            if len((topic or "").strip()) < min_chars:
                return too_short(topic)
            try:
                return await experts.run(topic.strip())
            except QueryTooShortError:
                return too_short(topic)

        return await guarded(language, run)

    @tool
    async def get_person_expertise(person_name: str, language: str | None = None) -> str:
        """Give the research domains of a named internal researcher and their publications in those domains.
        `person_name`: the name as the user wrote it. `language`: the ISO 639-1 code of the language to answer in,
        always provided."""
        return await guarded(language, lambda: researchers.expertise(person_name or ""))

    @tool
    async def get_researcher_profile(person_name: str, question: str, language: str | None = None) -> str:
        """Gather what is needed to summarise a named researcher's work or to answer a question about it: their
        domains and their most relevant publications with abstracts, keywords and numbered references. Use it for
        summaries and for any question about a researcher's work. `person_name`: the name, from the question or the
        conversation. `question`: the user's question as asked. `language`: the ISO 639-1 code of the language to
        answer in, always provided."""
        return await guarded(language, lambda: researchers.profile(person_name or "", question or ""))

    @tool
    async def explore_domains(topic: str | None = None, domain_names: list[str] | None = None,
                              language: str | None = None) -> str:
        """Describe research domains: what they cover and how they differ. Give either `topic` (a domain described
        in English in at least 25 characters) or `domain_names` (the exact names of domains displayed earlier in
        the conversation, copied as displayed); `domain_names` wins when both are given. `language`: the ISO 639-1
        code of the language to answer in, always provided."""

        async def run() -> dict:
            names = [str(name) for name in domain_names if str(name).strip()] if domain_names else []
            if not names and len((topic or "").strip()) < min_chars:
                return too_short(topic or "")
            try:
                return await domains.run((topic or "").strip() or None, names or None)
            except QueryTooShortError:
                return too_short(topic or "")

        return await guarded(language, run)

    return [find_domain_experts, get_person_expertise, get_researcher_profile, explore_domains]
