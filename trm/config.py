import os
from dataclasses import dataclass, field, replace
from pathlib import Path


@dataclass(frozen=True)
class TRMSettings:
    model: str
    max_queries: int = 10
    max_candidates: int = 25
    min_score: float = 0.35
    max_researchers: int = 15
    concurrency: int = 4
    output_dir: Path = Path("reports")
    publications_per_candidate: int = 8  # shown to the verification prompt
    top_publications_for_prescore: int = 5
    rank_constant: int = 60
    abstract_chars: int = 600
    scope_chars: int = 6000
    llm_retries: int = 2
    recency: tuple[tuple[int, float], ...] = field(default=((5, 1.0), (10, 0.6)))  # (max age in years, weight)
    recency_floor: float = 0.3

    @classmethod
    def from_env(cls, **overrides) -> "TRMSettings":
        env = os.environ
        settings = cls(
            model=env.get("TRM_MODEL", ""),
            max_queries=int(env.get("TRM_MAX_QUERIES", "10")),
            max_candidates=int(env.get("TRM_MAX_CANDIDATES", "25")),
            min_score=float(env.get("TRM_MIN_SCORE", "0.35")),
            max_researchers=int(env.get("TRM_MAX_RESEARCHERS", "15")),
            concurrency=int(env.get("TRM_CONCURRENCY", "4")),
            output_dir=Path(env.get("TRM_OUTPUT_DIR", "reports")),
        )
        return replace(settings, **{k: v for k, v in overrides.items() if v is not None})

    def to_dict(self) -> dict:
        return {
            "model": self.model, "max_queries": self.max_queries, "max_candidates": self.max_candidates,
            "min_score": self.min_score, "max_researchers": self.max_researchers, "concurrency": self.concurrency,
            "publications_per_candidate": self.publications_per_candidate,
            "top_publications_for_prescore": self.top_publications_for_prescore, "rank_constant": self.rank_constant,
        }
