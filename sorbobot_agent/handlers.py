"""Async intent handlers — domain_experts, person_expertise, general_question.

Each handler returns the final answer text (str) directly: OpenWebUI is a
purely conversational frontend, there is no structured JSON response to build.
"""

import asyncio
import logging
from typing import List, Optional

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from sorbobot_agent import domain_tools
from sorbobot_agent.config import AppConfig
from sorbobot_agent.mcp_toolbox import McpToolboxClient

logger = logging.getLogger("sorbobot_agent.handlers")


def _format_domain_experts_text(
    keywords: list,
    qualified: list,
    authors_data: list,
    language: str,
    max_authors: int = 10,
) -> str:
    kw_str = ", ".join(keywords) if keywords else "?"
    max_pubs = max((a.get("nb_publications", 0) for a in authors_data), default=1) or 1
    fr = language == "fr"

    # Build uid → "#N" mapping from the displayed domain list
    uid_to_id: dict = {
        d.get("uid", ""): f"#{i}"
        for i, d in enumerate(qualified, 1)
        if d.get("uid")
    }

    def _author_domain_ids(author: dict) -> str:
        """Return space-separated domain IDs for an author based on article domain_uid."""
        ids: set = set()
        for art in author.get("sample_articles", []):
            if not isinstance(art, dict):
                continue
            art_uid = art.get("domain_uid", "")
            if art_uid in uid_to_id:
                ids.add(uid_to_id[art_uid])
        return " ".join(sorted(ids, key=lambda x: int(x.replace("#", ""))))

    lines: list = []

    if fr:
        lines.append(
            f"Nous avons trouvé des experts de Sorbonne Université associés aux domaines: {kw_str}."
        )
        if qualified:
            lines.append(
                "Voici la liste des domaines les plus importants (nombre d'articles dans ce domaine) :"
            )
            for i, d in enumerate(qualified, 1):
                label = d.get("hierarchy_label") or d.get("name", "")
                nb = d.get("nb_articles", d.get("nb_docs", 0))
                lines.append(f"#{i} {label} ({nb} art.)")
        if authors_data:
            lines.append("\nVoici les chercheurs les plus pertinents :\n")
            for a in authors_data:
                score = int(a.get("nb_publications", 0) / max_pubs * 100)
                domain_ids = _author_domain_ids(a)
                line = f"    {a.get('display_name', '')} (score {score})"
                if domain_ids:
                    line += f"  {domain_ids}"
                lines.append(line)
    else:
        lines.append(f"We found Sorbonne University experts in: {kw_str}.")
        if qualified:
            lines.append("Most relevant domains (article count per domain):")
            for i, d in enumerate(qualified, 1):
                label = d.get("hierarchy_label") or d.get("name", "")
                nb = d.get("nb_articles", d.get("nb_docs", 0))
                lines.append(f"#{i} {label} ({nb} art.)")
        if authors_data:
            lines.append("\nMost relevant researchers:\n")
            for a in authors_data:
                score = int(a.get("nb_publications", 0) / max_pubs * 100)
                domain_ids = _author_domain_ids(a)
                line = f"    {a.get('display_name', '')} (score {score})"
                if domain_ids:
                    line += f"  {domain_ids}"
                lines.append(line)

    if not lines:
        return (
            "Aucun expert trouvé pour cette requête."
            if fr
            else "No experts found for this query."
        )
    return "\n".join(lines)


async def _search_domains_safe(
    toolbox: McpToolboxClient, kw: str, config: AppConfig
) -> Optional[list]:
    """Returns `None` (not `[]`) when crisalid-taxi itself is unreachable/timed
    out, so callers can tell "search service down" apart from "no match" —
    the two have very different user-facing messages and root causes.
    """
    try:
        return await domain_tools.search_domains(
            toolbox,
            kw,
            threshold=config.validation.semantic_threshold,
            limit=config.validation.top_k_syntactic,
            taxi_url=config.crisalid_taxi.base_url,
        )
    except httpx.HTTPError:
        logger.error("Domain search unavailable for '%s' (crisalid-taxi unreachable or timed out)", kw, exc_info=True)
        return None
    except Exception:
        logger.error("Domain search failed for '%s'", kw, exc_info=True)
        return []


