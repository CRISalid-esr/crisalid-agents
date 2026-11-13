"""Expert search: the internal researchers working on a research theme (§5.1 of the spec)."""

import logging
import time
from datetime import date

from agents.sorbobot.config import RECENT_PUBLICATIONS, SorbobotSettings
from agents.sorbobot.models import tag_key
from agents.sorbobot.ranking import rank_experts
from agents.sorbobot.topics import Topic, TopicFinder, TopicSelection

logger = logging.getLogger(__name__)


class ExpertSearch:
    def __init__(self, finder: TopicFinder, toolbox, settings: SorbobotSettings):
        self._finder = finder
        self._toolbox = toolbox
        self._settings = settings

    async def run(self, topic_text: str) -> dict:
        started = time.monotonic()
        settings = self._settings
        selection: TopicSelection = await self._finder.select(topic_text)
        if selection.hint:
            return {"topic": topic_text, "experts": [], "hint": selection.hint}
        kept = selection.kept
        threshold = settings.topic_link_threshold

        evidence = await self._toolbox.list_expert_evidence([t.uid for t in kept], threshold)
        siblings: list[Topic] = []
        if kept and len({row.person_uid for row in evidence}) < settings.min_authors:
            siblings = await self._widen_to_siblings(topic_text, kept)
            if siblings:
                widened = await self._toolbox.list_expert_evidence([t.uid for t in kept + siblings], threshold)
                if widened:
                    evidence = widened

        totals = await self._toolbox.get_publication_counts(sorted({row.person_uid for row in evidence}))
        similarities = {t.uid: t.similarity for t in kept if t.similarity is not None}
        ranked = rank_experts(evidence, similarities, totals, settings.ranking_method, date.today().year)
        logger.info(
            "Expert search %r: %.1fs, %d taxi topic(s), %d documented, %d kept by the judge, %d sibling(s) added, "
            "%d evidence row(s), %d expert(s)",
            topic_text, time.monotonic() - started, selection.matched, len(selection.documented), len(kept),
            len(siblings), len(evidence), len(ranked),
        )
        if not ranked:
            return {"topic": topic_text, "experts": [], "hint": "no_expert"}

        ordered = sorted(kept, key=lambda t: -(t.similarity or 0.0)) + sorted(siblings, key=lambda t: -(t.judge_score or 0.0))
        domains = [topic.as_domain(number) for number, topic in enumerate(ordered[: settings.show_max_domains], 1)]
        tags_by_uid = {domain.uid: domain.tag for domain in domains}
        experts = []
        for expert in ranked[: settings.show_max_authors]:
            tags = sorted({tags_by_uid[uid] for uid in expert.topic_uids if uid in tags_by_uid}, key=tag_key)
            experts.append({
                "person_uid": expert.person_uid,
                "name": expert.name,
                "score": expert.score,
                "tags": tags,
                "nb_publications_on_topic": expert.nb_publications_on_topic,
                "nb_publications_total": expert.nb_publications_total,
                "recent_publications": [
                    {"title": work.title, "year": work.year} for work in expert.recent_works(RECENT_PUBLICATIONS)
                ],
            })
        return {
            "topic": topic_text,
            "ranking_method": settings.ranking_method,
            "domains": [domain.as_dict() for domain in domains],
            "experts": experts,
        }

    async def _widen_to_siblings(self, question: str, kept: list[Topic]) -> list[Topic]:
        """Step 6: the documented siblings of the kept topics that the judge accepts (all of them without a judge)."""
        sibling_uids = await self._toolbox.list_siblings([t.uid for t in kept])
        documented = await self._finder.describe(sibling_uids)
        documented.sort(key=lambda t: -t.concept.nb_docs)
        candidates = documented[: self._settings.max_judged_siblings]
        if not candidates:
            return []
        accepted, judge_failed = await self._finder.judge(question, candidates)
        if judge_failed:
            logger.warning("Sibling judgement failed for %r: no sibling added", question)
            return []
        return accepted
