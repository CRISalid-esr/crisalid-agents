"""Sorbobot settings."""

import os
from dataclasses import dataclass, fields
from typing import ClassVar, cast

from agents.sorbobot.ranking import RANKING_METHODS

POSITIVE_SETTINGS = (
    "taxi_timeout_seconds",
    "toolbox_timeout_seconds",
    "judge_timeout_seconds",
    "taxi_max_topics",
    "taxi_min_query_chars",
    "max_search_topics",
    "show_max_authors",
    "show_max_domains",
    "show_max_person_publications",
    "profile_max_publications",
    "explore_max_domains",
)


class SettingsError(ValueError):
    """An environment variable is missing or holds an invalid value."""


@dataclass(frozen=True)
class SorbobotSettings:
    """Every field can be set with ``SORBOBOT_<FIELD>``, except ``taxi_url`` which reads ``CRISALID_TAXI_URL``."""

    taxi_url: str
    taxi_timeout_seconds: float = 15.0
    toolbox_timeout_seconds: float = 30.0
    judge_timeout_seconds: float = 30.0
    # Matches asked of taxi
    taxi_max_topics: int = 100
    # crisalid-taxi ignores a shorter query: below this, the tools ask the LLM for a fuller phrase.
    taxi_min_query_chars: int = 25
    # Minimum query-to-topic similarity applied by taxi.
    semantic_threshold: float = 0.56
    max_search_topics: int = 20
    # Minimum stored document-to-topic similarity (HAS_TOPIC) for a document to count.
    topic_link_threshold: float = 0.57
    # Relevance the LLM judge must give a topic; 0 disables both the concept and the publication judge.
    judge_threshold: float = 0.7
    min_authors: int = 10
    max_authors: int = 20
    ranking_method: str = "weighted_relevance"
    show_max_authors: int = 10
    show_max_domains: int = 5
    show_max_person_publications: int = 20
    profile_max_publications: int = 20
    explore_max_domains: int = 10

    # Fixed values
    JUDGE_RETRY_THRESHOLD: ClassVar[float] = 0.5
    MAX_WALK_STEPS: ClassVar[int] = 4
    RECENT_PUBLICATIONS_PER_EXPERT: ClassVar[int] = 3
    MAX_JUDGED_PUBLICATIONS: ClassVar[int] = 60
    TEXT_MAX_CHARS: ClassVar[int] = 300

    def __post_init__(self):
        if not self.taxi_url:
            raise SettingsError("CRISALID_TAXI_URL is required")
        if self.ranking_method not in RANKING_METHODS:
            raise SettingsError(f"SORBOBOT_RANKING_METHOD must be one of {RANKING_METHODS}")
        for name in ("semantic_threshold", "topic_link_threshold", "judge_threshold"):
            if not 0.0 <= getattr(self, name) <= 1.0:
                raise SettingsError(f"SORBOBOT_{name.upper()} must be between 0 and 1")
        if not 0 < self.min_authors <= self.max_authors:
            raise SettingsError("SORBOBOT_MIN_AUTHORS must be positive and at most SORBOBOT_MAX_AUTHORS")
        for name in POSITIVE_SETTINGS:
            if getattr(self, name) <= 0:
                raise SettingsError(f"SORBOBOT_{name.upper()} must be positive")

    @classmethod
    def from_env(cls) -> "SorbobotSettings":
        values: dict = {"taxi_url": os.getenv("CRISALID_TAXI_URL", "")}
        for field in fields(cls):
            variable = f"SORBOBOT_{field.name.upper()}"
            raw = os.getenv(variable)
            if field.name == "taxi_url" or raw is None:
                continue
            value_type = cast(type, field.type)
            try:
                values[field.name] = value_type(raw)
            except (TypeError, ValueError) as exc:
                raise SettingsError(f"{variable}={raw!r} is not a valid {value_type.__name__}") from exc
        return cls(**values)
