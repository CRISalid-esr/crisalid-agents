"""Async domain-search, judging and author-lookup helpers for the SorboBot agent.
"""

import json
import logging
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from sorbobot_agent.mcp_toolbox import McpToolboxClient

logger = logging.getLogger("sorbobot_agent.domain_tools")

_PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
_JUDGE_SYSTEM = (_PROMPT_DIR / "judge_domains_system.md").read_text(encoding="utf-8")
_JUDGE_USER = "User query: {query}\n\nDomains to score:\n{domain_list}\n\nScores:"

_TYPE_DEPTH = {"domain": 2, "field": 3, "subfield": 4, "topic": 5}


# ── Domain search ──────────────────────────────────────────────────────────────

_TYPE_LABELS = {
    "domain": "Domain",
    "field": "Field",
    "subfield": "Subfield",
    "topic": "Topic",
}


def format_type_label(type_value: Optional[str]) -> str:
    """Render a Concept.type value as a display label (e.g. "topic" -> "Topic")."""
    return _TYPE_LABELS.get((type_value or "").lower(), "")


def format_hierarchy_label(hierarchy: List[dict]) -> str:
    """Render an ancestor chain (Domain -> ... -> matched node, as returned by
    `fetch_hierarchies`) as "Name [Type] > Name [Type] > ..."."""
    parts = []
    for h in hierarchy:
        name = h.get("name") or "?"
        type_label = format_type_label(h.get("type"))
        parts.append(f"{name} [{type_label}]" if type_label else name)
    return " > ".join(parts)


async def fetch_hierarchies(
    toolbox: McpToolboxClient, uids: List[str]
) -> Dict[str, List[dict]]:
    """Batch-fetch each uid's full ancestor chain via `get-concept-hierarchy`.

    Returns {uid: [{"uid", "name", "type"}, ...]} ordered from Domain (root)
    down to the uid's own entry, inclusive.
    """
    uids = [u for u in dict.fromkeys(uids) if u]
    if not uids:
        return {}
    rows = await toolbox.call("sorbobot-get-concept-hierarchy", uids=",".join(uids))
    hierarchies: Dict[str, List[dict]] = {}
    for row in rows:
        hierarchies.setdefault(row["uid"], []).append(
            {
                "uid": row.get("ancestor_uid"),
                "name": row.get("ancestor_name"),
                "type": row.get("ancestor_type"),
            }
        )
    return hierarchies


async def search_domains(
    toolbox: McpToolboxClient,
    keyword: str,
    threshold: float = 0.63,
    limit: int = 20,
    taxi_url: Optional[str] = None,
) -> List[dict]:
    """Find candidate Concept:Topic nodes (OpenAlex taxonomy) for `keyword`.
    """
    logger.info(
        "crisalid-taxi request: POST %s/api/v1/match/ keyword=%r threshold=%.2f",
        taxi_url, keyword, threshold,
    )
    async with httpx.AsyncClient(base_url=taxi_url, timeout=15.0) as client:
        # Trailing slash required: crisalid-taxi 307-redirects "/match" -> "/match/",
        # and httpx raises on redirect responses when not following them.
        response = await client.post(
            "/api/v1/match/",
            json={"inputs": [{"id": "query", "text": keyword}], "similarity_threshold": threshold},
        )
        response.raise_for_status()
        payload = response.json()

    matches = payload["results"][0]["matches"] if payload.get("results") else []
    logger.info(
        "crisalid-taxi response: %d match(es) for keyword=%r", len(matches), keyword
    )

    topic_matches = [m for m in matches if m.get("rel_type") == "HAS_TOPIC"]
    similarity_by_uid = {m["concept_uid"]: m["value"] for m in topic_matches}
    logger.info(
        "search_domains: %d/%d match(es) kept after Topic-only filter (keyword=%r)",
        len(topic_matches), len(matches), keyword,
    )

    if not similarity_by_uid:
        logger.info(
            "search_domains: no candidate Topic — returning no domains (keyword=%r)",
            keyword,
        )
        return []

    domains = await toolbox.call(
        "sorbobot-get-domains-by-uid",
        uids=",".join(similarity_by_uid),
        similarity_threshold=threshold,
    )
    logger.info(
        "search_domains: get-domains-by-uid -> %d domain(s) (keyword=%r)",
        len(domains), keyword,
    )

    hierarchies = await fetch_hierarchies(
        toolbox, [d["uid"] for d in domains if d.get("uid")]
    )
    for d in domains:
        hierarchy = hierarchies.get(d.get("uid"), [])
        d["hierarchy"] = hierarchy
        d["hierarchy_label"] = format_hierarchy_label(hierarchy)
        d["taxi_similarity"] = similarity_by_uid.get(d.get("uid"))

    return domains[:limit]


# ── Judge ──────────────────────────────────────────────────────────────────────


