from dataclasses import asdict, dataclass, field
from datetime import date


@dataclass
class Passage:
    """A section-level chunk of a topic, embedded on its own (nested ``passages`` field)."""

    section: str  # "expected_outcome" | "scope"
    order: int
    text: str


@dataclass
class ParseIssue:
    level: str  # "warning" | "error"
    message: str
    source_file: str
    page: int | None = None
    topic_id: str | None = None


@dataclass
class Topic:
    topic_id: str
    title: str
    cluster: str
    cluster_label: str
    work_programme: str
    part: int
    source_file: str
    source_pages: tuple[int, int]
    call_id: str | None = None
    call_name: str | None = None
    destination: str | None = None
    type_of_action: str | None = None
    type_of_action_label: str | None = None
    stage: str = "single"
    contribution_min_eur: int | None = None
    contribution_max_eur: int | None = None
    indicative_budget_eur: int | None = None
    expected_projects: int | None = None
    opening_date: date | None = None
    deadlines: list[date] = field(default_factory=list)
    trl: str | None = None
    conditions_text: str = ""
    expected_outcome: str = ""
    scope: str = ""
    passages: list[Passage] = field(default_factory=list)

    @property
    def content(self) -> str:
        return "\n\n".join(part for part in (self.title, self.expected_outcome, self.scope) if part)

    def to_document(self) -> dict:
        # OpenSearch document without the provenance/embedding fields added by the ingester.
        doc = asdict(self)
        doc["source_pages"] = list(self.source_pages)
        doc["opening_date"] = self.opening_date.isoformat() if self.opening_date else None
        doc["deadlines"] = [d.isoformat() for d in self.deadlines]
        doc["content"] = self.content
        return doc


@dataclass
class ParseResult:
    source_file: str
    part: int | None
    cluster: str | None
    cluster_label: str | None
    work_programme: str | None
    topics: list[Topic]
    issues: list[ParseIssue]

    @property
    def errors(self) -> list[ParseIssue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self) -> list[ParseIssue]:
        return [i for i in self.issues if i.level == "warning"]


@dataclass
class TopicHit:
    topic_id: str
    score: float
    title: str
    call_id: str | None
    call_name: str | None
    cluster: str
    destination: str | None
    type_of_action: str | None
    deadlines: list[str]
    opening_date: str | None
    contribution_min_eur: int | None
    contribution_max_eur: int | None
    indicative_budget_eur: int | None
    best_passage: str | None = None
    best_passage_section: str | None = None
    query_ranks: dict[str, int] = field(default_factory=dict)
