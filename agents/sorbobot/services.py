"""The collaborators every Sorbobot search receives."""

from dataclasses import dataclass

from agents.sorbobot.config import SorbobotSettings
from agents.sorbobot.graph import SorbobotGraph
from agents.sorbobot.judges import ConceptJudge, PublicationJudge
from agents.sorbobot.taxi import TaxiClient


@dataclass(frozen=True)
class SorbobotServices:
    graph: SorbobotGraph
    taxi: TaxiClient
    settings: SorbobotSettings
    concept_judge: ConceptJudge | None = None
    publication_judge: PublicationJudge | None = None