def _judge_score_heuristic(keywords: List[str], domain: dict) -> float:
    """
    Combines keyword overlap on name/hierarchy + depth fitness.
    Returns a float in [0, 1].
    """
    name = domain.get("name", "").lower()
    hierarchy_label = (domain.get("hierarchy_label") or "").lower()
    depth = _TYPE_DEPTH.get((domain.get("type") or "").lower(), 3)
    kw_lower = [kw.lower() for kw in keywords]

    kw_hits = 0.0
    for kw in kw_lower:
        kw_words = [w for w in kw.split() if len(w) > 2]
        if kw in name or kw in hierarchy_label:
            kw_hits += 1.0
        elif kw_words and all(w in name or w in hierarchy_label for w in kw_words):
            kw_hits += 0.7
        elif kw_words and any(w in name or w in hierarchy_label for w in kw_words):
            kw_hits += 0.4
    kw_score = kw_hits / max(len(kw_lower), 1)

    fuzzy = (
        max(SequenceMatcher(None, name, kw).ratio() for kw in kw_lower)
        if kw_lower
        else 0.0
    )

    depth_score = max(0.0, 1.0 - abs(depth - 3) * 0.15)
    return kw_score * 0.60 + fuzzy * 0.25 + depth_score * 0.15


def _fmt_domain_for_llm(d: dict) -> str:
    """Format a domain dict as a single line for the judge prompt."""
    hierarchy_label = d.get("hierarchy_label") or d.get("name", "?")
    type_label = format_type_label(d.get("type"))
    desc = d.get("description") or ""
    base = f"- Name: {d.get('name', '?')} | Type: {type_label or '?'} | Path: {hierarchy_label}"
    return f"{base} | Description: {desc}" if desc else base


async def judge_domains(
    llm: BaseChatModel,
    keywords: List[str],
    domains: List[dict],
    query: str = "",
    threshold: float = 0.7,
) -> List[dict]:
    """Score candidate domains against the user's query using the LLM.
    """
    if not domains:
        return []

    logger.info(
        "judge_domains: %d candidate domain(s), threshold=%.2f", len(domains), threshold
    )

    if threshold == 0:
        logger.info("judge_domains: threshold=0 — judge disabled, keeping all domains")
        return [
            {
                "uid": d.get("uid"),
                "name": d.get("name"),
                "hierarchy": d.get("hierarchy", []),
                "hierarchy_label": d.get("hierarchy_label"),
                "type": d.get("type"),
                "nb_docs": d.get("nb_docs"),
                "taxi_similarity": d.get("taxi_similarity"),
                "score": 1.0,
            }
            for d in domains
        ]

    effective_query = query.strip() or ", ".join(keywords) or "research domains"

    # ── LLM scoring (primary path) ────────────────────────────────────────────
    scores: List[float] = []
    scoring_method = "LLM"
    domain_list = "\n".join(_fmt_domain_for_llm(d) for d in domains)
    try:
        messages = [
            SystemMessage(content=_JUDGE_SYSTEM),
            HumanMessage(
                content=_JUDGE_USER.format(
                    query=effective_query,
                    domain_list=domain_list,
                )
            ),
        ]
        response = await llm.ainvoke(messages)
        raw = response.content if hasattr(response, "content") else str(response)
        match = re.search(r"\[[\d\s.,]+\]", raw)
        if match:
            llm_scores = json.loads(match.group())
            if len(llm_scores) == len(domains):
                scores = [float(s) for s in llm_scores]
    except Exception as exc:
        logger.warning(
            "judge_domains LLM call failed: %s — falling back to heuristic", exc
        )

    # ── Heuristic fallback (no LLM or LLM parse failed) ──────────────────────
    if not scores:
        scoring_method = "heuristic"
        scores = [_judge_score_heuristic(keywords, d) for d in domains]

    # ── Filter by threshold; always keep at least the top scorer ─────────────
    paired = list(zip(domains, scores))
    qualified_pairs = [(d, s) for d, s in paired if s >= threshold]
    if not qualified_pairs:
        qualified_pairs = [max(paired, key=lambda x: x[1])]
    qualified_pairs.sort(key=lambda x: x[1], reverse=True)

    qualified = [
        {
            "uid": d.get("uid"),
            "name": d.get("name"),
            "hierarchy": d.get("hierarchy", []),
            "hierarchy_label": d.get("hierarchy_label"),
            "type": d.get("type"),
            "nb_docs": d.get("nb_docs"),
            "taxi_similarity": d.get("taxi_similarity"),
            "score": round(s, 3),
        }
        for d, s in qualified_pairs
    ]

    logger.info(
        "judge_domains: scoring=%s — %d/%d domain(s) qualified (threshold=%.2f) -> %s",
        scoring_method,
        len(qualified),
        len(domains),
        threshold,
        [d["hierarchy_label"] for d in qualified],
    )
    return qualified


# ── Authors ────────────────────────────────────────────────────────────────────


_DEFAULT_MIN_DOCS = 5


