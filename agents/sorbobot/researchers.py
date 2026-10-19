"""A named researcher: their expertise, and a profile answering a question about it."""

import asyncio

from agents.sorbobot.models import (
    ROLES,
    CitedPublication,
    Domain,
    ExpertiseResult,
    ProfileResult,
    Publication,
    shorten,
    sorted_tags,
    tag,
)
from agents.sorbobot.ranking import publication_year
from agents.sorbobot.services import SorbobotServices

LUCENE_SPECIAL_CHARACTERS = '+-&|!(){}[]^"~*?:\\/'
MAX_PERSON_CANDIDATES = 10
MAX_OTHER_CANDIDATES = 3


def clean_person_name(name: str) -> str:
    """Lower case turns AND, OR and NOT into plain words; Lucene operators become spaces."""
    without_operators = "".join(" " if char in LUCENE_SPECIAL_CHARACTERS else char for char in name.lower())
    return " ".join(without_operators.split())


async def get_person_expertise(services: SorbobotServices, person_name: str) -> ExpertiseResult | None:
    found = await _find_person(services, person_name)
    if found is None:
        return None
    person, other_candidates = found
    settings = services.settings

    concept_rows = await _person_concepts(services, person["uid"])
    uids = [row["domain_uid"] for row in concept_rows]
    paths, rows = await asyncio.gather(
        services.graph.get_concept_paths(uids),
        services.graph.list_person_documents(
            person["uid"], uids, settings.topic_link_threshold, settings.show_max_person_publications
        ),
    )
    domains = _person_domains(concept_rows, paths)
    tag_of = {domain.uid: domain.tag for domain in domains}
    publications = tuple(
        Publication(
            row.get("title") or "",
            publication_year(row.get("publication_date")),
            _tags(row, tag_of),
            _main_role(row),
        )
        for row in rows
    )
    return ExpertiseResult(person["uid"], _display_name(person), domains, publications, other_candidates)


async def get_researcher_profile(services: SorbobotServices, person_name: str, question: str) -> ProfileResult | None:
    found = await _find_person(services, person_name)
    if found is None:
        return None
    person, _ = found
    settings = services.settings

    concept_rows = await _person_concepts(services, person["uid"])
    uids = [row["domain_uid"] for row in concept_rows]
    paths, candidates = await asyncio.gather(
        services.graph.get_concept_paths(uids),
        services.graph.list_person_documents(
            person["uid"], uids, settings.topic_link_threshold, settings.MAX_JUDGED_PUBLICATIONS
        ),
    )
    domains = _person_domains(concept_rows, paths)
    if services.publication_judge is not None:
        selected = await services.publication_judge.select(question, candidates, settings.profile_max_publications)
    else:
        selected = candidates[: settings.profile_max_publications]

    summaries = await services.graph.get_document_summaries([row["doc_uid"] for row in selected if row.get("doc_uid")])
    publications = _cite(selected, summaries, {d.uid: d.tag for d in domains}, settings.TEXT_MAX_CHARS)
    return ProfileResult(person["uid"], _display_name(person), question, domains, publications)


async def _find_person(services: SorbobotServices, person_name: str) -> tuple[dict, tuple[str, ...]] | None:
    """The best internal candidate of the name search, and the names of the next ones."""
    cleaned_name = clean_person_name(person_name)
    if not cleaned_name:
        return None
    candidates = await services.graph.search_persons(cleaned_name, MAX_PERSON_CANDIDATES)
    internal = [candidate for candidate in candidates if candidate.get("uid") and not candidate.get("external")]
    if not internal:
        return None
    return internal[0], tuple(_display_name(c) for c in internal[1 : 1 + MAX_OTHER_CANDIDATES])


def _display_name(person: dict) -> str:
    return person.get("display_name") or person["uid"]


async def _person_concepts(services: SorbobotServices, person_uid: str) -> list[dict]:
    """The concepts the person publishes on, most published first, every row naming one."""
    settings = services.settings
    rows = await services.graph.list_person_concepts(
        person_uid, settings.topic_link_threshold, settings.show_max_domains
    )
    return [row for row in rows if row.get("domain_uid")]


def _person_domains(concept_rows: list[dict], paths: dict[str, str]) -> tuple[Domain, ...]:
    """The concepts of ``_person_concepts``, tagged #1, #2… in their order."""
    domains = []
    for number, row in enumerate(concept_rows, 1):
        uid = row["domain_uid"]
        name = row.get("domain_name") or uid
        domains.append(Domain(tag(number), uid, name, paths.get(uid, name), row.get("nb_publications") or 0))
    return tuple(domains)


def _cite(
    rows: list[dict], summaries: dict[str, dict], tag_of: dict[str, str], max_chars: int
) -> tuple[CitedPublication, ...]:
    """Number the publications [1], [2]… grouped by role: author, thesis supervisor, speaker, then other."""
    role_order = [*ROLES.values(), "other"]
    ordered = sorted(rows, key=lambda row: role_order.index(_main_role(row)))
    cited = []
    for number, row in enumerate(ordered, 1):
        summary = summaries.get(row.get("doc_uid") or "", {})
        cited.append(
            CitedPublication(
                reference=f"[{number}]",
                role=_main_role(row),
                title=row.get("title") or "",
                year=publication_year(row.get("publication_date")),
                tags=_tags(row, tag_of),
                keywords=tuple(summary.get("keywords") or ()),
                abstract=shorten(summary.get("abstract"), max_chars),
            )
        )
    return tuple(cited)


def _main_role(row: dict) -> str:
    return next((name for code, name in ROLES.items() if code in (row.get("roles") or [])), "other")


def _tags(row: dict, tag_of: dict[str, str]) -> tuple[str, ...]:
    return sorted_tags(tag_of[uid] for uid in row.get("concept_uids") or () if uid in tag_of)
