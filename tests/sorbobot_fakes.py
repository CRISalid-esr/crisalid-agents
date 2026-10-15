"""In-memory stand-ins for the Sorbobot collaborators, shared by the sorbobot tests."""

from agents.sorbobot.config import SorbobotSettings
from agents.sorbobot.models import TopicMatch
from agents.sorbobot.services import SorbobotServices
from agents.sorbobot.taxi import TaxiUnavailableError


def make_services(graph=None, taxi=None, concept_judge=None, publication_judge=None, **settings) -> SorbobotServices:
    values = {"taxi_url": "http://taxi.test", "min_authors": 1, "max_authors": 5, **settings}
    return SorbobotServices(
        graph=graph or FakeGraph(),
        taxi=taxi or FakeTaxi(["t1"]),
        settings=SorbobotSettings(**values),
        concept_judge=concept_judge,
        publication_judge=publication_judge,
    )


def evidence(person_uid: str, doc_uid: str, topic_uid: str, similarity: float = 0.8, date: str = "2024") -> dict:
    return {
        "person_uid": person_uid,
        "display_name": person_uid.title(),
        "doc_uid": doc_uid,
        "doc_title": f"Title of {doc_uid}",
        "publication_date": date,
        "topic_uid": topic_uid,
        "similarity": similarity,
    }


class FakeTaxi:
    """Matches the given topics in this order, with similarities 0.9, 0.8, 0.7…"""

    min_query_chars = 25

    def __init__(self, uids=(), unavailable=False):
        self.matches = [TopicMatch(uid, round(0.9 - 0.1 * index, 2)) for index, uid in enumerate(uids)]
        self.unavailable = unavailable

    async def match_topics(self, text):
        if self.unavailable:
            raise TaxiUnavailableError("taxi is down")
        return self.matches


class FakeJudge:
    """Concept judge answering fixed scores by position, or None for "no usable judgement"."""

    def __init__(self, scores=None):
        self.scores = scores
        self.calls = []

    async def score(self, query, paths):
        self.calls.append(list(paths))
        return self.scores


class FakePublicationJudge:
    def __init__(self, keep):
        self.keep = keep

    async def select(self, question, publications, limit):
        return [publications[index] for index in self.keep]


class FakeGraph:
    """Answers from dictionaries: every topic holds 2 documents unless ``counts`` says otherwise."""

    def __init__(self, evidence=None, siblings=None, children=None, counts=None, persons=(), person_concepts=(),
                 person_documents=(), summaries=None):
        self.evidence = evidence or {}
        self.siblings = siblings or {}
        self.children = children or {}
        self.counts = counts or {}
        self.persons = list(persons)
        self.person_concepts = list(person_concepts)
        self.person_documents = list(person_documents)
        self.summaries = summaries or {}
        self.evidence_requests = []
        self.person_searches = []

    async def get_concepts(self, uids, similarity_threshold):
        return {
            uid: {"uid": uid, "name": uid.upper(), "description": f"About {uid}.", "nb_docs": self.counts.get(uid, 2)}
            for uid in uids
        }

    async def get_concept_paths(self, uids):
        return {uid: f"Science [Domain] > {uid.upper()} [Topic]" for uid in uids}

    async def list_children(self, uids):
        return sorted({child for uid in uids for child in self.children.get(uid, [])})

    async def list_siblings(self, uids):
        return sorted({sibling for uid in uids for sibling in self.siblings.get(uid, [])} - set(uids))

    async def list_expert_evidence(self, uids, similarity_threshold):
        if not uids:
            return []
        self.evidence_requests.append(sorted(uids))
        return self.evidence.get(frozenset(uids), [])

    async def get_publication_counts(self, person_uids):
        return {}

    async def search_persons(self, name, max_results):
        self.person_searches.append(name)
        return self.persons

    async def list_person_concepts(self, person_uid, similarity_threshold, limit):
        return self.person_concepts[:limit]

    async def list_person_documents(self, person_uid, concept_uids, similarity_threshold, limit):
        return self.person_documents[:limit]

    async def get_document_summaries(self, uids):
        return {uid: self.summaries[uid] for uid in uids if uid in self.summaries}
