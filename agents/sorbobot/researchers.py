"""Searches about one researcher: expertise (§5.2) and profile (§5.3 of the spec)."""

import asyncio
import re

from agents.sorbobot.config import PROFILE_CANDIDATE_PUBLICATIONS, SorbobotSettings
from agents.sorbobot.judges import PublicationJudge
from agents.sorbobot.models import ROLE_ORDER, Domain, PersonConcept, PersonDocument, PersonHit, role_of, tag, tag_key, truncate, work_key, year_of

_SPECIAL_CHARS = re.compile(r'[+\-&|!(){}\[\]^"~*?:\\/]')


def clean_name(name) -> str:
    """Lowercase (so that AND/OR/NOT are not operators), special characters of the full-text query replaced."""
    cleaned = _SPECIAL_CHARS.sub(" ", str(name or "").lower())
    return " ".join(cleaned.split())


def merge_duplicates(documents: list[PersonDocument]) -> list[PersonDocument]:
    """One entry per work: the first row met keeps its place and gathers the roles and topics of its duplicates."""
    merged: dict[str, PersonDocument] = {}
    for document in documents:
        key = work_key(document.title, document.uid)
        kept = merged.get(key)
        if kept is None:
            merged[key] = PersonDocument(
                uid=document.uid, title=document.title, publication_date=document.publication_date,
                concept_uids=list(dict.fromkeys(document.concept_uids)), roles=list(dict.fromkeys(document.roles)),
            )
            continue
        kept.concept_uids.extend(uid for uid in document.concept_uids if uid not in kept.concept_uids)
        kept.roles.extend(role for role in document.roles if role not in kept.roles)
        if kept.publication_date is None:
            kept.publication_date = document.publication_date
    return list(merged.values())


class ResearcherSearch:
    def __init__(self, toolbox, publication_judge: PublicationJudge | None, settings: SorbobotSettings):
        self._toolbox = toolbox
        self._judge = publication_judge
        self._settings = settings

    async def find_person(self, person_name: str) -> PersonHit | None:
        """The most likely internal researcher of that name."""
        name = clean_name(person_name)
        if not name:
            return None
        hits = await self._toolbox.search_persons(name, 10)
        return next((hit for hit in hits if not hit.external), None)

    async def _domains_and_documents(self, person: PersonHit, publication_limit: int):
        settings = self._settings
        concepts: list[PersonConcept] = await self._toolbox.list_person_concepts(
            person.uid, settings.topic_link_threshold, settings.show_max_domains
        )
        uids = [concept.uid for concept in concepts]
        paths, details, documents = await asyncio.gather(
            self._toolbox.get_concept_paths(uids),
            self._toolbox.get_concepts(uids, settings.topic_link_threshold),
            self._toolbox.list_person_documents(person.uid, uids, settings.topic_link_threshold, publication_limit),
        )
        domains = []
        for number, concept in enumerate(concepts, 1):
            detail = details.get(concept.uid)
            domains.append(Domain(
                tag=tag(number), uid=concept.uid, name=concept.name or (detail.name if detail else ""),
                path=paths.get(concept.uid, concept.name), nb_publications=concept.nb_publications,
                definition=truncate(detail.description if detail else ""),
            ))
        return domains, documents

    @staticmethod
    def _tags(document: PersonDocument, domains: list[Domain]) -> list[str]:
        by_uid = {domain.uid: domain.tag for domain in domains}
        return sorted({by_uid[uid] for uid in document.concept_uids if uid in by_uid}, key=tag_key)

    async def expertise(self, person_name: str) -> dict:
        person = await self.find_person(person_name)
        if person is None:
            return {"person_name": person_name, "hint": "person_not_found"}
        domains, documents = await self._domains_and_documents(person, self._settings.show_max_person_publications)
        authored = merge_duplicates([document for document in documents if "aut" in document.roles])
        return {
            "person_uid": person.uid,
            "name": person.display_name,
            "domains": [domain.as_dict() for domain in domains],
            "publications": [
                {"title": document.title, "year": year_of(document.publication_date), "tags": self._tags(document, domains)}
                for document in authored
            ],
        }

    async def profile(self, person_name: str, question: str) -> dict:
        person = await self.find_person(person_name)
        if person is None:
            return {"person_name": person_name, "hint": "person_not_found"}
        domains, documents = await self._domains_and_documents(person, PROFILE_CANDIDATE_PUBLICATIONS)
        documents = merge_duplicates(documents)
        limit = self._settings.profile_max_publications
        if len(documents) > limit:
            documents = await self._judge.select(question, documents, limit) if self._judge else documents[:limit]
        summaries = await self._toolbox.get_document_summaries([document.uid for document in documents])
        ordered = sorted(documents, key=lambda document: ROLE_ORDER.index(role_of(document.roles)))
        publications = []
        for number, document in enumerate(ordered, 1):
            summary = summaries.get(document.uid)
            publications.append({
                "reference": f"[{number}]",
                "role": role_of(document.roles),
                "title": document.title,
                "year": year_of(document.publication_date),
                "tags": self._tags(document, domains),
                "keywords": list(summary.keywords) if summary else [],
                "abstract": truncate(summary.abstract) if summary and summary.abstract else "",
            })
        return {
            "person_uid": person.uid,
            "name": person.display_name,
            "question": question,
            "domains": [domain.as_dict() for domain in domains],
            "publications": publications,
        }
