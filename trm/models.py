from dataclasses import asdict, dataclass, field


@dataclass
class Publication:
    uid: str
    title: str
    abstract: str | None
    year: int | None
    best_score: float  # best cosine similarity over the queries
    contributors: list[dict]  # {uid, name}
    query_ranks: dict[str, int] = field(default_factory=dict)
    rrf: float = 0.0
    score: float = 0.0  # rrf × best_score


@dataclass
class Candidate:
    person_uid: str
    name: str
    publications: list[Publication]  # sorted by score, best first
    pre_score: float


@dataclass
class PublicationVerdict:
    uid: str
    relevant: bool
    reason: str


@dataclass
class Verification:
    verdicts: list[PublicationVerdict]
    overall: str  # strong | plausible | weak | none
    justification: str
    error: str | None = None

    @property
    def relevant_uids(self) -> set[str]:
        return {v.uid for v in self.verdicts if v.relevant}


@dataclass
class ResearcherMatch:
    pair_id: str
    person_uid: str
    name: str
    units: list[str]
    score: float  # normalised to [0, 1] within the topic
    raw_score: float
    overall: str
    justification: str
    publications: list[dict]  # {uid, title, year, score, relevant, reason}


@dataclass
class Expansion:
    summary: str
    queries: list[str]
    raw: dict = field(default_factory=dict)


@dataclass
class TopicResult:
    topic_id: str
    title: str
    cluster: str
    call_id: str | None
    deadlines: list[str]
    destination: str | None
    summary: str = ""
    queries: list[str] = field(default_factory=list)
    publications_retrieved: int = 0
    candidates_considered: int = 0
    candidates_verified: int = 0
    researchers: list[ResearcherMatch] = field(default_factory=list)
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RunResult:
    run_id: str
    cluster: str
    started_at: str
    settings: dict
    topics: list[TopicResult] = field(default_factory=list)
    duration_seconds: float = 0.0

    @property
    def failed_topics(self) -> list[TopicResult]:
        return [t for t in self.topics if t.error]

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "cluster": self.cluster,
            "started_at": self.started_at,
            "settings": self.settings,
            "duration_seconds": self.duration_seconds,
            "topics": [t.to_dict() for t in self.topics],
            "errors": [{"topic_id": t.topic_id, "error": t.error} for t in self.failed_topics],
        }
