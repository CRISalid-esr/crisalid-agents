"""Ranking of the researchers found by an expert search (§6.3 of the spec). Pure functions, no service call."""

import math
from dataclasses import dataclass, field
from statistics import median

from agents.sorbobot.models import EvidenceRow, work_key, year_of


@dataclass
class Work:
    """One work of a researcher on the subject: duplicates of the graph are merged into it (§6.4)."""

    title: str
    year: int | None
    similarities: dict[str, float] = field(default_factory=dict)  # topic uid -> best link similarity


@dataclass
class RankedExpert:
    person_uid: str
    name: str
    score: int
    works: list[Work]
    topic_uids: set[str]
    nb_publications_total: int

    @property
    def nb_publications_on_topic(self) -> int:
        return len(self.works)

    def recent_works(self, count: int) -> list[Work]:
        return sorted(self.works, key=lambda work: (work.year is None, -(work.year or 0)))[:count]


def group_evidence(evidence: list[EvidenceRow]) -> dict[str, tuple[str, dict[str, Work]]]:
    """person uid -> (display name, work key -> Work), with one work per title and the best similarity per topic."""
    grouped: dict[str, tuple[str, dict[str, Work]]] = {}
    for row in evidence:
        name, works = grouped.setdefault(row.person_uid, (row.display_name, {}))
        key = work_key(row.doc_title, row.doc_uid)
        work = works.get(key)
        if work is None:
            work = works[key] = Work(title=row.doc_title, year=year_of(row.publication_date))
        elif work.year is None:
            work.year = year_of(row.publication_date)
        work.similarities[row.topic_uid] = max(work.similarities.get(row.topic_uid, 0.0), row.similarity)
    return grouped


def rank_experts(
    evidence: list[EvidenceRow],
    query_similarity: dict[str, float],
    totals: dict[str, int],
    method: str,
    current_year: int,
) -> list[RankedExpert]:
    grouped = group_evidence(evidence)
    if not grouped:
        return []
    years = [work.year for _, works in grouped.values() for work in works.values() if work.year is not None]
    fallback_year = int(median(years)) if years else current_year
    # A topic without a question similarity (a sibling) takes the lowest similarity given by taxi.
    fallback_similarity = min(query_similarity.values()) if query_similarity else 1.0

    def weight(work: Work) -> float:
        closeness = max(
            query_similarity.get(topic_uid, fallback_similarity) * link for topic_uid, link in work.similarities.items()
        )
        age = max(0, current_year - (work.year if work.year is not None else fallback_year))
        return closeness * math.pow(2.0, -age / 5.0)

    raw: dict[str, float] = {}
    share: dict[str, float] = {}
    for person_uid, (_, works) in grouped.items():
        if method == "count":
            raw[person_uid] = float(len(works))
        else:
            weighted = sum(weight(work) for work in works.values())
            total = totals.get(person_uid)
            if total is None:
                total = len(works)
            raw[person_uid] = weighted
            share[person_uid] = weighted / (total + 1)

    max_raw = max(raw.values()) or 1.0
    max_share = max(share.values()) if share else 1.0
    ranked = []
    for person_uid, (name, works) in grouped.items():
        if method == "count":
            score = 100 * raw[person_uid] / max_raw
        else:
            score = 100 * (raw[person_uid] / max_raw) * (share[person_uid] / (max_share or 1.0))
        ranked.append(RankedExpert(
            person_uid=person_uid, name=name, score=int(round(score)), works=list(works.values()),
            topic_uids={uid for work in works.values() for uid in work.similarities},
            nb_publications_total=totals.get(person_uid, len(works)),
        ))
    ranked.sort(key=lambda expert: (-expert.score, -expert.nb_publications_on_topic, expert.name))
    return ranked
