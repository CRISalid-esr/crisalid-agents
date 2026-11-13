import re
from datetime import date
from pathlib import Path

import pytest

from common.horizon import wp_parser as P
from common.horizon.wp_parser import parse_pages

FIXTURES = Path(__file__).parent / "fixtures" / "horizon"


def load_fixture(name: str) -> list[str]:
    raw = (FIXTURES / name).read_text(encoding="utf-8")
    return re.split(r"\n<<<PAGE \d+>>>\n", raw)[1:]


@pytest.fixture(scope="module")
def cl2():
    # Cover, two TOC pages, the 2026 call overview and the pages of two complete topics (+ the header of a third).
    return parse_pages(load_fixture("cl2_excerpt.txt"), "wp-5-culture-creativity-and-inclusive-society_horizon-2026-2027_en.pdf")


def test_cover_and_file_metadata(cl2):
    assert (cl2.part, cl2.cluster, cl2.work_programme) == (5, "CL2", "2026-2027")
    assert cl2.cluster_label == "Culture, Creativity and Inclusive Society"


def test_topics_found_with_titles_joined_over_wrapped_lines(cl2):
    ids = [t.topic_id for t in cl2.topics]
    assert ids == ["HORIZON-CL2-2026-01-HERITAGE-01", "HORIZON-CL2-2026-01-HERITAGE-02", "HORIZON-CL2-2026-01-HERITAGE-03"]
    first = cl2.topics[0]
    assert first.title.startswith("“Artistic intelligence”")
    assert first.title.endswith("boost innovation and competitiveness")


def test_overview_table_fields(cl2):
    topic = cl2.topics[1]
    assert topic.call_id == "HORIZON-CL2-2026-01"
    assert topic.call_name == "Culture, Creativity and Inclusive Society 2026"
    assert topic.type_of_action == "IA"
    assert topic.type_of_action_label == "Innovation Actions"
    assert topic.expected_projects == 2
    assert topic.opening_date == date(2026, 5, 12)
    assert topic.deadlines == [date(2026, 9, 23)]
    assert topic.destination.endswith("Cultural Heritage and Cultural and Creative Industries")


def test_specific_conditions_amounts(cl2):
    topic = cl2.topics[0]
    assert (topic.contribution_min_eur, topic.contribution_max_eur) == (4_500_000, 5_000_000)
    assert topic.indicative_budget_eur == 15_000_000
    assert topic.stage == "single"
    assert "General Annex" in topic.conditions_text


def test_sections_and_passages(cl2):
    topic = cl2.topics[0]
    assert topic.expected_outcome.startswith("Proposals should contribute to the first two expected outcomes")
    assert "\n• " in topic.expected_outcome
    assert topic.scope.startswith("Artistic research fosters inter-, multi-, and trans-disciplinary thinking")
    assert topic.scope.endswith("policymaking.")
    # Headers, footers and footnotes never leak into the sections.
    for text in (topic.expected_outcome, topic.scope):
        assert "Work Programme 2026-2027" not in text
        assert not re.search(r"Part 5 - Page", text)
        assert not re.search(r"^\d{1,2} [A-Z]", text, re.M)
    # Focus blocks become separate passages, in order.
    sections = [(p.section, p.text[:8]) for p in topic.passages]
    assert sections == [
        ("expected_outcome", "Proposal"), ("expected_outcome", "Focus 1 "), ("expected_outcome", "Focus 2 "),
        ("scope", "Artistic"), ("scope", "Focus 1."), ("scope", "Focus 2."),
    ]
    assert [p.order for p in topic.passages] == list(range(6))
    assert topic.source_pages == (6, 10)
    assert topic.content.startswith(topic.title)