async def get_domain_authors(
    toolbox: McpToolboxClient,
    domains: List[dict],
    min_docs: int = _DEFAULT_MIN_DOCS,
) -> List[dict]:
    """Internal Sorbonne researchers for the given (Topic) domains.
    """
    search_uids = await _resolve_search_scope(toolbox, domains, min_docs)
    authors_data = await toolbox.call(
        "sorbobot-list-domain-experts", uids=",".join(search_uids)
    )
    logger.info(
        "get_domain_authors: searching uids=%s -> %d author(s)",
        search_uids, len({a["person_uid"] for a in authors_data}),
    )

    result = []
    for a in authors_data:
        articles_raw = a.get("articles", [])
        # Group by domain uid first, then sample round-robin across domains.
        seen: set = set()
        by_uid: dict = {}
        for art in articles_raw:
            if not isinstance(art, dict):
                continue
            uid = art.get("uid", "")
            if uid in seen:
                continue
            seen.add(uid)
            by_uid.setdefault(art.get("domain_uid", ""), []).append(art)

        sample_articles = []
        buckets = list(by_uid.values())
        while len(sample_articles) < 10 and any(buckets):
            for bucket in buckets:
                if not bucket:
                    continue
                art = bucket.pop(0)
                sample_articles.append(
                    {
                        "uid": art.get("uid", ""),
                        "title": art.get("title", ""),
                        "domain_uid": art.get("domain_uid", ""),
                        "domain_name": art.get("domain_name", ""),
                        "type": art.get("type", ""),
                    }
                )
                if len(sample_articles) >= 10:
                    break

        result.append(
            {
                "uid": a.get("person_uid", ""),
                "display_name": a.get("author", ""),
                "nb_publications": a.get("nb_publications", 0),
                "sample_domains": [
                    d.get("domain_name", "") for d in a.get("domains", [])[:3]
                ],
                "sample_articles": sample_articles,
            }
        )

    logger.info(
        "get_domain_authors: %d domain(s) -> %d author(s)",
        len(domains),
        len(result),
    )
    return result


# ── Search scope resolution — broaden to SubField when too few docs ─────────


async def _resolve_search_scope(
    toolbox: McpToolboxClient,
    domains: List[dict],
    min_docs: int = _DEFAULT_MIN_DOCS,
) -> List[str]:
    """Decide which uid(s) to pass to `sorbobot-list-domain-experts`.

    If the matched Topics collectively cover at least `min_docs` documents,
    search them directly. Otherwise broaden once to their most common parent
    SubField (ties broken by the highest `taxi_similarity` among that
    SubField's matched Topics).
    """
    total_docs = sum(d.get("nb_docs") or 0 for d in domains)
    if total_docs >= min_docs:
        return [d["uid"] for d in domains if d.get("uid")]

    subfield_stats: Dict[str, Dict[str, float]] = {}
    for d in domains:
        hierarchy = d.get("hierarchy") or []
        if len(hierarchy) < 2:
            continue  # no SubField ancestor available
        subfield_uid = hierarchy[-2].get("uid")
        if not subfield_uid:
            continue
        stats = subfield_stats.setdefault(subfield_uid, {"count": 0, "best_similarity": -1.0})
        stats["count"] += 1
        stats["best_similarity"] = max(
            stats["best_similarity"], d.get("taxi_similarity") or 0.0
        )

    if not subfield_stats:
        logger.info(
            "_resolve_search_scope: only %d doc(s) but no SubField ancestor available — searching as-is",
            total_docs,
        )
        return [d["uid"] for d in domains if d.get("uid")]

    best_subfield_uid = max(
        subfield_stats,
        key=lambda uid: (subfield_stats[uid]["count"], subfield_stats[uid]["best_similarity"]),
    )
    logger.info(
        "_resolve_search_scope: only %d doc(s) across %d Topic(s) — broadening to SubField %s",
        total_docs, len(domains), best_subfield_uid,
    )
    return [best_subfield_uid]


# ── Person expertise ────────────────────────────────────────────────────────────

_PERSON_FUZZY_MATCH_THRESHOLD = 0.6


async def get_person_expertise(toolbox: McpToolboxClient, name: str) -> List[dict]:
    """Top research domains for a named internal Sorbonne researcher.
    """
    candidates = await toolbox.call(
        "sorbobot-search-person-by-name-fuzzy", name=name, max_results=50
    )
    logger.info(
        "get_person_expertise: %d candidate(s) for name=%r", len(candidates), name
    )
    if not candidates:
        return []

    name_lower = name.lower()
    best = max(
        candidates,
        key=lambda c: SequenceMatcher(
            None, name_lower, c["display_name"].lower()
        ).ratio(),
    )
    best_score = SequenceMatcher(None, name_lower, best["display_name"].lower()).ratio()
    logger.info(
        "get_person_expertise: best match=%r score=%.2f (threshold=%.2f)",
        best["display_name"], best_score, _PERSON_FUZZY_MATCH_THRESHOLD,
    )
    if best_score < _PERSON_FUZZY_MATCH_THRESHOLD:
        logger.info("get_person_expertise: best match below threshold — returning empty")
        return []

    domains = await toolbox.call("sorbobot-list-person-research-domains", person_uid=best["uid"])
    hierarchies = await fetch_hierarchies(
        toolbox, [d["domain_uid"] for d in domains if d.get("domain_uid")]
    )
    for d in domains:
        hierarchy = hierarchies.get(d.get("domain_uid"), [])
        d["hierarchy"] = hierarchy
        d["hierarchy_label"] = format_hierarchy_label(hierarchy)

    return domains
