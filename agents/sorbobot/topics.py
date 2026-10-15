"""The first steps of the expert search and of the domain exploration: matched, documented and judged topics."""

import asyncio
import logging
from dataclasses import dataclass

from agents.sorbobot.models import Domain, shorten
from agents.sorbobot.services import SorbobotServices

logger = logging.getLogger(__name__)


class NoDocumentedTopicError(Exception):
    """crisalid-taxi matched topics, but no document of the graph is linked to any of them."""


@dataclass(frozen=True)
class CandidateTopics:
    # Topics holding documents, most similar to the query first.
    uids: list[str]
    similarity: dict[str, float]
    concepts: dict[str, dict]
    paths: dict[str, str]
    # None without a judgment; a topic the judge skipped is simply absent from it.
    scores: dict[str, float] | None

    def is_judged(self, uid: str) -> bool:
        return self.scores is not None and uid in self.scores

    def relevance(self, uid: str) -> float | None:
        """The score the judge gave, or None when it did not score this topic."""
        return None if self.scores is None else self.scores.get(uid)

    def select(self, threshold: float, with_unjudged: bool = False) -> list[str]:
        """The topics scored at least ``threshold``, plus those the judge skipped when ``with_unjudged``."""
        kept = []
        for uid in self.uids:
            score = self.relevance(uid)
            if score is None:
                if self.scores is None or with_unjudged:
                    kept.append(uid)
            elif score >= threshold:
                kept.append(uid)
        return kept

    def domain(self, uid: str, tag: str, definition_chars: int = 0) -> Domain:
        return build_domain(uid, tag, self.concepts, self.paths, definition_chars)


def concept_label(uid: str, concepts: dict[str, dict], paths: dict[str, str]) -> str:
    """How a concept is named to the judge and in an answer: its taxonomy path, or its name, or its uid."""
    return paths.get(uid) or concepts.get(uid, {}).get("name") or uid


def build_domain(
    uid: str, tag: str, concepts: dict[str, dict], paths: dict[str, str], definition_chars: int = 0
) -> Domain:
    concept = concepts.get(uid, {})
    name = concept.get("name") or uid
    return Domain(
        tag=tag,
        uid=uid,
        name=name,
        path=concept_label(uid, concepts, paths),
        nb_publications=concept.get("nb_docs", 0),
        definition=shorten(concept.get("description"), definition_chars) if definition_chars else "",
    )


async def find_candidate_topics(services: SorbobotServices, query: str) -> CandidateTopics:
    settings = services.settings
    matches = await services.taxi.match_topics(query)
    similarity = {match.uid: match.similarity for match in matches[: settings.max_search_topics]}
    matched_uids = list(similarity)
    concepts, paths = await asyncio.gather(
        services.graph.get_concepts(matched_uids, settings.topic_link_threshold),
        services.graph.get_concept_paths(matched_uids),
    )
    uids = [uid for uid in matched_uids if concepts.get(uid, {}).get("nb_docs", 0) > 0]
    if matched_uids and not uids:
        logger.warning("crisalid-taxi matched %d topic(s) for %r, none holds a document", len(matched_uids), query)
        raise NoDocumentedTopicError(f"No document is linked to the {len(matched_uids)} topic(s) matched for {query!r}")

    scores = None
    if services.concept_judge is not None:
        judged = await services.concept_judge.score(query, [concept_label(uid, concepts, paths) for uid in uids])
        if judged is not None:
            scores = {uid: judged[number] for number, uid in enumerate(uids, 1) if number in judged}
            if len(scores) < len(uids):
                logger.warning(
                    "The concept judge scored %d topic(s) out of %d: the others are set aside",
                    len(scores),
                    len(uids),
                )
    return CandidateTopics(uids, similarity, concepts, paths, scores)
