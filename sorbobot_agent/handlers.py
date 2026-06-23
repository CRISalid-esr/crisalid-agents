import asyncio
import logging
from typing import List, Optional

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from sorbobot_agent import domain_tools
from sorbobot_agent.config import AppConfig
from sorbobot_agent.domain_tools import format_domain_path as _fmt_path
from sorbobot_agent.domain_tools import format_type_label
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

    # Build path → "#N" mapping from the displayed domain list
    path_to_id: dict = {
        d.get("full_path", ""): f"#{i}"
        for i, d in enumerate(qualified, 1)
        if d.get("full_path")
    }

    def _author_domain_ids(author: dict) -> str:
        """Return space-separated domain IDs for an author based on article paths."""
        ids: set = set()
        for art in author.get("sample_articles", []):
            if not isinstance(art, dict):
                continue
            art_path = art.get("path", "")
            if art_path in path_to_id:
                ids.add(path_to_id[art_path])
            else:
                # Partial match: article path is a child or parent of a displayed domain
                for dp, did in path_to_id.items():
                    if art_path.startswith(dp + "/-") or dp.startswith(art_path + "/-"):
                        ids.add(did)
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
                path = _fmt_path(d.get("full_path", "")) or d.get("name", "")
                nb = d.get("nb_articles", d.get("nb_docs", 0))
                type_label = format_type_label(d.get("type"))
                prefix = f"[{type_label}] " if type_label else ""
                lines.append(f"#{i} {prefix}{path} ({nb} art.)")
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
                path = _fmt_path(d.get("full_path", "")) or d.get("name", "")
                nb = d.get("nb_articles", d.get("nb_docs", 0))
                type_label = format_type_label(d.get("type"))
                prefix = f"[{type_label}] " if type_label else ""
                lines.append(f"#{i} {prefix}{path} ({nb} art.)")
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
    seen_paths: set = set()
    for domains in results:
        if domains is None:
            continue
        for d in domains:
            if d.get("full_path") not in seen_paths:
                seen_paths.add(d["full_path"])
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

    # Judge: LLM scores domains against the user's query.
    if len(all_domains) <= 1:
        logger.info("handle_domain_experts: judge skipped (single candidate domain)")
        qualified = [
            {
                "name": d.get("name"),
                "full_path": d.get("full_path"),
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

    # Authors via adaptive tree search
    paths = [d["full_path"] for d in qualified]
    try:
        authors_data = await domain_tools.get_domain_authors(
            toolbox,
            paths,
            author_min=config.validation.author_min,
            author_max=config.validation.author_max,
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

    # Rebuild domain results from the actual paths used by authors
    # (adaptive search may have switched to parent domains → collect real paths from articles)
    actual_paths: dict = {}  # path → {name, type, count}
    for a in authors_data:
        for art in a.get("sample_articles", []):
            path = art.get("path", "")
            if not path:
                continue
            if path not in actual_paths:
                parts = [p for p in path.split("/-") if p and p.lower() != "root"]
                name = parts[-1].replace("-", " ").title() if parts else path
                actual_paths[path] = {"name": name, "type": art.get("type", ""), "count": 0}
            actual_paths[path]["count"] += 1

    if actual_paths:
        domain_results = sorted(
            [
                {
                    "name": v["name"],
                    "full_path": k,
                    "type": v["type"],
                    "nb_articles": v["count"],
                    "score": next(
                        (
                            float(d.get("score", 1.0))
                            for d in qualified
                            if d.get("full_path") == k
                        ),
                        1.0,
                    ),
                }
                for k, v in actual_paths.items()
            ],
            key=lambda d: d["nb_articles"],
            reverse=True,
        )[:show_max_domains]
    else:
        domain_results = [
            {
                "name": d.get("name", ""),
                "full_path": d.get("full_path", ""),
                "type": d.get("type"),
                "nb_articles": d.get("nb_docs", 0),
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


    resolved_name = rows[0].get("display_name", person_name) if rows else person_name
    fr = language == "fr"

    if fr:
        lines = [f"Les domaines d'expertises de {resolved_name} sont :"]
    else:
        lines = [f"{resolved_name}'s areas of expertise are:"]

    for r in rows:
        path = _fmt_path(r.get("domain_path", "")) or r.get("domain_name", "")
        nb = r.get("nb_publications", 0)
        s = "s" if nb > 1 else ""
        type_label = format_type_label(r.get("domain_type"))
        prefix = f"[{type_label}] " if type_label else ""
        lines.append(f"* {prefix}{path} ({nb} article{s})")

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
