"""Settings of Sorbobot, read from the environment on the first message (§12 of the spec)."""

import os
from collections.abc import Mapping
from dataclasses import dataclass

RANKING_METHODS = ("weighted_relevance", "count")

# Fixed values: they do not depend on the deployment.
RECENT_PUBLICATIONS = 2
PROFILE_CANDIDATE_PUBLICATIONS = 60
TEXT_CHARS = 300


class ConfigurationError(ValueError):
    """A deployment error: a required setting is missing or a value has the wrong type or range."""


@dataclass(frozen=True)
class SorbobotSettings:
    taxi_url: str
    toolbox_url: str | None = None
    toolset: str = "sorbobot"
    taxi_timeout_seconds: float = 15.0
    toolbox_timeout_seconds: float = 30.0
    judge_timeout_seconds: float = 30.0
    taxi_max_topics: int = 100
    taxi_min_query_chars: int = 25
    semantic_threshold: float = 0.56
    max_search_topics: int = 20
    topic_link_threshold: float = 0.57
    topic_sources: str = "crisalid"
    judge_threshold: float = 0.6
    min_authors: int = 2
    max_judged_siblings: int = 100
    ranking_method: str = "weighted_relevance"
    show_max_authors: int = 10
    show_max_domains: int = 5
    show_max_person_publications: int = 20
    profile_max_publications: int = 20
    explore_max_domains: int = 10
    max_message_chars: int = 1000
    supported_languages: tuple[str, ...] = ("en", "fr")
    default_language: str = "en"

    def __post_init__(self):
        if not self.taxi_url:
            raise ConfigurationError("CRISALID_TAXI_URL is required")
        positive = (
            "taxi_timeout_seconds", "toolbox_timeout_seconds", "judge_timeout_seconds", "taxi_max_topics",
            "taxi_min_query_chars", "max_search_topics", "min_authors", "max_judged_siblings", "show_max_authors",
            "show_max_domains", "show_max_person_publications", "profile_max_publications", "explore_max_domains",
            "max_message_chars",
        )
        for name in positive:
            if getattr(self, name) <= 0:
                raise ConfigurationError(f"SORBOBOT_{name.upper()} must be > 0, got {getattr(self, name)!r}")
        for name in ("semantic_threshold", "topic_link_threshold", "judge_threshold"):
            if not 0 <= getattr(self, name) <= 1:
                raise ConfigurationError(f"SORBOBOT_{name.upper()} must be between 0 and 1, got {getattr(self, name)!r}")
        if self.ranking_method not in RANKING_METHODS:
            raise ConfigurationError(f"SORBOBOT_RANKING_METHOD must be one of {RANKING_METHODS}, got {self.ranking_method!r}")
        if not self.supported_languages:
            raise ConfigurationError("SORBOBOT_SUPPORTED_LANGUAGES must list at least one language code")
        if self.default_language not in self.supported_languages:
            raise ConfigurationError(
                f"SORBOBOT_DEFAULT_LANGUAGE {self.default_language!r} is not in SORBOBOT_SUPPORTED_LANGUAGES "
                f"{list(self.supported_languages)}"
            )

    @property
    def judges_enabled(self) -> bool:
        return self.judge_threshold > 0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "SorbobotSettings":
        env = os.environ if env is None else env
        return cls(
            taxi_url=env.get("CRISALID_TAXI_URL", "").strip(),
            toolbox_url=env.get("SORBOBOT_MCP_TOOLBOX_URL") or env.get("CRISALID_MCP_TOOLBOX_URL") or None,
            toolset=env.get("SORBOBOT_MCP_TOOLBOX_TOOLSET") or "sorbobot",
            taxi_timeout_seconds=_number(env, "SORBOBOT_TAXI_TIMEOUT_SECONDS", 15.0),
            toolbox_timeout_seconds=_number(env, "SORBOBOT_TOOLBOX_TIMEOUT_SECONDS", 30.0),
            judge_timeout_seconds=_number(env, "SORBOBOT_JUDGE_TIMEOUT_SECONDS", 30.0),
            taxi_max_topics=_integer(env, "SORBOBOT_TAXI_MAX_TOPICS", 100),
            taxi_min_query_chars=_integer(env, "SORBOBOT_TAXI_MIN_QUERY_CHARS", 25),
            semantic_threshold=_number(env, "SORBOBOT_SEMANTIC_THRESHOLD", 0.56),
            max_search_topics=_integer(env, "SORBOBOT_MAX_SEARCH_TOPICS", 20),
            topic_link_threshold=_number(env, "SORBOBOT_TOPIC_LINK_THRESHOLD", 0.57),
            topic_sources=_sources(env.get("SORBOBOT_TOPIC_SOURCES")),
            judge_threshold=_number(env, "SORBOBOT_JUDGE_THRESHOLD", 0.6),
            min_authors=_integer(env, "SORBOBOT_MIN_AUTHORS", 2),
            max_judged_siblings=_integer(env, "SORBOBOT_MAX_JUDGED_SIBLINGS", 100),
            ranking_method=(env.get("SORBOBOT_RANKING_METHOD") or "weighted_relevance").strip(),
            show_max_authors=_integer(env, "SORBOBOT_SHOW_MAX_AUTHORS", 10),
            show_max_domains=_integer(env, "SORBOBOT_SHOW_MAX_DOMAINS", 5),
            show_max_person_publications=_integer(env, "SORBOBOT_SHOW_MAX_PERSON_PUBLICATIONS", 20),
            profile_max_publications=_integer(env, "SORBOBOT_PROFILE_MAX_PUBLICATIONS", 20),
            explore_max_domains=_integer(env, "SORBOBOT_EXPLORE_MAX_DOMAINS", 10),
            max_message_chars=_integer(env, "SORBOBOT_MAX_MESSAGE_CHARS", 1000),
            supported_languages=_languages(env.get("SORBOBOT_SUPPORTED_LANGUAGES") or "en,fr"),
            default_language=(env.get("SORBOBOT_DEFAULT_LANGUAGE") or "en").strip().lower(),
        )


def _number(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number, got {raw!r}") from exc


def _integer(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer, got {raw!r}") from exc



def _sources(value: str | None, default: str = "crisalid") -> str:
    """Comma-separated ``HAS_TOPIC`` sources to keep; unset means ``crisalid``, empty means every source."""
    if value is None:
        return default
    return ",".join(part.strip() for part in value.split(",") if part.strip())

def _languages(raw: str) -> tuple[str, ...]:
    return tuple(code.strip().lower() for code in raw.split(",") if code.strip())