async def handle_domain_experts(
    toolbox: McpToolboxClient,
    llm: BaseChatModel,
    config: AppConfig,
    keywords: List[str],
    query: str,
    language: str,
) -> str:
    kw_str = ", ".join(keywords) if keywords else query
    kw_list = keywords if keywords else [query]
    logger.info("handle_domain_experts: keywords=%s query=%r", kw_list, query)

    # Independent per-keyword lookups — run them concurrently rather than
    # one HTTP+MCP round-trip at a time.
    results = await asyncio.gather(
        *(_search_domains_safe(toolbox, kw, config) for kw in kw_list)
    )

    search_unavailable = any(r is None for r in results)
    all_domains: list = []
    seen_uids: set = set()
    for domains in results:
        if domains is None:
            continue
        for d in domains:
            if d.get("uid") not in seen_uids:
                seen_uids.add(d["uid"])
                all_domains.append(d)

    if not all_domains:
        if search_unavailable:
            logger.warning(
                "handle_domain_experts: crisalid-taxi unavailable for keywords=%s — returning service-unavailable message",
                kw_list,
            )
            return (
                "Le service de recherche sémantique est temporairement indisponible. Réessayez dans quelques instants."
                if language == "fr"
                else "The semantic search service is temporarily unavailable. Please try again shortly."
            )
        logger.info(
            "handle_domain_experts: no domain found for keywords=%s — returning empty-result message",
            kw_list,
        )
        return (
            f"Aucun domaine trouvé pour : {kw_str}."
            if language == "fr"
            else f"No domains found for: {kw_str}."
        )

    # Judge: LLM scores domains against the user's query. Skipped when there's
    # a single candidate — the threshold logic always keeps the top scorer
    # anyway, so judging one domain can't change the outcome.
    if len(all_domains) <= 1:
        logger.info("handle_domain_experts: judge skipped (single candidate domain)")
        qualified = [
            {
                "uid": d.get("uid"),
                "name": d.get("name"),
                "hierarchy": d.get("hierarchy", []),
                "hierarchy_label": d.get("hierarchy_label"),
                "type": d.get("type"),
                "score": 1.0,
            }
            for d in all_domains
        ]
    else:
        try:
            qualified = await domain_tools.judge_domains(
                llm,
                keywords,
                all_domains,
                query,
                threshold=config.validation.judge_threshold,
            )
        except Exception:
            logger.error("Judge failed — keeping all domains", exc_info=True)
            qualified = all_domains

    if not qualified:
        qualified = all_domains[:5]

    # Authors — broadens to the parent SubField if the matched Topics cover
    # too few documents (see domain_tools._resolve_search_scope).
    try:
        authors_data = await domain_tools.get_domain_authors(
            toolbox,
            qualified,
            min_docs=config.validation.domain_min_docs,
        )
    except Exception:
        logger.error("Author search failed", exc_info=True)
        authors_data = []

    show_max_authors = config.display.show_max_authors
    show_max_domains = config.display.show_max_domains

    # Sort authors by publications and apply display limit
    authors_data = sorted(
        authors_data, key=lambda a: a.get("nb_publications", 0), reverse=True
    )[:show_max_authors]

    # Rebuild domain results from the actual domains used by authors'
    # articles (adaptive search may have switched to parent/child domains —
    # collect the real uids from articles rather than trusting `qualified`).
    actual_uids: dict = {}  # uid → {name, type, count}
    for a in authors_data:
        for art in a.get("sample_articles", []):
            uid = art.get("domain_uid", "")
            if not uid:
                continue
            if uid not in actual_uids:
                actual_uids[uid] = {
                    "name": art.get("domain_name") or uid,
                    "type": art.get("type", ""),
                    "count": 0,
                }
            actual_uids[uid]["count"] += 1

    if actual_uids:
        # Article domain_uids can differ from `qualified`'s (adaptive search
        # may have broadened/narrowed) — fetch their hierarchy fresh rather
        # than assuming `qualified` already has it.
        hierarchies = await domain_tools.fetch_hierarchies(
            toolbox, list(actual_uids.keys())
        )
        qualified_by_uid = {d.get("uid"): d for d in qualified}
        domain_results = sorted(
            [
                {
                    "uid": k,
                    "name": v["name"],
                    "type": v["type"],
                    "nb_articles": v["count"],
                    "hierarchy_label": domain_tools.format_hierarchy_label(
                        hierarchies.get(k, [])
                    )
                    or v["name"],
                    "score": float(qualified_by_uid.get(k, {}).get("score", 1.0)),
                }
                for k, v in actual_uids.items()
            ],
            key=lambda d: d["nb_articles"],
            reverse=True,
        )[:show_max_domains]
    else:
        domain_results = [
            {
                "uid": d.get("uid", ""),
                "name": d.get("name", ""),
                "type": d.get("type"),
                "nb_articles": d.get("nb_docs", 0),
                "hierarchy_label": d.get("hierarchy_label"),
                "score": float(d.get("score", 1.0)),
            }
            for d in qualified[:show_max_domains]
        ]

    logger.info(
        "handle_domain_experts: returning %d domain(s), %d author(s) for keywords=%s",
        len(domain_results), len(authors_data), kw_list,
    )
    return _format_domain_experts_text(
        keywords, domain_results, authors_data, language, show_max_authors
    )


