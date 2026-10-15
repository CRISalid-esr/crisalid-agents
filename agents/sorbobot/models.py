"""Data models for Sorbobot's answers."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

ROLES = {"aut": "author", "ths": "thesis_supervisor", "spk": "speaker"}
TAG_PREFIX = "#"


def tag(number: int) -> str:
    """The label of the n-th domain of an answer, referred to by the experts and publications it carries."""
    return f"{TAG_PREFIX}{number}"


def as_number(value: Any, fallback: float | None = None) -> float | None:
    """``None`` for a value the toolbox did not send as a number, unless a fallback is given."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


@dataclass(frozen=True)
class TopicMatch:
    uid: str
    similarity: float


@dataclass(frozen=True)
class Domain:
    """An OpenAlex concept shown in an answer and tagged "#1", "#2"…"""

    tag: str
    uid: str
    name: str
    path: str
    nb_publications: int
    definition: str = ""


@dataclass(frozen=True)
class Publication:
    title: str
    year: int | None
    tags: tuple[str, ...] = ()
    role: str = ""


@dataclass(frozen=True)
class Expert:
    person_uid: str
    name: str
    score: int
    tags: tuple[str, ...]
    nb_publications_on_topic: int
    nb_publications_total: int
    recent_publications: tuple[Publication, ...]


@dataclass(frozen=True)
class ExpertsResult:
    topic: str
    ranking_method: str
    domains: tuple[Domain, ...]
    experts: tuple[Expert, ...]


@dataclass(frozen=True)
class ExpertiseResult:
    person_uid: str
    name: str
    domains: tuple[Domain, ...]
    publications: tuple[Publication, ...]
    other_candidates: tuple[str, ...]


@dataclass(frozen=True)
class CitedPublication:
    reference: str
    role: str
    title: str
    year: int | None
    tags: tuple[str, ...]
    keywords: tuple[str, ...]
    abstract: str


@dataclass(frozen=True)
class ProfileResult:
    person_uid: str
    name: str
    question: str
    domains: tuple[Domain, ...]
    publications: tuple[CitedPublication, ...]


@dataclass(frozen=True)
class DomainsResult:
    topic: str
    domains: tuple[Domain, ...]


def shorten(text: str | None, limit: int) -> str:
    """Collapse whitespace and cut the text to ``limit`` characters on a word boundary."""
    flat = " ".join(str(text or "").split())
    if len(flat) <= limit:
        return flat
    return flat[:limit].rsplit(" ", 1)[0] + "…"


def sorted_tags(tags: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(tags), key=lambda value: int(value.removeprefix(TAG_PREFIX))))
