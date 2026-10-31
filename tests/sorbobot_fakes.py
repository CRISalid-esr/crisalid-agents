"""In-memory stand-ins for the Sorbobot collaborators, shared by the sorbobot tests."""

import asyncio
import json

from agents.sorbobot.config import SorbobotSettings
from agents.sorbobot.models import Concept, DocumentSummary, EvidenceRow, PersonConcept, PersonDocument, PersonHit, TopicMatch
from agents.sorbobot.taxi import TaxiUnavailableError
from agents.sorbobot.toolbox import REQUIRED_TOOLS, GraphUnavailableError


def make_settings(**overrides) -> SorbobotSettings:
    values = {"taxi_url": "http://taxi.test", **overrides}
    return SorbobotSettings(**values)


def evidence(person_uid: str, doc_uid: str, topic_uid: str, similarity: float = 0.8, date="2024-01-01",
             title: str | None = None) -> EvidenceRow:
    return EvidenceRow(
        person_uid=person_uid, display_name=person_uid.replace("-", " ").title(), doc_uid=doc_uid,
        doc_title=title if title is not None else f"Title of {doc_uid}", publication_date=date, topic_uid=topic_uid,
        similarity=similarity,
    )


def document(uid: str, concepts=("t1",), roles=("aut",), date="2021-05-01", title: str | None = None) -> PersonDocument:
    return PersonDocument(uid=uid, title=title if title is not None else f"Title of {uid}", publication_date=date,
                          concept_uids=list(concepts), roles=list(roles))


class FakeTaxi:
    """Matches the given topics in this order, with similarities 0.9, 0.8, 0.7…"""

    def __init__(self, uids=(), unavailable: bool = False):
        self.matches = [TopicMatch(uid, round(0.9 - 0.1 * index, 2)) for index, uid in enumerate(uids)]
        self.unavailable = unavailable
        self.queries: list[str] = []

    async def match_topics(self, text: str):
        self.queries.append(text)
        if self.unavailable:
            raise TaxiUnavailableError("taxi is down")
        return list(self.matches)


class FakeTopicJudge:
    """Answers fixed scores by position (1-based), or None for "no usable judgement"."""

    def __init__(self, scores: dict[int, float] | None = None, *more: dict[int, float] | None):
        self.answers = [scores, *more]
        self.calls: list[tuple[str, list[str]]] = []

    async def score(self, question, paths):
        self.calls.append((question, list(paths)))
        answer = self.answers[min(len(self.calls), len(self.answers)) - 1]
        if answer is None:
            return None
        return {number: value for number, value in answer.items() if 1 <= number <= len(paths)}


class FakePublicationJudge:
    def __init__(self, keep: list[int] | None = None):
        self.keep = keep
        self.calls = []

    async def select(self, question, publications, limit):
        self.calls.append((question, [p.uid for p in publications], limit))
        if self.keep is None:
            return publications[:limit]
        return [publications[index] for index in self.keep]


class FakeToolbox:
    """Answers from dictionaries. Every known topic has 2 documents unless ``counts`` says otherwise."""

    def __init__(self, topics=(), counts=None, siblings=None, names=None, evidence=(), totals=None, persons=(),
                 person_concepts=(), person_documents=(), summaries=None, fail: Exception | None = None):
        self.topics = set(topics)
        self.counts = counts or {}
        self.siblings = siblings or {}
        self.names = names or {}
        self.evidence = list(evidence)
        self.totals = totals or {}
        self.persons = list(persons)
        self.person_concepts = list(person_concepts)
        self.person_documents = list(person_documents)
        self.summaries = summaries or {}
        self.fail = fail
        self.calls: list[tuple] = []

    def _record(self, *call):
        self.calls.append(call)
        if self.fail is not None:
            raise self.fail

    def _known(self, uid: str) -> bool:
        return uid in self.topics or uid in self.counts

    async def get_concepts(self, uids, similarity_threshold):
        self._record("get_concepts", list(uids), similarity_threshold)
        return {uid: Concept(uid, uid.upper(), f"About {uid}. " * 40, self.counts.get(uid, 2))
                for uid in uids if self._known(uid)}

    async def get_concept_paths(self, uids):
        self._record("get_concept_paths", list(uids))
        return {uid: f"Science [Domain] > Field [Field] > Sub [SubField] > {uid.upper()} [Topic]"
                for uid in uids if self._known(uid)}

    async def list_siblings(self, uids):
        self._record("list_siblings", list(uids))
        return sorted({sibling for uid in uids for sibling in self.siblings.get(uid, [])} - set(uids))

    async def search_concepts_by_name(self, name):
        self._record("search_concepts_by_name", name)
        return list(self.names.get(name, []))

    async def list_expert_evidence(self, uids, similarity_threshold):
        self._record("list_expert_evidence", sorted(uids), similarity_threshold)
        wanted = set(uids)
        return [row for row in self.evidence if row.topic_uid in wanted and row.similarity >= similarity_threshold]

    async def get_publication_counts(self, person_uids):
        self._record("get_publication_counts", list(person_uids))
        return {uid: self.totals[uid] for uid in person_uids if uid in self.totals}

    async def search_persons(self, name, max_results):
        self._record("search_persons", name, max_results)
        return self.persons[:max_results]

    async def list_person_concepts(self, person_uid, similarity_threshold, limit):
        self._record("list_person_concepts", person_uid, similarity_threshold, limit)
        return self.person_concepts[:limit]

    async def list_person_documents(self, person_uid, concept_uids, similarity_threshold, limit):
        self._record("list_person_documents", person_uid, list(concept_uids), similarity_threshold, limit)
        return [PersonDocument(d.uid, d.title, d.publication_date, list(d.concept_uids), list(d.roles))
                for d in self.person_documents[:limit]]

    async def get_document_summaries(self, uids):
        self._record("get_document_summaries", list(uids))
        return {uid: self.summaries[uid] for uid in uids if uid in self.summaries}


def internal(uid: str, name: str, score: float = 1.0) -> PersonHit:
    return PersonHit(uid=uid, display_name=name, external=False, score=score)


def external(uid: str, name: str, score: float = 2.0) -> PersonHit:
    return PersonHit(uid=uid, display_name=name, external=True, score=score)


def concept(uid: str, nb: int) -> PersonConcept:
    return PersonConcept(uid=uid, name=uid.upper(), nb_publications=nb)


def summary(uid: str, abstract: str | None = "An abstract.", keywords=("kw1", "kw2")) -> DocumentSummary:
    return DocumentSummary(uid=uid, abstract=abstract, keywords=tuple(keywords))


class FakeMCPTool:
    """A toolbox tool: a name and an ``ainvoke`` answering a JSON string, raising, or sleeping."""

    def __init__(self, name: str, response="null", error: Exception | None = None, delay: float = 0.0):
        self.name = name
        self.response = response if isinstance(response, str) else json.dumps(response)
        self.error = error
        self.delay = delay
        self.calls: list[dict] = []

    async def ainvoke(self, arguments: dict) -> str:
        self.calls.append(arguments)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.response


def mcp_toolset(**responses) -> list[FakeMCPTool]:
    """Every required tool, answering ``null`` unless a response is given (keys use underscores)."""
    return [FakeMCPTool(name, responses.get(name.replace("-", "_"), "null")) for name in REQUIRED_TOOLS]


__all__ = ["GraphUnavailableError"]