def test_incomplete_topic_reports_warnings(cl2):
    # The fixture ends on the header of HERITAGE-03: indexed, but flagged.
    messages = {(i.topic_id, i.message) for i in cl2.warnings}
    assert ("HORIZON-CL2-2026-01-HERITAGE-03", "No Expected Outcome section") in messages
    assert ("HORIZON-CL2-2026-01-HERITAGE-03", "No Scope section") in messages
    assert cl2.errors == []


def test_toc_lines_are_not_taken_as_topics(cl2):
    # The TOC lists every CL2 topic; only those with a body block are parsed.
    assert len(cl2.topics) == 3


def test_document_serialisation(cl2):
    doc = cl2.topics[0].to_document()
    assert doc["opening_date"] == "2026-05-12" and doc["deadlines"] == ["2026-09-23"]
    assert doc["source_pages"] == [6, 10]
    assert doc["passages"][0]["section"] == "expected_outcome"
    assert "content" in doc


# ----------------------------------------------------------------------------- unit helpers


def test_merge_wrapped_ids_in_overview_tables():
    lines = [
        P._Line(1, "HORIZON-HLTH-2026-02-"), P._Line(1, "DISEASE-12: European Partnership"),
        P._Line(2, "HORIZON-CL6-2027-01-CIRCBIO-01-two- IA 10.00 Around 2"), P._Line(3, "stage: Deploying circular"),
        P._Line(3, "HORIZON-CL2-2026-01-DEMOCRACY-01: Tackling"),
    ]
    merged = [l.text for l in P._merge_wrapped_ids(lines)]
    assert merged == [
        "HORIZON-HLTH-2026-02-DISEASE-12: European Partnership",
        "HORIZON-CL6-2027-01-CIRCBIO-01-two-stage: Deploying circular IA 10.00 Around 2",
        "HORIZON-CL2-2026-01-DEMOCRACY-01: Tackling",
    ]


@pytest.mark.parametrize("row, expected", [
    ("Tackling gender-based violence RIA 12.00 3.50 to 4.00 3", ("RIA", 3)),
    ("RIA 64.00 5.00 to 8.00 9 Optimise the usage of resources", ("RIA", 9)),
    ("Identifying and addressing low-value care in RIA 38.00 Around 4 health and 9.00", ("RIA", 4)),
    ("Tools and technologies PCP 20.00 45 4.00 to 5.00 4", ("PCP", 4)),  # 45 is a footnote marker
    ("Overall indicative budget COFUND 60.00 Around 60.00 1", ("COFUND", 1)),
    ("No cells at all", (None, None)),
])
def test_overview_cells(row, expected):
    assert P._parse_overview_cells(row) == expected


def test_strip_footnotes_tolerates_skipped_numbers_and_double_spaces():
    page = ["Body line one.", "Body line two 12.", "", "12  Footnote text", "13 Another footnote", "continued"]
    body, next_number = P._strip_footnotes(page, 10)
    assert body == ["Body line one.", "Body line two 12."]
    assert next_number == 14
    # A page without footnotes is returned untouched.
    assert P._strip_footnotes(["1 million citizens", "text"], 14) == (["1 million citizens", "text"], 14)


def test_conditions_with_interleaved_label_fragments():
    lines = [
        "Specific conditions", "Expected EU", "contribution per", "project",
        "The Commission estimates that an EU contribution of between EUR contribution per project 9.00 and",
        "10.00 million would allow these outcomes to be addressed appropriately.",
        "Indicative budget The total indicative budget for the topic is EUR 20.60 million.",
        "Type of Action Research and Innovation Actions",
        "Technology Readiness Level Activities are expected to achieve TRL 5 by the end of the project – see",
    ]
    parsed = P._parse_conditions(lines)
    assert (parsed["contribution_min_eur"], parsed["contribution_max_eur"]) == (9_000_000, 10_000_000)
    assert parsed["indicative_budget_eur"] == 20_600_000
    assert parsed["type_of_action_label"] == "Research and Innovation Actions"
    assert parsed["trl"].startswith("Activities are expected to achieve TRL 5")
    around = P._parse_conditions(["The Commission estimates that an EU contribution of around EUR 7.50 contribution per project million"])
    assert (around["contribution_min_eur"], around["contribution_max_eur"]) == (7_500_000, 7_500_000)


