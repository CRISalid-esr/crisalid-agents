"""TRM batch: scoring, structured LLM parsing, the per-topic graph with fakes, and report rendering."""

import json
from datetime import date

import pytest
from langchain_core.messages import AIMessage

from tests.fake_llm import ScriptedChatModel
from trm.config import TRMSettings
from trm.llm import LLMJsonError, StructuredLLM, flatten_queries, parse_json_object
from trm.models import Candidate, Publication, RunResult, TopicResult, Verification, PublicationVerdict
from trm.pipeline import build_graph, run_topic
from trm.report import render_html, render_markdown, write_reports
from trm.scoring import build_candidates, final_matches, publication_year, rank_publications, recency_weight
from trm.sources import internal_only_filter, unit_label

SETTINGS = TRMSettings(model="test-model", max_queries=6, max_candidates=3, min_score=0.35, max_researchers=2,
                       concurrency=2, top_publications_for_prescore=2, rank_constant=60)

TOPIC = {
    "topic_id": "HORIZON-CL2-2026-01-HERITAGE-02", "title": "Boosting creative startups", "cluster": "CL2",
    "call_id": "HORIZON-CL2-2026-01", "deadlines": ["2026-09-23"], "destination": "Destination Heritage",
    "expected_outcome": "Startups in the creative sectors scale up.", "scope": "Support creative entrepreneurship.",
}


def _row(uid, score, contributors, title="T", year="2024-01-01", abstract=None):
    return {"uid": uid, "score": score, "titles": [f"{title} {uid}"], "abstracts": [abstract] if abstract else [],
            "publication_date": year,
            "contributors": [{"uid": c, "name": c.upper(), "external": False} for c in contributors]}


# ----------------------------------------------------------------------------- scoring


def test_rank_publications_fuses_queries_and_scales_by_best_score():
    results = {"q1": [_row("p1", 0.9, ["a"]), _row("p2", 0.8, ["b"])], "q2": [_row("p2", 0.85, ["b"]), _row("p3", 0.7, ["a", "c"])]}
    pubs = rank_publications(results, rank_constant=60)
    assert pubs["p2"].query_ranks == {"q1": 2, "q2": 1} and pubs["p2"].best_score == 0.85
    assert abs(pubs["p2"].rrf - (1 / 62 + 1 / 61)) < 1e-9
    assert pubs["p2"].score > pubs["p1"].score  # hit by two queries beats a single first rank
    assert pubs["p3"].contributors == [{"uid": "a", "name": "A"}, {"uid": "c", "name": "C"}]
    assert pubs["p1"].year == 2024 and pubs["p1"].title == "T p1"


def test_build_candidates_sums_the_best_publications_only():
    results = {"q": [_row(f"p{i}", 0.9 - i * 0.1, ["a"]) for i in range(4)] + [_row("x", 0.95, ["b"])]}
    pubs = rank_publications(results, 60)
    candidates = build_candidates(pubs, SETTINGS)
    a = next(c for c in candidates if c.person_uid == "a")
    assert a.pre_score == pytest.approx(sum(p.score for p in a.publications[:2]))
    assert [p.uid for p in a.publications][:2] == ["p0", "p1"]
    assert len(candidates) == 2 and candidates[0].person_uid == "a"


def test_build_candidates_caps_the_number_of_candidates():
    results = {"q": [_row(f"p{i}", 0.9, [f"r{i}"]) for i in range(10)]}
    assert len(build_candidates(rank_publications(results, 60), SETTINGS)) == SETTINGS.max_candidates


def test_recency_and_year_parsing():
    today = date(2026, 1, 1)
    assert recency_weight(2024, SETTINGS, today) == 1.0
    assert recency_weight(2018, SETTINGS, today) == 0.6
    assert recency_weight(2010, SETTINGS, today) == 0.3
    assert recency_weight(None, SETTINGS, today) == 1.0
    assert publication_year("2021-05-03") == 2021 and publication_year("2019") == 2019 and publication_year(None) is None


