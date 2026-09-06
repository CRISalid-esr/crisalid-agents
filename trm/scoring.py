"""Pure scoring functions of the TRM pipeline."""

import re
from datetime import date

from trm.config import TRMSettings
from trm.models import Candidate, Publication, ResearcherMatch, Verification


def publication_year(value) -> int | None:
    if value is None:
        return None
    m = re.search(r"(19|20)\d{2}", str(value))
    return int(m.group(0)) if m else None


def rank_publications(results: dict[str, list[dict]], rank_constant: int) -> dict[str, Publication]:
    """Merge the per-query result lists: RRF over the ranks, scaled by the best cosine score."""
    publications: dict[str, Publication] = {}
    for query, rows in results.items():
        for rank, row in enumerate(rows, start=1):
            uid = row.get("uid")
            if not uid:
                continue
            score = float(row.get("score") or 0.0)
            pub = publications.get(uid)
            if pub is None:
                titles = row.get("titles") or []
                abstracts = row.get("abstracts") or []
                pub = Publication(
                    uid=uid,
                    title=str(titles[0]) if titles else "",
                    abstract=str(abstracts[0]) if abstracts else None,
                    year=publication_year(row.get("publication_date")),
                    best_score=score,
                    contributors=[{"uid": c["uid"], "name": c.get("name") or c["uid"]}
                                  for c in row.get("contributors") or [] if c and c.get("uid")],
                )
                publications[uid] = pub
            pub.best_score = max(pub.best_score, score)
            pub.query_ranks[query] = min(rank, pub.query_ranks.get(query, rank))
    for pub in publications.values():
        pub.rrf = sum(1.0 / (rank_constant + r) for r in pub.query_ranks.values())
        pub.score = pub.rrf * pub.best_score
    return publications


def build_candidates(publications: dict[str, Publication], settings: TRMSettings) -> list[Candidate]:
    by_person: dict[str, list[Publication]] = {}
    names: dict[str, str] = {}
    for pub in publications.values():
        for c in pub.contributors:
            by_person.setdefault(c["uid"], []).append(pub)
            names.setdefault(c["uid"], c["name"])
    candidates = []
    for uid, pubs in by_person.items():
        pubs = sorted(pubs, key=lambda p: -p.score)
        pre_score = sum(p.score for p in pubs[:settings.top_publications_for_prescore])
        candidates.append(Candidate(person_uid=uid, name=names[uid], publications=pubs, pre_score=pre_score))
    candidates.sort(key=lambda c: -c.pre_score)
    return candidates[:settings.max_candidates]


def recency_weight(year: int | None, settings: TRMSettings, today: date | None = None) -> float:
    if year is None:
        return 1.0
    age = (today or date.today()).year - year
    for max_age, weight in settings.recency:
        if age <= max_age:
            return weight
    return settings.recency_floor


def is_retained(verification: Verification) -> bool:
    return verification.overall != "none" and bool(verification.relevant_uids)


def final_matches(
    topic_id: str,
    verified: list[tuple[Candidate, Verification]],
    units: dict[str, list[str]],
    settings: TRMSettings,
    today: date | None = None,
) -> list[ResearcherMatch]:
    """Score retained candidates, normalise within the topic, apply the threshold and the cap."""
    scored = []
    for candidate, verification in verified:
        if not is_retained(verification):
            continue
        reasons = {v.uid: v for v in verification.verdicts}
        raw = sum(
            p.score * recency_weight(p.year, settings, today)
            for p in candidate.publications if p.uid in verification.relevant_uids
        )
        publications = [
            {"uid": p.uid, "title": p.title, "year": p.year, "score": round(p.score, 6),
             "relevant": p.uid in verification.relevant_uids,
             "reason": reasons[p.uid].reason if p.uid in reasons else ""}
            for p in candidate.publications[:settings.publications_per_candidate]
        ]
        scored.append((candidate, verification, raw, publications))
    if not scored:
        return []
    best = max(raw for _, _, raw, _ in scored) or 1.0
    matches = []
    for candidate, verification, raw, publications in scored:
        score = raw / best
        if score < settings.min_score:
            continue
        matches.append(ResearcherMatch(
            pair_id=f"{topic_id}|{candidate.person_uid}",
            person_uid=candidate.person_uid,
            name=candidate.name,
            units=units.get(candidate.person_uid, []),
            score=round(score, 4),
            raw_score=round(raw, 6),
            overall=verification.overall,
            justification=verification.justification,
            publications=publications,
        ))
    matches.sort(key=lambda m: -m.score)
    return matches[:settings.max_researchers]
