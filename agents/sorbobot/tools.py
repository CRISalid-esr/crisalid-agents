"""The tools the Sorbobot LLM can call."""

import json
import logging
from dataclasses import asdict

from langchain_core.tools import BaseTool, tool

from agents.sorbobot import domains, experts, researchers
from agents.sorbobot.graph import GraphUnavailableError
from agents.sorbobot.models import ExpertsResult
from agents.sorbobot.services import SorbobotServices
from agents.sorbobot.taxi import QueryTooShortError, TaxiUnavailableError
from agents.sorbobot.topics import NoDocumentedTopicError

logger = logging.getLogger(__name__)

def _json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _experts_payload(result: ExpertsResult) -> dict:
    payload = asdict(result)
    for domain in payload["domains"]:
        # The path already carries the name.
        domain.pop("name")
    return payload


def build_tools(services: SorbobotServices) -> list[BaseTool]:
    min_chars = services.taxi.min_query_chars

    def unavailable(exc: Exception) -> str:
        logger.warning("A Sorbobot service is unavailable", exc_info=exc)
        if isinstance(exc, TaxiUnavailableError):
            return _json({"error": "The topic matching service is unavailable. Ask the user to retry later."})
        return _json({"error": "The research database is unavailable. Ask the user to retry later."})

    def too_short(topic: str) -> str:
        hint = (
            f"The topic matching service ignores a topic shorter than {min_chars} characters. Call the tool again "
            'with a fuller English phrase naming the field and what it studies, for example "biology and life '
            'sciences research" for "biology".'
        )
        return _json({"topic": topic, "hint": hint})

    def no_document(topic: str) -> str:
        return _json({"topic": topic, "error": "No document of the database is linked to the topics found."})

    def person_not_found(person_name: str) -> str:
        error = "No Sorbonne University researcher matches this name. Ask the user to check the spelling."
        return _json({"person_name": person_name, "error": error})

    async def find_domain_experts(topic: str) -> str:
        """Find the Sorbonne University researchers working on a research topic, best experts first.

        Args:
            topic: The research topic in English, as a descriptive phrase of at least {min_chars} characters
                rather than an acronym or a single word (e.g. "machine learning for medical imaging", not "ML";
                "biology and life sciences research", not "biology").
        """
        try:
            result = await experts.find_domain_experts(services, topic)
        except QueryTooShortError:
            return too_short(topic)
        except NoDocumentedTopicError:
            return no_document(topic)
        except (TaxiUnavailableError, GraphUnavailableError) as exc:
            return unavailable(exc)
        if not result.experts:
            hint = "No researcher found. Retry once with a broader English phrase, then say nothing was found."
            return _json({"topic": topic, "experts": [], "hint": hint})
        return _json(_experts_payload(result))

    async def get_person_expertise(person_name: str) -> str:
        """List the research domains of a Sorbonne University researcher and a few of their publications on them.

        Args:
            person_name: The researcher's name, e.g. "Jeannette Durand".
        """
        try:
            result = await researchers.get_person_expertise(services, person_name)
        except GraphUnavailableError as exc:
            return unavailable(exc)
        if result is None:
            return person_not_found(person_name)
        return _json(asdict(result))

    async def get_researcher_profile(person_name: str, question: str) -> str:
        """Gather what is needed to summarise a researcher's work or answer a question about their expertise.

        Args:
            person_name: The researcher's name, taken from the question or from the conversation.
            question: The user's question about this researcher, as asked.
        """
        try:
            result = await researchers.get_researcher_profile(services, person_name, question)
        except GraphUnavailableError as exc:
            return unavailable(exc)
        if result is None:
            return person_not_found(person_name)
        return _json(asdict(result))

    async def explore_domains(topic: str) -> str:
        """Describe research domains, what they cover and how they differ, from their definitions.

        Args:
            topic: In English, a research field as a phrase of at least {min_chars} characters
                (e.g. "computer vision and image processing") or the exact names of domains shown earlier in the
                conversation.
        """
        try:
            result = await domains.explore_domains(services, topic)
        except QueryTooShortError:
            return too_short(topic)
        except NoDocumentedTopicError:
            return no_document(topic)
        except (TaxiUnavailableError, GraphUnavailableError) as exc:
            return unavailable(exc)
        if not result.domains:
            return _json({"topic": topic, "domains": [], "hint": "No domain matches. Say so in one sentence."})
        return _json(asdict(result))

    def described(function) -> BaseTool:
        # The docstring becomes the tool description: the settings fill its fields before LangChain reads it.
        if not function.__doc__:
            raise RuntimeError(f"{function.__name__} has no docstring to describe it: Python stripped them (-OO)")
        function.__doc__ = function.__doc__.format(min_chars=min_chars)
        return tool(parse_docstring=True)(function)

    return [
        described(function)
        for function in (find_domain_experts, get_person_expertise, get_researcher_profile, explore_domains)
    ]
