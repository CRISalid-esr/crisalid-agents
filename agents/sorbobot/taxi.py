"""Client of crisalid-taxi."""

import logging

import aiohttp

from agents.sorbobot.models import TopicMatch, as_number

logger = logging.getLogger(__name__)

MATCH_PATH = "/api/v1/match/"


class TaxiUnavailableError(Exception):
    """crisalid-taxi could not be reached or answered with an error."""


class QueryTooShortError(Exception):
    """Shorter than ``min_query_chars``: crisalid-taxi matches nothing and says so with an empty answer."""


def _topic(match: object) -> TopicMatch | None:
    """A topic-level match crisalid-taxi sent with a readable similarity, or None for a row to skip."""
    if not isinstance(match, dict) or match.get("rel_type") != "HAS_TOPIC":
        return None
    uid, similarity = match.get("concept_uid"), as_number(match.get("value"))
    return TopicMatch(uid, similarity) if uid and similarity is not None else None


class TaxiClient:
    """Opens a session for the duration of one call: the adapters may run every request on a new event loop."""

    def __init__(
        self,
        base_url: str,
        similarity_threshold: float,
        max_topics: int,
        timeout_seconds: float,
        min_query_chars: int,
    ):
        # Built by hand rather than with ClientSession(base_url=...), which drops a path prefix of the base URL.
        self.match_url = base_url.rstrip("/") + MATCH_PATH
        self.similarity_threshold = similarity_threshold
        self.max_topics = max_topics
        self.min_query_chars = min_query_chars
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    async def match_topics(self, text: str) -> list[TopicMatch]:
        """Topic-level matches of the text, most similar first."""
        if len(text.strip()) < self.min_query_chars:
            raise QueryTooShortError(f"crisalid-taxi ignores a text shorter than {self.min_query_chars} characters")
        payload = {
            "inputs": [{"id": "query", "text": text}],
            "similarity_threshold": self.similarity_threshold,
            "max_topics": self.max_topics,
        }

        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.post(self.match_url, json=payload) as response:
                    response.raise_for_status()
                    body = await response.json()
            results = body.get("results") or []
            matches = results[0].get("matches") or [] if results else []
            topics = [topic for topic in map(_topic, matches) if topic is not None]
        except (aiohttp.ClientError, TimeoutError, AttributeError, TypeError, ValueError) as exc:
            raise TaxiUnavailableError(f"crisalid-taxi request failed: {exc!r}") from exc

        if not topics:
            logger.warning("crisalid-taxi matched no topic for %r", text)
        else:
            logger.info("crisalid-taxi matched %d topic(s)", len(topics))
        return sorted(topics, key=lambda topic: topic.similarity, reverse=True)
