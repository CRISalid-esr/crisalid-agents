import math

from agents.sorbobot.ranking import group_evidence, rank_experts
from tests.sorbobot_fakes import evidence

YEAR = 2026


def test_count_method_scales_to_the_best_researcher():
    rows = [evidence("a", "d1", "t1"), evidence("a", "d2", "t1"), evidence("a", "d3", "t2"), evidence("b", "d4", "t1")]
    ranked = rank_experts(rows, {"t1": 0.9, "t2": 0.8}, {}, "count", YEAR)
    assert [(e.person_uid, e.score, e.nb_publications_on_topic) for e in ranked] == [("a", 100, 3), ("b", 33, 1)]


def test_weighted_relevance_follows_the_formula():
    rows = [
        evidence("a", "d1", "t1", similarity=0.8, date="2026-01-01"),
        evidence("a", "d1", "t2", similarity=0.9, date="2026-01-01"),  # same work, two topics: the best product counts
        evidence("b", "d2", "t1", similarity=0.8, date="2021-03-03"),  # five years old: weight halved
    ]
    ranked = rank_experts(rows, {"t1": 0.9, "t2": 0.5}, {"a": 9, "b": 1}, "weighted_relevance", YEAR)
    weights = {"a": max(0.9 * 0.8, 0.5 * 0.9), "b": 0.9 * 0.8 * 0.5}
    shares = {"a": weights["a"] / 10, "b": weights["b"] / 2}
    expected = {uid: 100 * (weights[uid] / max(weights.values())) * (shares[uid] / max(shares.values())) for uid in weights}
    by_uid = {e.person_uid: e for e in ranked}
    # "a" has the best weight but 9 publications in total: the share term lets the focused "b" win (50 vs 40).
    assert {uid: e.score for uid, e in by_uid.items()} == {uid: round(value) for uid, value in expected.items()} == {"a": 40, "b": 50}
    assert [e.person_uid for e in ranked] == ["b", "a"]
    assert by_uid["a"].nb_publications_total == 9 and by_uid["a"].topic_uids == {"t1", "t2"}


def test_fallbacks_for_missing_date_similarity_and_total():
    rows = [
        evidence("a", "d1", "t1", date=None),            # takes the median year of the dated publications (2026)
        evidence("b", "d2", "t1", date="2026"),
        evidence("c", "d3", "t2", date="2016"),
        evidence("d", "d4", "sibling", date="2026"),     # no taxi similarity: the lowest taxi similarity (0.6)
    ]
    ranked = rank_experts(rows, {"t1": 0.9, "t2": 0.6}, {}, "weighted_relevance", YEAR)
    by_uid = {e.person_uid: e for e in ranked}
    weights = {"a": 0.9 * 0.8, "b": 0.9 * 0.8, "c": 0.6 * 0.8 * 2 ** (-10 / 5), "d": 0.6 * 0.8}
    # Unknown totals: N = publications on the subject = 1 for everyone, so the share ratio equals the weight ratio.
    expected = {uid: round(100 * (w / max(weights.values())) ** 2) for uid, w in weights.items()}
    assert {uid: e.score for uid, e in by_uid.items()} == expected
    assert by_uid["a"].score == by_uid["b"].score == 100
    assert by_uid["b"].nb_publications_total == 1


def test_the_sibling_fallback_uses_the_lowest_taxi_similarity():
    rows = [evidence("a", "d1", "t1", date="2026"), evidence("b", "d2", "sibling", date="2026")]
    ranked = rank_experts(rows, {"t1": 0.9, "t2": 0.6}, {"a": 0, "b": 0}, "weighted_relevance", YEAR)
    by_uid = {e.person_uid: e for e in ranked}
    assert by_uid["a"].score == 100 and by_uid["b"].score == round(100 * (0.6 / 0.9) * (0.6 / 0.9))


def test_duplicate_works_count_once_with_the_best_similarity_per_topic():
    rows = [
        evidence("a", "d1", "t1", similarity=0.6, title="The Book", date="2020"),
        evidence("a", "d2", "t1", similarity=0.9, title="The  book!", date=None),
        evidence("a", "d3", "t1", similarity=0.7, title="", date="2019"),
        evidence("a", "d4", "t1", similarity=0.7, title="", date="2018"),
    ]
    grouped = group_evidence(rows)
    name, works = grouped["a"]
    assert name == "A" and len(works) == 3
    book = next(work for work in works.values() if work.title == "The Book")
    assert book.similarities == {"t1": 0.9} and book.year == 2020
    ranked = rank_experts(rows, {"t1": 1.0}, {}, "count", YEAR)
    assert ranked[0].nb_publications_on_topic == 3
    assert [w.year for w in ranked[0].recent_works(2)] == [2020, 2019]


def test_no_evidence_no_expert_and_zero_weights_do_not_divide_by_zero():
    assert rank_experts([], {"t1": 0.9}, {}, "weighted_relevance", YEAR) == []
    ranked = rank_experts([evidence("a", "d1", "t1", similarity=0.0)], {"t1": 0.0}, {}, "weighted_relevance", YEAR)
    assert ranked[0].score == 0 and not math.isnan(ranked[0].score)