def test_toc_destinations_without_destination_prefix():
    # CL5 style: destinations are plain headings after the "Destinations" entry.
    entries = [
        P._TocEntry("Introduction", 11, None),
        P._TocEntry("Destinations", 50, None),
        P._TocEntry("Climate sciences and responses", 50, None),
        P._TocEntry("HORIZON-CL5-2026-07-D1-01: Next generation climate monitoring", 51, "HORIZON-CL5-2026-07-D1-01"),
        P._TocEntry("Batteries", 90, None),
        P._TocEntry("HORIZON-CL5-2026-09-D2-01: Producing battery-grade materials", 91, "HORIZON-CL5-2026-09-D2-01"),
        P._TocEntry("Other Actions", 302, None),
    ]
    assert P._toc_destinations(entries) == {
        "HORIZON-CL5-2026-07-D1-01": "Climate sciences and responses",
        "HORIZON-CL5-2026-09-D2-01": "Batteries",
    }


def test_toc_destinations_with_destination_prefix_ignores_sub_headings():
    entries = [
        P._TocEntry("Destinations", 34, None),
        P._TocEntry("Destination: Leadership in materials", 34, None),
        P._TocEntry("Raw Materials", 60, None),
        P._TocEntry("HORIZON-CL4-2026-01-MAT-PROD-01: Title", 61, "HORIZON-CL4-2026-01-MAT-PROD-01"),
    ]
    assert P._toc_destinations(entries) == {"HORIZON-CL4-2026-01-MAT-PROD-01": "Destination: Leadership in materials"}


def test_chunking_splits_focus_blocks_and_long_paragraphs():
    text = "Intro paragraph.\nFocus 1 first focus text.\nFocus 2 second focus text."
    assert P._chunk(text, limit=1000) == ["Intro paragraph.", "Focus 1 first focus text.", "Focus 2 second focus text."]
    long = " ".join(f"Sentence number {i} is here." for i in range(200))
    chunks = P._chunk(long, limit=500)
    assert len(chunks) > 1 and all(len(c) <= 500 for c in chunks)
    assert " ".join(chunks) == long


def test_cluster_derivation():
    assert P._cluster_of("HORIZON-HLTH-2026-01-STAYHLTH-02", 4) == "CL1"
    assert P._cluster_of("HORIZON-CL5-2026-08-Two-Stage-D1-06", 8) == "CL5"
    assert P._cluster_of("HORIZON-MISS-2026-01", 10) == "MISS"


def test_text_cleaning_of_extraction_artifacts():
    assert P._clean_text("gender -based violence in the EU -funded domain14. inter -, multi -, and trans") == \
        "gender-based violence in the EU-funded domain. inter-, multi-, and trans"
    assert P._clean_text("art-science- technology") == "art-science-technology"


# ----------------------------------------------------------------------------- real PDFs (opt-in)


def test_real_work_programme_pdfs(request):
    if not request.config.getoption("--run-pdf"):
        pytest.skip("pass --run-pdf to parse the real PDFs of HORIZON_WP_DIR")
    import os

    wp_dir = Path(os.environ.get("HORIZON_WP_DIR", "data/cff/he/2026-27"))
    pdfs = sorted(wp_dir.glob("*.pdf"))
    assert pdfs, f"no PDF in {wp_dir}"
    total = 0
    for pdf in pdfs:
        result = P.parse_pdf(pdf)
        assert result.errors == [], [i.message for i in result.errors]
        assert all(t.scope and t.expected_outcome and t.destination and t.call_id for t in result.topics), pdf.name
        total += len(result.topics)
    if len(pdfs) == 6:
        assert total == 409
