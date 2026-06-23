"""Async domain-search, judging and author-lookup helpers for the SorboBot agent.

These are plain async functions called directly by handlers.py — not
LangChain `@tool`s — since they are never exposed to LLM tool-calling.
"""

import json
import logging
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import List, Optional, Tuple

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from sorbobot_agent.mcp_toolbox import McpToolboxClient

logger = logging.getLogger("sorbobot_agent.domain_tools")

_PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
_JUDGE_SYSTEM = (_PROMPT_DIR / "judge_domains_system.md").read_text(encoding="utf-8")
_JUDGE_USER = "User query: {query}\n\nDomains to score:\n{domain_list}\n\nScores:"

# crisalid-taxi /match `rel_type` -> equivalent Neo4j Domain.depth.
_REL_TYPE_DEPTH = {"HAS_DOMAIN": 2, "HAS_FIELD": 3, "HAS_SUBFIELD": 4, "HAS_TOPIC": 5}


# ── Domain search ──────────────────────────────────────────────────────────────


def format_domain_path(full_path: str) -> str:
    """Render a Neo4j Domain.full_path as a short "A > B > C" label.

    Strips the leading "root" segment and keeps only the last 3 levels.
    Returns the input unchanged if it has no usable segments.
    """
    parts = [p for p in full_path.split("/-") if p and p.lower() != "root"]
    return " > ".join(parts[-3:]) if parts else full_path


# Domain.type values (domain/field/subfield/topic — set by
# scripts/rebuild_domain_graph.py) -> display label. Every OpenAlex level
# shares the same :Domain label and HAS_CONCEPT relationship, so this is the
# only place that tells a user-facing result apart from another.
_TYPE_LABELS = {
    "domain": "Domain",
    "field": "Field",
    "subfield": "Subfield",
    "topic": "Topic",
}


def format_type_label(type_value: Optional[str]) -> str:
    """Render a Domain.type value as a display label (e.g. "topic" -> "Topic")."""
    return _TYPE_LABELS.get((type_value or "").lower(), "")


async def search_domains(
    toolbox: McpToolboxClient,
    keyword: str,
    min_depth: int = 2,
    threshold: float = 0.63,
    limit: int = 20,
    taxi_url: Optional[str] = None,
) -> List[dict]:
    """Find candidate Domain nodes for `keyword`.

    Calls crisalid-taxi's POST /api/v1/match to semantically match `keyword`
    against the OpenAlex taxonomy
    """
    logger.info(
        "crisalid-taxi request: POST %s/api/v1/match/ keyword=%r threshold=%.2f",
        taxi_url, keyword, threshold,
    )
    async with httpx.AsyncClient(base_url=taxi_url, timeout=15.0) as client:

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

    candidate_uids = {
        m["concept_uid"]
        for m in matches
        if _REL_TYPE_DEPTH.get(m["rel_type"], 0) >= min_depth
    }
    logger.info(
        "search_domains: %d/%d match(es) kept after min_depth=%d filter (keyword=%r)",
        len(candidate_uids), len(matches), min_depth, keyword,
    )

    if not candidate_uids:
        logger.info(
            "search_domains: no candidate uid — returning no domains (keyword=%r)",
            keyword,
        )
        return []

    domains = await toolbox.call(
        "get-domains-by-uid",
        uids=",".join(candidate_uids),
        similarity_threshold=threshold,
    )
    logger.info(
        "search_domains: get-domains-by-uid -> %d domain(s) (keyword=%r)",
        len(domains), keyword,
    )

    return domains[:limit]


# ── Judge ──────────────────────────────────────────────────────────────────────


def _judge_score_heuristic(keywords: List[str], domain: dict) -> float:
    """
    Combines keyword overlap on name/path + depth fitness.
    Returns a float in [0, 1].
    """
    name = domain.get("name", "").lower()
    path = domain.get("full_path", "").lower()
    depth = int(domain.get("depth", 3))
    kw_lower = [kw.lower() for kw in keywords]

    kw_hits = 0.0
    for kw in kw_lower:
        kw_words = [w for w in kw.split() if len(w) > 2]
        if kw in name or kw in path:
            kw_hits += 1.0
        elif kw_words and all(w in name or w in path for w in kw_words):
            kw_hits += 0.7
        elif kw_words and any(w in name or w in path for w in kw_words):
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
    full_path = d.get("full_path", "")
    path_str = format_domain_path(full_path) if full_path else d.get("name", "?")
    type_label = format_type_label(d.get("type"))
    desc = d.get("description") or ""
    base = f"- Name: {d.get('name', '?')} | Type: {type_label or '?'} | Path: {path_str}"
    return f"{base} | Description: {desc}" if desc else base