def _candidate(uid, pubs):
    return Candidate(person_uid=uid, name=uid.upper(), publications=pubs, pre_score=sum(p.score for p in pubs))


def _pub(uid, score, year=2024):
    return Publication(uid=uid, title=f"T {uid}", abstract=None, year=year, best_score=1.0, contributors=[], score=score)


def test_final_matches_normalises_thresholds_and_orders():
    a = _candidate("a", [_pub("p1", 0.5), _pub("p2", 0.4), _pub("p3", 0.3)])
    b = _candidate("b", [_pub("p4", 0.2)])
    c = _candidate("c", [_pub("p5", 0.9)])
    d = _candidate("d", [_pub("p6", 0.9)])
    verified = [
        (a, Verification([PublicationVerdict("p1", True, "yes"), PublicationVerdict("p2", False, "no")], "strong", "fits")),
        (b, Verification([PublicationVerdict("p4", True, "yes")], "weak", "loose")),
        (c, Verification([PublicationVerdict("p5", True, "yes")], "none", "no")),      # overall none → dropped
        (d, Verification([PublicationVerdict("p6", False, "no")], "plausible", "no")),  # no relevant paper → dropped
    ]
    matches = final_matches("T1", verified, {"a": ["Lab A (LA)"]}, SETTINGS, today=date(2026, 1, 1))
    assert [m.person_uid for m in matches] == ["a", "b"]
    assert matches[0].score == 1.0 and matches[0].raw_score == 0.5 and matches[0].units == ["Lab A (LA)"]
    assert matches[0].pair_id == "T1|a" and matches[0].overall == "strong"
    assert matches[1].score == pytest.approx(0.4) and matches[1].units == []
    assert [p["relevant"] for p in matches[0].publications] == [True, False, False]
    # threshold: b falls below 0.35 when a's raw score grows
    a2 = _candidate("a", [_pub("p1", 1.0)])
    verified[0] = (a2, verified[0][1])
    assert [m.person_uid for m in final_matches("T1", verified, {}, SETTINGS)] == ["a"]
    assert final_matches("T1", [], {}, SETTINGS) == []


# ----------------------------------------------------------------------------- LLM helpers