async def handle_person_expertise(
    toolbox: McpToolboxClient,
    person_name: str,
    query: str,
    language: str,
) -> str:
    logger.info("handle_person_expertise: person_name=%r query=%r", person_name, query)
    try:
        rows = await domain_tools.get_person_expertise(toolbox, person_name)
    except Exception:
        logger.error("Person expertise lookup failed", exc_info=True)
        rows = []

    if not rows:
        logger.info(
            "handle_person_expertise: no researcher found for person_name=%r",
            person_name,
        )
        return (
            f"Aucun chercheur Sorbonne trouvé pour « {person_name} »."
            if language == "fr"
            else f"No Sorbonne researcher found for '{person_name}'."
        )

    # No judge step here: classify_intent always returns keywords=[] for
    # person_expertise (the query names a person, not a topic), so there is
    # never a real signal to score domains against. judge_domains' "always
    # keep at least the top scorer" fallback would silently collapse this
    # already-correct, publication-count-ranked list down to a single domain
    # whenever the LLM scores every candidate below threshold.
    resolved_name = rows[0].get("display_name", person_name) if rows else person_name
    fr = language == "fr"

    if fr:
        lines = [f"Les domaines d'expertises de {resolved_name} sont :"]
    else:
        lines = [f"{resolved_name}'s areas of expertise are:"]

    for r in rows:
        label = r.get("hierarchy_label") or r.get("domain_name", "")
        nb = r.get("nb_publications", 0)
        s = "s" if nb > 1 else ""
        lines.append(f"* {label} ({nb} article{s})")

    return "\n".join(lines)


async def handle_general_question(llm: BaseChatModel, query: str, language: str) -> str:
    lang_note = (
        "Réponds en français de manière concise."
        if language == "fr"
        else "Answer concisely."
    )
    try:
        messages = [
            SystemMessage(
                content=f"Tu es SorboBot, un assistant de la Sorbonne. {lang_note}"
            ),
            HumanMessage(content=query),
        ]
        resp = await llm.ainvoke(messages)
        return resp.content if hasattr(resp, "content") else str(resp)
    except Exception as exc:
        logger.error("General question LLM failed: %s", exc)
        return f"Erreur : {exc}" if language == "fr" else f"Error: {exc}"
