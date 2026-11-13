"""Steps shared by the expert search and the domain exploration: taxi topics -> documented topics -> judged topics
(§5.1 steps 1 to 4 of the spec)."""

import asyncio
import logging
from dataclasses import dataclass, field

from agents.sorbobot.config import SorbobotSettings
from agents.sorbobot.judges import TopicJudge
from agents.sorbobot.models import Concept, Domain, TopicMatch, tag, truncate

logger = logging.getLogger(__name__)


@dataclass
class Topic:
    uid: str
    concept: Concept
    path: str
    similarity: float | None = None  # question similarity given by taxi; None for a sibling
    judge_score: float | None = None

    def as_domain(self, number: int) -> Domain:
        return Domain(
            tag=tag(number), uid=self.uid, name=self.concept.name, path=self.path or self.concept.name,
            nb_publications=self.concept.nb_docs, definition=truncate(self.concept.description),
        )


@dataclass
class TopicSelection:
    """What the first steps of a search found."""

    matched: int = 0  # topics returned by taxi (kept ones)
    documented: list[Topic] = field(default_factory=list)
    kept: list[Topic] = field(default_factory=list)
    judge_failed: bool = False
    hint: str | None = None


class TopicFinder:
    def __init__(self, taxi, toolbox, judge: TopicJudge | None, settings: SorbobotSettings):
        self._taxi = taxi
        self._toolbox = toolbox
        self._judge = judge
        self._settings = settings

    async def describe(self, uids: list[str], similarities: dict[str, float] | None = None) -> list[Topic]:
        """Information and paths of the topics, read in parallel; topics without publications are discarded."""
        if not uids:
            return []
        threshold = self._settings.topic_link_threshold
        concepts, paths = await asyncio.gather(
            self._toolbox.get_concepts(uids, threshold), self._toolbox.get_concept_paths(uids)
        )
        topics = []
        for uid in uids:
            concept = concepts.get(uid)
            if concept is None or concept.nb_docs <= 0:
                continue
            topics.append(Topic(uid=uid, concept=concept, path=paths.get(uid, concept.name),
                                similarity=(similarities or {}).get(uid)))
        return topics

    async def judge(self, question: str, topics: list[Topic]) -> tuple[list[Topic], bool]:
        """(topics scored at least the threshold, judge failed). Without a judge, every topic is kept."""
        if self._judge is None or not topics:
            return list(topics), False
        scores = await self._judge.score(question, [topic.path for topic in topics])
        if scores is None:
            return list(topics), True
        kept = []
        for number, topic in enumerate(topics, 1):
            topic.judge_score = scores.get(number)
            if topic.judge_score is not None and topic.judge_score >= self._settings.judge_threshold:
                kept.append(topic)
        return kept, False

    async def select(self, question: str) -> TopicSelection:
        matches: list[TopicMatch] = (await self._taxi.match_topics(question))[: self._settings.max_search_topics]
        selection = TopicSelection(matched=len(matches))
        if not matches:
            return selection
        selection.documented = await self.describe(
            [match.uid for match in matches], {match.uid: match.similarity for match in matches}
        )
        if not selection.documented:
            logger.warning("None of the %d topic(s) matched for %r has a publication", len(matches), question)
            selection.hint = "no_document"
            return selection
        selection.kept, selection.judge_failed = await self.judge(question, selection.documented)
        return selection
