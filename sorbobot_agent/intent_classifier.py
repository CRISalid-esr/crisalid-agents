"""Language detection and intent classification for the SorboBot agent."""

import json
import logging
import re
from pathlib import Path
from typing import List

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

logger = logging.getLogger("sorbobot_agent.intent_classifier")

_PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
_CLASSIFY_SYSTEM = (_PROMPT_DIR / "classify_intent_system.md").read_text(
    encoding="utf-8"
)
_CLASSIFY_USER = "Query: {query}\nJSON:"
_CONTEXTUALIZE_SYSTEM = (_PROMPT_DIR / "contextualize_query_system.md").read_text(
    encoding="utf-8"
)
_CONTEXTUALIZE_USER = (
    "Conversation history:\n{history}\n\nLatest message: {query}\nStandalone query:"
)

_HISTORY_MAX_MESSAGES = 6
_HISTORY_MAX_CHARS = 500


def last_human_message(messages: List[BaseMessage]) -> str:
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            return msg.content if isinstance(msg.content, str) else str(msg.content)
    return ""


def _format_history(messages: List[BaseMessage]) -> str:
    """Render the conversation up to (but excluding) the latest message as text."""
    lines: list = []
    for msg in messages[:-1][-_HISTORY_MAX_MESSAGES:]:
        if isinstance(msg, HumanMessage):
            role = "User"
        elif isinstance(msg, AIMessage):
            role = "Assistant"
        else:
            continue
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        if len(content) > _HISTORY_MAX_CHARS:
            content = content[:_HISTORY_MAX_CHARS] + "…"
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


async def contextualize_query(llm: BaseChatModel, messages: List[BaseMessage]) -> str:
    """Rewrite the latest user message as a standalone query using recent history.

    Returns the message unchanged (no LLM call) if there is no prior history.
    """
    query = last_human_message(messages)
    history = _format_history(messages)
    if not history:
        return query

    try:
        resp = await llm.ainvoke(
            [
                SystemMessage(content=_CONTEXTUALIZE_SYSTEM),
                HumanMessage(
                    content=_CONTEXTUALIZE_USER.format(history=history, query=query)
                ),
            ]
        )
        rewritten = resp.content if hasattr(resp, "content") else str(resp)
        rewritten = (
            rewritten.strip() if isinstance(rewritten, str) else str(rewritten).strip()
        )
        if rewritten and rewritten != query:
            logger.info("contextualize_query: %r -> %r", query, rewritten)
        return rewritten or query
    except Exception as exc:
        logger.warning("Query contextualization failed (%s), using raw query.", exc)
        return query


# ── French word-set for language detection (no LLM) ──────────────────────────

_FR_WORDS = {
    "les",
    "des",
    "une",
    "pour",
    "dans",
    "sur",
    "avec",
    "par",
    "qui",
    "que",
    "est",
    "sont",
    "ont",
    "peut",
    "faire",
    "plus",
    "aussi",
    "mais",
    "comme",
    "quel",
    "quelle",
    "quels",
    "quelles",
    "trouver",
    "cherche",
    "chercher",
    "travaux",
    "domaine",
    "recherche",
    "auteurs",
    "experts",
    "publications",
    "articles",
    "thèse",
    "laboratoire",
    "spécialistes",
    "connaît",
    "travaille",
    "combien",
    "publié",
    "publiés",
    "affiliés",
    "récentes",
    "récents",
    "collabore",
    "domaines",
    "labos",
    "institutions",
    "chercheurs",
    "auteur",
    "thèses",
    "santé",
    "donne",
    "moi",
    "titre",
    "montre",
    "liste",
    "premiers",
    "derniers",
    "expertises",
    "expertise",
}


def detect_language(query: str) -> str:
    words = set(re.findall(r"\b\w+\b", query.lower()))
    return "fr" if len(words & _FR_WORDS) >= 2 else "en"


# Regex heuristics to catch common mis-classifications
_PERSON_TRIGGER = re.compile(
    r"\b(?:expertises?|contributions?|travaux|recherches?|publications?|domaines?|fait)\s+"
    r"(?:de|par|d[ue]s?|of)\s+([A-ZÀ-Ü][a-zà-ü\-]+(?:\s+[A-ZÀ-Ü][a-zà-ü\-]+)+)",
    re.IGNORECASE,
)
_NOISE_WORDS = {
    "quels",
    "queles",
    "quelles",
    "sont",
    "contributions",
    "travaux",
    "recherche",
    "recherches",
    "domaines",
    "expertise",
    "expertises",
    "publications",
    "les",
    "des",
    "qui",
    "que",
    "what",
    "which",
}


async def classify_intent(llm: BaseChatModel, query: str) -> dict:
    """Single LLM call to extract intent + params. Falls back to heuristics."""
    try:
        messages = [
            SystemMessage(content=_CLASSIFY_SYSTEM),
            HumanMessage(content=_CLASSIFY_USER.format(query=query)),
        ]
        resp = await llm.ainvoke(messages)
        raw = resp.content if hasattr(resp, "content") else str(resp)
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(m.group()) if m else {}
    except Exception as exc:
        logger.warning("Intent classification failed (%s), using fallback.", exc)
        data = {}

    intent = data.get("intent", "domain_experts")
    keywords = data.get("keywords") or []
    person_name = data.get("person_name") or None

    # Heuristic 1: name present but intent wrong
    if person_name and intent not in ("person_expertise", "database_query"):
        intent = "person_expertise"
        keywords = []

    # Heuristic 2: "travaux/expertises de [Nom]"
    if intent == "domain_experts" and not person_name:
        m2 = _PERSON_TRIGGER.search(query)
        if m2:
            person_name = m2.group(1).strip()
            intent = "person_expertise"
            keywords = []

    # Heuristic 3: all keywords are noise words + capitalised name in query
    if intent == "domain_experts" and keywords and not person_name:
        if all(k.lower() in _NOISE_WORDS for k in keywords):
            m3 = re.search(r"\b([A-ZÀ-Ü][a-zà-ü\-]+\s+[A-ZÀ-Ü][a-zà-ü\-]+)\b", query)
            if m3:
                person_name = m3.group(1).strip()
                intent = "person_expertise"
                keywords = []

    logger.info(
        "classify_intent: query=%r -> intent=%s keywords=%s person=%s",
        query, intent, keywords, person_name,
    )
    return {"intent": intent, "keywords": keywords, "person_name": person_name}
