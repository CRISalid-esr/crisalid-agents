"""Experts of a research topic: relevant topics, concept walk, then ranking of the researchers."""

import asyncio
import logging
from collections import Counter
from time import perf_counter

from agents.sorbobot.models import Domain, Expert, ExpertsResult, Publication, sorted_tags, tag
from agents.sorbobot.ranking import ResearcherSignals, build_signals, score_researchers
from agents.sorbobot.services import SorbobotServices
from agents.sorbobot.topics import CandidateTopics, build_domain, find_candidate_topics

logger = logging.getLogger(__name__)


async def find_domain_experts(services: SorbobotServices, topic: str) -> ExpertsResult:
    started = perf_counter()
    candidates = await find_candidate_topics(services, topic)
    judged_uids, evidence = await _select_topics(services, candidates)
    searched_uids, evidence = await _walk_concepts(services, judged_uids, evidence)
    domains = await _shown_domains(services, candidates, judged_uids, searched_uids, evidence)
    experts = await _rank_experts(services, evidence, candidates.similarity, domains)
    logger.info(
        "Experts of %r in %.1fs: %d documented topic(s), %d judged relevant, %d searched after the walk, "
        "%d evidence row(s), %d expert(s)",
        topic,
        perf_counter() - started,
        len(candidates.uids),
        len(judged_uids),
        len(searched_uids),
        len(evidence),
        len(experts),
    )
    return ExpertsResult(topic, services.settings.ranking_method, domains if experts else (), experts)


def _column(evidence: list[dict], key: str) -> list[str]:
    """The values the rows carry under ``key``, the rows missing it left out."""
    return [row[key] for row in evidence if row.get(key)]


def _count_authors(evidence: list[dict]) -> int:
    return len(set(_column(evidence, "person_uid")))


async def _select_topics(services: SorbobotServices, candidates: CandidateTopics) -> tuple[list[str], list[dict]]:
    """The topics judged relevant."""
    settings = services.settings
    judged_uids = candidates.select(settings.judge_threshold)
    evidence = await services.graph.list_expert_evidence(judged_uids, settings.topic_link_threshold)
    if _count_authors(evidence) < settings.min_authors:
        retry = candidates.select(settings.JUDGE_RETRY_THRESHOLD, with_unjudged=True)
        if len(retry) > len(judged_uids):
            judged_uids = retry
            evidence = await services.graph.list_expert_evidence(judged_uids, settings.topic_link_threshold)
    return judged_uids, evidence


async def _walk_concepts(
    services: SorbobotServices, uids: list[str], evidence: list[dict]
) -> tuple[list[str], list[dict]]:
    """Adaptive search: move to the children when researchers are too many, add the siblings when too few."""
    settings = services.settings
    for _ in range(settings.MAX_WALK_STEPS):
        nb_authors = _count_authors(evidence)
        if settings.min_authors <= nb_authors <= settings.max_authors:
            break
        if nb_authors > settings.max_authors:
            neighbours = await services.graph.list_children(uids)
        else:
            siblings = await services.graph.list_siblings(uids)
            neighbours = sorted(set(uids) | set(siblings)) if siblings else []
        if not neighbours or set(neighbours) == set(uids):
            break
        neighbour_evidence = await services.graph.list_expert_evidence(neighbours, settings.topic_link_threshold)
        if not neighbour_evidence:
            break
        uids, evidence = neighbours, neighbour_evidence
    return uids, evidence


async def _shown_domains(
    services: SorbobotServices,
    candidates: CandidateTopics,
    judged_uids: list[str],
    searched_uids: list[str],
    evidence: list[dict],
) -> tuple[Domain, ...]:
    """The judged topics the search ended on, most similar first. Topics added by the walk were never judged."""
    searched = set(searched_uids)
    displayed_uids = [uid for uid in judged_uids if uid in searched]
    if not displayed_uids:
        return await _walked_domains(services, evidence)
    displayed_uids.sort(key=lambda uid: candidates.similarity.get(uid, 0.0), reverse=True)
    return tuple(
        candidates.domain(uid, tag(number))
        for number, uid in enumerate(displayed_uids[: services.settings.show_max_domains], 1)
    )


async def _walked_domains(services: SorbobotServices, evidence: list[dict]) -> tuple[Domain, ...]:
    """The walk left the judged topics: show the topics the evidence actually comes from, richest first."""
    documented = Counter(_column(evidence, "topic_uid"))
    displayed_uids = [uid for uid, _ in documented.most_common(services.settings.show_max_domains)]
    if not displayed_uids:
        return ()
    concepts, paths = await asyncio.gather(
        services.graph.get_concepts(displayed_uids, services.settings.topic_link_threshold),
        services.graph.get_concept_paths(displayed_uids),
    )
    return tuple(build_domain(uid, tag(number), concepts, paths) for number, uid in enumerate(displayed_uids, 1))


async def _rank_experts(
    services: SorbobotServices, evidence: list[dict], similarity: dict[str, float], domains: tuple[Domain, ...]
) -> tuple[Expert, ...]:
    if not evidence:
        return ()
    settings = services.settings
    totals = await services.graph.get_publication_counts(sorted(set(_column(evidence, "person_uid"))))
    researchers = build_signals(evidence, totals)
    scores = score_researchers(researchers, similarity, settings.ranking_method)
    best = sorted(researchers, key=lambda r: scores[r.person_uid], reverse=True)[: settings.show_max_authors]
    tag_of = {domain.uid: domain.tag for domain in domains}
    return tuple(_expert(services, researcher, scores[researcher.person_uid], tag_of) for researcher in best)


def _expert(services: SorbobotServices, researcher: ResearcherSignals, score: float, tag_of: dict[str, str]) -> Expert:
    recent = sorted(researcher.documents, key=lambda d: d.year or 0, reverse=True)
    topic_uids = {uid for document in researcher.documents for uid, _ in document.topic_links}
    return Expert(
        person_uid=researcher.person_uid,
        name=researcher.name,
        score=round(score),
        tags=sorted_tags(tag_of[uid] for uid in topic_uids if uid in tag_of),
        nb_publications_on_topic=len(researcher.documents),
        nb_publications_total=researcher.total_publications,
        recent_publications=tuple(
            Publication(document.title, document.year)
            for document in recent[: services.settings.RECENT_PUBLICATIONS_PER_EXPERT]
        ),
    )
