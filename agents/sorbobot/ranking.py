"""Rank the researchers of a domain expert query."""

from dataclasses import dataclass
from datetime import UTC, datetime

from agents.sorbobot.models import as_number

RANKING_METHODS = ("count", "weighted_relevance")
RECENCY_HALF_LIFE_YEARS = 5.0


@dataclass(frozen=True)
class DocumentSignal:
    document_uid: str
    title: str
    year: int | None
    topic_links: tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class ResearcherSignals:
    person_uid: str
    name: str
    documents: tuple[DocumentSignal, ...]
    total_publications: int


def _this_year() -> int:
    return datetime.now(UTC).year


def publication_year(raw_date: object) -> int | None:
    text = str(raw_date or "")[:4]
    return int(text) if text.isdigit() else None


def build_signals(evidence_rows: list[dict], total_counts: dict[str, int]) -> list[ResearcherSignals]:
    """One entry per researcher; a document linked to several searched topics keeps every link."""
    names: dict[str, str] = {}
    documents: dict[str, dict[str, dict]] = {}
    for row in evidence_rows:
        person_uid, doc_uid = row.get("person_uid"), row.get("doc_uid")
        if not person_uid or not doc_uid:
            continue
        names[person_uid] = row.get("display_name") or person_uid
        document = documents.setdefault(person_uid, {}).setdefault(
            doc_uid,
            {
                "title": row.get("doc_title") or "",
                "year": publication_year(row.get("publication_date")),
                "links": {},
            },
        )
        similarity = as_number(row.get("similarity"), fallback=0.0)
        if row.get("topic_uid"):
            document["links"][row["topic_uid"]] = similarity

    researchers = []
    for person_uid, person_documents in documents.items():
        signals = [
            DocumentSignal(uid, document["title"], document["year"], tuple(document["links"].items()))
            for uid, document in person_documents.items()
        ]
        total = total_counts.get(person_uid, len(signals))
        researchers.append(ResearcherSignals(person_uid, names[person_uid], tuple(signals), total))
    return researchers


def recency(year: int | None, median_year: int, current_year: int | None = None) -> float:
    """1 for a publication of this year, 0.5 five years ago; an undated one takes the median year."""
    effective_year = year if year is not None else median_year
    return 2.0 ** (-((current_year or _this_year()) - effective_year) / RECENCY_HALF_LIFE_YEARS)


def score_researchers(
    researchers: list[ResearcherSignals], query_similarity: dict[str, float], method: str
) -> dict[str, float]:
    """Person uid -> score on 0-100. ``query_similarity`` maps a topic uid to its similarity with the query."""
    if method not in RANKING_METHODS:
        raise ValueError(f"Unknown ranking method {method!r}, expected one of {RANKING_METHODS}")
    if not researchers:
        return {}
    if method == "count":
        return _relative({r.person_uid: float(len(r.documents)) for r in researchers})

    current_year = _this_year()
    years = sorted(d.year for r in researchers for d in r.documents if d.year is not None)
    median_year = years[len(years) // 2] if years else current_year
    floor = min(query_similarity.values(), default=0.0)
    mass = {r.person_uid: _relevance_mass(r, query_similarity, floor, median_year, current_year) for r in researchers}
    focus = {r.person_uid: mass[r.person_uid] / (r.total_publications + 1) for r in researchers}
    relative_mass, relative_focus = _relative(mass), _relative(focus)
    return {uid: relative_mass[uid] * relative_focus[uid] / 100.0 for uid in mass}


def _relevance_mass(
    researcher: ResearcherSignals,
    query_similarity: dict[str, float],
    floor: float,
    median_year: int,
    current_year: int,
) -> float:
    return sum(
        max((query_similarity.get(uid, floor) * similarity for uid, similarity in document.topic_links), default=0.0)
        * recency(document.year, median_year, current_year)
        for document in researcher.documents
    )


def _relative(values: dict[str, float]) -> dict[str, float]:
    """Scale the values to a 0-100 range, where the maximum value becomes 100."""
    top = max(values.values(), default=0.0)
    return {uid: (100.0 * value / top if top else 0.0) for uid, value in values.items()}
