"""Rows read from the services and small helpers shared by the searches."""

import re
from dataclasses import dataclass, field

from agents.sorbobot.config import TEXT_CHARS

# Relator codes of the graph -> role names of the answers (§5.2), in display order (§7.4).
ROLES = {"aut": "author", "ths": "thesis_supervisor", "spk": "speaker"}
ROLE_ORDER = ("author", "thesis_supervisor", "speaker", "other")


@dataclass(frozen=True)
class TopicMatch:
    """A topic crisalid-taxi matched to the question, with the question similarity."""

    uid: str
    similarity: float


@dataclass(frozen=True)
class Concept:
    uid: str
    name: str
    description: str
    nb_docs: int


@dataclass(frozen=True)
class EvidenceRow:
    person_uid: str
    display_name: str
    doc_uid: str
    doc_title: str
    publication_date: str | None
    topic_uid: str
    similarity: float


@dataclass(frozen=True)
class PersonHit:
    uid: str
    display_name: str
    external: bool
    score: float


@dataclass(frozen=True)
class PersonConcept:
    uid: str
    name: str
    nb_publications: int


@dataclass
class PersonDocument:
    uid: str
    title: str
    publication_date: str | None
    concept_uids: list[str] = field(default_factory=list)
    roles: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DocumentSummary:
    uid: str
    abstract: str | None
    keywords: tuple[str, ...]


@dataclass(frozen=True)
class Domain:
    """A research domain as displayed in an answer, numbered by its tag."""

    tag: str
    uid: str
    name: str
    path: str
    nb_publications: int
    definition: str

    def as_dict(self) -> dict:
        return {
            "tag": self.tag,
            "uid": self.uid,
            "name": self.name,
            "path": self.path,
            "nb_publications": self.nb_publications,
            "definition": self.definition,
        }


def as_number(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_int(value, default: int = 0) -> int:
    number = as_number(value)
    return default if number is None else int(number)


def year_of(date) -> int | None:
    """The year of a publication date written in any common form (``2020-01-15``, ``2020``, ``15/01/2020``)."""
    match = re.search(r"(1[5-9]\d\d|2\d\d\d)", str(date or ""))
    return int(match.group(1)) if match else None


def truncate(text, limit: int = TEXT_CHARS) -> str:
    """Cut a text on a word boundary, appending an ellipsis."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0] or text[:limit]
    return cut.rstrip(" ,;:") + "…"


def role_of(roles: list[str]) -> str:
    """The first role found in the order author, thesis supervisor, speaker; ``other`` otherwise."""
    codes = set(roles)
    for code, name in ROLES.items():
        if code in codes:
            return name
    return "other"


def tag(number: int) -> str:
    return f"#{number}"


def tag_key(value: str) -> int:
    """Tags sort as numbers: ``#2`` before ``#10``."""
    digits = value.lstrip("#")
    return int(digits) if digits.isdigit() else 0


def work_key(title, fallback: str) -> str:
    """Two documents with the same title are one work (two records of one book, for example)."""
    normalised = re.sub(r"[\W_]+", " ", str(title or "").lower()).strip()
    return normalised or fallback