async def judge_domains(
    llm: BaseChatModel,
    keywords: List[str],
    domains: List[dict],
    query: str = "",
    threshold: float = 0.7,
) -> List[dict]:
    """Score candidate domains against the user's query using the LLM.

    Returns a list of {name, full_path, score}, keeping only domains with
    score >= threshold (always keeps at least the top scorer). threshold=0
    disables filtering entirely.
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
                "name": d.get("name"),
                "full_path": d.get("full_path"),
                "type": d.get("type"),
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
            "name": d.get("name"),
            "full_path": d.get("full_path"),
            "type": d.get("type"),
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
        [d["full_path"] for d in qualified],
    )
    return qualified


# ── Authors ────────────────────────────────────────────────────────────────────


async def get_domain_authors(
    toolbox: McpToolboxClient,
    domain_paths: List[str],
    author_min: int = 10,
    author_max: int = 20,
) -> List[dict]:
    """Internal Sorbonne researchers for the given domains, via adaptive tree search.
    """
    final_paths, authors_data = await _adaptive_tree_search(
        toolbox,
        initial_paths=domain_paths,
        target_authors_min=author_min,
        target_authors_max=author_max,
    )

    result = []
    for a in authors_data:
        articles_raw = a.get("articles", [])
        # Group by domain path first, then sample round-robin across paths.
        seen: set = set()
        by_path: dict = {}
        for art in articles_raw:
            if not isinstance(art, dict):
                continue
            uid = art.get("uid", "")
            if uid in seen:
                continue
            seen.add(uid)
            by_path.setdefault(art.get("path", ""), []).append(art)

        sample_articles = []
        buckets = list(by_path.values())
        while len(sample_articles) < 10 and any(buckets):
            for bucket in buckets:
                if not bucket:
                    continue
                art = bucket.pop(0)
                sample_articles.append(
                    {
                        "uid": art.get("uid", ""),
                        "title": art.get("title", ""),
                        "path": art.get("path", ""),
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
        "get_domain_authors: %d input path(s) -> %d final path(s) -> %d author(s)",
        len(domain_paths),
        len(final_paths),
        len(result),
    )
    return result


# ── Adaptive search — broaden/narrow domain scope ─────────────────────────────


async def _adaptive_tree_search(
    toolbox: McpToolboxClient,
    initial_paths: List[str],
    target_authors_min: int = 5,
    target_authors_max: int = 10,
    max_iterations: int = 3,
) -> Tuple[List[str], List[dict]]:
    """Navigate up/down the domain tree until the author count fits the target range."""
    current_paths = initial_paths.copy()
    iteration = 0

    while iteration < max_iterations:
        authors = await toolbox.call(
            "list-domain-experts", domain_paths=",".join(current_paths)
        )
        nb_authors = len({a["person_uid"] for a in authors})
        logger.info(
            "_adaptive_tree_search[%d]: %d author(s) for paths=%s (target=%d-%d)",
            iteration, nb_authors, current_paths, target_authors_min, target_authors_max,
        )

        if target_authors_min <= nb_authors <= target_authors_max:
            return current_paths, authors

        if nb_authors > target_authors_max:
            # Too many -> go down to children
            new_paths = []
            for path in current_paths:
                children = await toolbox.call(
                    "get-child-domains", full_paths=path, depth_delta=1
                )
                if children:
                    new_paths.extend([c["full_path"] for c in children])
                else:
                    new_paths.append(path)
            new_paths = list(set(new_paths))
            logger.info(
                "_adaptive_tree_search[%d]: too many — narrowing to %s",
                iteration, new_paths,
            )
        else:
            # Too few -> go up to parents
            new_paths = []
            for path in current_paths:
                parents = await toolbox.call(
                    "get-parent-domains", full_paths=path, depth_delta=1
                )
                if parents:
                    new_paths.extend([p["full_path"] for p in parents])
                else:
                    new_paths.append(path)
            new_paths = list(set(new_paths))
            logger.info(
                "_adaptive_tree_search[%d]: too few — broadening to %s",
                iteration, new_paths,
            )

        if set(new_paths) == set(current_paths):
            # No domain has children/parents to move to (e.g. every path is
            # already a leaf topic) — further iterations would be a no-op.
            logger.info(
                "_adaptive_tree_search[%d]: no further narrowing/broadening possible — stopping",
                iteration,
            )
            return current_paths, authors

        current_paths = new_paths
        iteration += 1

    authors = await toolbox.call(
        "list-domain-experts", domain_paths=",".join(current_paths)
    )
    logger.info(
        "_adaptive_tree_search: max_iterations reached — %d author(s) for paths=%s",
        len({a["person_uid"] for a in authors}), current_paths,
    )
    return current_paths, authors


# ── Person expertise ────────────────────────────────────────────────────────────

_PERSON_FUZZY_MATCH_THRESHOLD = 0.6


async def get_person_expertise(toolbox: McpToolboxClient, name: str) -> List[dict]:
    """Top research domains for a named internal Sorbonne researcher.
    """
    candidates = await toolbox.call(
        "search-person-by-name-fuzzy", name=name, max_results=50
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

    return await toolbox.call("list-person-research-domains", person_uid=best["uid"])