def test_parse_json_object_accepts_fences_and_prose():
    assert parse_json_object('Sure:\n```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('text {"a": {"b": 2}} trailing') == {"a": {"b": 2}}
    with pytest.raises(LLMJsonError):
        parse_json_object("no json here")
    with pytest.raises(LLMJsonError):
        parse_json_object('{"a": }')


async def test_structured_llm_retries_on_invalid_json():
    llm = StructuredLLM(ScriptedChatModel(responses=[AIMessage(content="oops"), AIMessage(content='{"ok": true}')], calls=[]), retries=1)
    assert await llm.json("prompt") == {"ok": True}
    assert llm.calls == 2
    failing = StructuredLLM(ScriptedChatModel(responses=[AIMessage(content="x"), AIMessage(content="y")], calls=[]), retries=1)
    with pytest.raises(LLMJsonError):
        await failing.json("prompt")


def test_flatten_queries_orders_dedupes_caps_and_falls_back_to_the_title():
    data = {"queries": {"topic": ["Topic q"], "summary": ["Summary q"], "variations": ["V1", "v1", "V2"]},
            "researcher_terms": {"en": ["EN1"], "fr": ["FR1", "FR2"]}}
    assert flatten_queries(data, TOPIC, 6) == ["Topic q", "Summary q", "V1", "V2", "EN1", "FR1"]
    fallback = flatten_queries({}, TOPIC, 6)
    assert fallback == ["Boosting creative startups. Support creative entrepreneurship."]


def test_internal_only_filter_and_unit_label():
    rows = [
        {"uid": "p1", "contributors": [{"uid": "a", "external": False}, {"uid": "x", "external": True}]},
        {"uid": "p2", "contributors": [{"uid": "y", "external": True}]},
    ]
    assert internal_only_filter(rows) == [{"uid": "p1", "contributors": [{"uid": "a", "external": False}]}]
    assert unit_label({"institution_uid": "u1", "long_labels": [{"value": "Lab"}], "short_labels": [{"value": "L"}]}) == "Lab (L)"
    assert unit_label({"institution_uid": "u1", "long_labels": [], "short_labels": []}) == "u1"


# ----------------------------------------------------------------------------- pipeline with fakes


class FakeEmbedder:
    async def embed_text(self, text):
        return [1.0, 0.0]

    async def embed_texts(self, texts):
        return [[1.0, 0.0] for _ in texts]


class FakeSource:
    def __init__(self, rows_by_query: dict[str, list[dict]], units: dict[str, list[str]]):
        self.rows_by_query = rows_by_query
        self.units = units
        self.queries: list[str] = []
        self.membership_calls: list[str] = []

    async def search_publications(self, query, vector, limit=100):
        self.queries.append(query)
        return internal_only_filter(self.rows_by_query.get(query, []))

    async def memberships(self, person_uid):
        self.membership_calls.append(person_uid)
        return self.units.get(person_uid, [])

    async def aclose(self):
        return None


EXPANSION = json.dumps({
    "summary": "Creative-sector startups and their scale-up.",
    "queries": {"topic": ["creative startups"], "summary": ["creative entrepreneurship"], "variations": ["cultural industries innovation"]},
    "researcher_terms": {"en": ["creative economy"], "fr": ["industries culturelles"]},
})


def _verdict(uids_relevant: dict, overall: str) -> str:
    return json.dumps({"publications": [{"uid": u, "relevant": r, "reason": "because"} for u, r in uids_relevant.items()],
                       "overall": overall, "justification": f"{overall} fit"})


async def test_pipeline_end_to_end_with_fakes():
    rows = {
        "creative startups": [_row("p1", 0.9, ["anna"], abstract="Startups in the creative economy"), _row("p2", 0.8, ["bob", "ext"])],
        "creative entrepreneurship": [_row("p1", 0.85, ["anna"]), _row("p3", 0.7, ["carl"], year="2012-01-01")],
        "cultural industries innovation": [_row("p2", 0.75, ["bob"])],
    }
    rows["creative startups"][1]["contributors"][1]["external"] = True
    source = FakeSource(rows, {"anna": ["Lab A (LA)"], "bob": ["Lab B"]})
    llm = ScriptedChatModel(responses=[
        AIMessage(content=EXPANSION),
        AIMessage(content=_verdict({"p1": True}, "strong")),        # anna (highest pre-score)
        AIMessage(content=_verdict({"p2": True}, "plausible")),     # bob
        AIMessage(content=_verdict({"p3": False}, "none")),         # carl
    ], calls=[])
    settings = TRMSettings(model="m", max_queries=6, max_candidates=3, min_score=0.1, max_researchers=5, concurrency=1)
    graph = build_graph(StructuredLLM(llm), FakeEmbedder(), source, settings)
    result = await run_topic(graph, TOPIC)

    assert result.error is None and result.topic_id == TOPIC["topic_id"]
    assert result.summary == "Creative-sector startups and their scale-up."
    assert result.queries == ["creative startups", "creative entrepreneurship", "cultural industries innovation", "creative economy", "industries culturelles"]
    assert set(source.queries) == set(result.queries)
    assert result.publications_retrieved == 3 and result.candidates_considered == 3 and result.candidates_verified == 3
    assert [r.person_uid for r in result.researchers] == ["anna", "bob"]
    assert result.researchers[0].score == 1.0 and result.researchers[0].units == ["Lab A (LA)"]
    assert result.researchers[0].pair_id == f"{TOPIC['topic_id']}|anna"
    assert result.researchers[1].overall == "plausible"
    assert sorted(source.membership_calls) == ["anna", "bob"]  # carl was not retained
    assert "ext" not in {c for r in result.researchers for c in [r.person_uid]}
    assert result.duration_seconds >= 0


async def test_pipeline_records_topic_failure_and_verification_failure():
    # Expansion fails (no JSON at all, retries exhausted) → topic error, no exception.
    llm = ScriptedChatModel(responses=[AIMessage(content="no json")] * 3, calls=[])
    graph = build_graph(StructuredLLM(llm, retries=2), FakeEmbedder(), FakeSource({}, {}), SETTINGS)
    result = await run_topic(graph, TOPIC)
    assert result.error and "LLMJsonError" in result.error and result.researchers == []

    # One verification fails (bad JSON twice) → warning, the other candidate is still scored.
    rows = {"creative startups": [_row("p1", 0.9, ["anna"]), _row("p2", 0.8, ["bob"])]}
    llm = ScriptedChatModel(responses=[
        AIMessage(content=EXPANSION), AIMessage(content="bad"), AIMessage(content="bad"),
        AIMessage(content=_verdict({"p2": True}, "plausible")),
    ], calls=[])
    settings = TRMSettings(model="m", max_candidates=2, min_score=0.0, concurrency=1, llm_retries=1)
    graph = build_graph(StructuredLLM(llm, retries=1), FakeEmbedder(), FakeSource(rows, {}), settings)
    result = await run_topic(graph, TOPIC)
    assert result.error is None
    assert result.candidates_verified == 1 and [r.person_uid for r in result.researchers] == ["bob"]
    assert result.warnings and "anna" in result.warnings[0]


# ----------------------------------------------------------------------------- reports


def _run() -> RunResult:
    topic = TopicResult(topic_id="T1", title="Title 1", cluster="CL2", call_id="C1", deadlines=["2026-09-23"],
                        destination="Dest", summary="Sum", queries=["q"], publications_retrieved=3,
                        candidates_considered=2, candidates_verified=2)
    from trm.models import ResearcherMatch
    topic.researchers = [ResearcherMatch(pair_id="T1|anna", person_uid="anna", name="Anna <A>", units=["Lab A"], score=1.0,
                                         raw_score=0.5, overall="strong", justification="Fits & more",
                                         publications=[{"uid": "p1", "title": "Paper", "year": 2024, "score": 0.5, "relevant": True, "reason": "yes"},
                                                       {"uid": "p2", "title": "Other", "year": None, "score": 0.1, "relevant": False, "reason": "no"}])]
    failed = TopicResult(topic_id="T2", title="Title 2", cluster="CL2", call_id=None, deadlines=[], destination=None, error="boom")
    return RunResult(run_id="r1", cluster="CL2", started_at="2026-09-06T10:00:00+00:00", settings=SETTINGS.to_dict(), topics=[topic, failed])


def test_markdown_and_html_reports():
    md = render_markdown(_run())
    assert "## T1 — Title 1" in md and "Pair id: `T1|anna`" in md and "Paper (2024) — yes" in md
    assert "Other" not in md.split("### 1.")[1]  # irrelevant publications are not listed
    assert "**Failed**: boom" in md and "`T2`" in md
    html = render_html(_run())
    assert "Anna &lt;A&gt;" in html and "Fits &amp; more" in html and "T1|anna" in html


def test_write_reports(tmp_path):
    paths = write_reports(_run(), tmp_path, pdf=False)
    assert set(paths) == {"json", "md"} and paths["json"].name == "trm-CL2-2026-09-06.json"
    data = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert data["errors"] == [{"topic_id": "T2", "error": "boom"}] and data["topics"][0]["researchers"][0]["pair_id"] == "T1|anna"


def test_pdf_report_smoke(tmp_path):
    pytest.importorskip("weasyprint")
    paths = write_reports(_run(), tmp_path, pdf=True)
    assert "pdf" in paths and paths["pdf"].stat().st_size > 1000
    assert paths["pdf"].read_bytes().startswith(b"%PDF")
