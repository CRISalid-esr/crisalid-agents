"""Exploration of research domains: what they cover and how they differ."""

from agents.sorbobot.models import DomainsResult, tag
from agents.sorbobot.services import SorbobotServices
from agents.sorbobot.topics import find_candidate_topics


async def explore_domains(services: SorbobotServices, topic: str) -> DomainsResult:
    """The topics judged relevant (at 0.5 when none reaches the threshold), most relevant first, with definitions."""
    settings = services.settings
    candidates = await find_candidate_topics(services, topic)
    kept = candidates.select(settings.judge_threshold) or candidates.select(
        settings.JUDGE_RETRY_THRESHOLD, with_unjudged=True
    )
    ranked = sorted(
        kept,
        key=lambda uid: (
            candidates.is_judged(uid),
            candidates.relevance(uid) or 0.0,
            candidates.similarity.get(uid, 0.0),
        ),
        reverse=True,
    )
    domains = tuple(
        candidates.domain(uid, tag(number), definition_chars=settings.TEXT_MAX_CHARS)
        for number, uid in enumerate(ranked[: settings.explore_max_domains], 1)
    )
    return DomainsResult(topic, domains)
