import json

import pytest

from agents.generic_agent.schema_postprocessor import (
    _compact_types,
    _filter_properties,
    _format_count,
    _simplify_type,
    compact_schema,
)

RAW_SCHEMA_FIXTURE = {
    "nodeLabels": [
        {
            "name": "Person",
            "properties": [
                {"name": "display_name", "types": ["STRING NOT NULL"]},
                {"name": "display_name_variants", "types": ["LIST<NOTHING> NOT NULL", "LIST<STRING NOT NULL> NOT NULL"]},
                {"name": "external", "types": ["BOOLEAN NOT NULL"]},
                {"name": "uid", "types": ["STRING NOT NULL"]},
            ],
            "count": 105138,
        },
        {
            "name": "Document",
            "properties": [
                {"name": "document_type", "types": ["STRING NOT NULL"]},
                {"name": "oa_computation_timestamp", "types": ["ZONED DATETIME NOT NULL"]},
                {"name": "publication_date", "types": ["STRING NOT NULL"]},
                {"name": "publication_date_start", "types": ["ZONED DATETIME NOT NULL"]},
                {"name": "publication_date_end", "types": ["ZONED DATETIME NOT NULL"]},
                {"name": "to_be_deleted", "types": ["BOOLEAN NOT NULL"]},
                {"name": "uid", "types": ["STRING NOT NULL"]},
            ],
            "count": 102231,
        },
        {
            "name": "JournalArticle",
            "properties": [
                {"name": "document_type", "types": ["STRING NOT NULL"]},
                {"name": "oa_computation_timestamp", "types": ["ZONED DATETIME NOT NULL"]},
                {"name": "publication_date", "types": ["STRING NOT NULL"]},
                {"name": "publication_date_start", "types": ["ZONED DATETIME NOT NULL"]},
                {"name": "publication_date_end", "types": ["ZONED DATETIME NOT NULL"]},
                {"name": "to_be_deleted", "types": ["BOOLEAN NOT NULL"]},
                {"name": "uid", "types": ["STRING NOT NULL"]},
            ],
            "count": 31506,
        },
        {
            "name": "SourceRecord",
            "properties": [
                {"name": "harvester", "types": ["STRING NOT NULL"]},
                {"name": "uid", "types": ["STRING NOT NULL"]},
            ],
            "count": 157506,
        },
    ],
    "relationships": [
        {
            "type": "HARVESTED_FOR",
            "properties": [],
            "startNode": "SourceRecord",
            "endNode": "Person",
            "count": 157826,
        },
        {
            "type": "EMPLOYED_AT",
            "properties": [
                {"name": "position_code", "types": ["STRING NOT NULL"]},
            ],
            "startNode": "Person",
            "endNode": "Institution",
            "count": 5907,
        },
        {
            "type": "MEMBER_OF",
            "properties": [],
            "startNode": "Person",
            "endNode": "ResearchUnit",
            "count": 3439,
        },
    ],
    "constraints": [
        {"name": "person_uid_unique", "type": "UNIQUENESS"},
    ],
}


# ── Type simplification ────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("STRING NOT NULL", "string"),
    ("BOOLEAN NOT NULL", "bool"),
    ("INTEGER NOT NULL", "int"),
    ("FLOAT NOT NULL", "float"),
    ("ZONED DATETIME NOT NULL", "datetime"),
    ("LIST<STRING NOT NULL> NOT NULL", "list<string>"),
    ("LIST<NOTHING> NOT NULL", None),
])
def test_simplify_type(raw, expected):
    assert _simplify_type(raw) == expected


# ── LIST<NOTHING> removal when a real list type is also present ───────────────

def test_list_nothing_removed_when_real_list_present():
    result = _compact_types(["LIST<NOTHING> NOT NULL", "LIST<STRING NOT NULL> NOT NULL"])
    assert result == "list<string>"


def test_list_nothing_only_returns_list():
    result = _compact_types(["LIST<NOTHING> NOT NULL"])
    assert result == "list"


# ── Count compaction ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("count,expected", [
    (105138, "105.1k"),
    (31506, "31.5k"),
    (4, "4"),
    (2_000_000, "2.0M"),
    (999, "999"),
    (1000, "1.0k"),
])
def test_format_count(count, expected):
    assert _format_count(count) == expected


# ── Property dropping ──────────────────────────────────────────────────────────

def test_noisy_properties_dropped():
    props = [
        {"name": "oa_computation_timestamp", "types": ["ZONED DATETIME NOT NULL"]},
        {"name": "publication_date_start", "types": ["ZONED DATETIME NOT NULL"]},
        {"name": "to_be_deleted", "types": ["BOOLEAN NOT NULL"]},
        {"name": "uid", "types": ["STRING NOT NULL"]},
    ]
    result = _filter_properties(props)
    names = {p["name"] for p in result}
    assert "oa_computation_timestamp" not in names
    assert "publication_date_start" not in names
    assert "to_be_deleted" not in names
    assert "uid" in names


# ── Property kept (not in either list) ────────────────────────────────────────

def test_property_not_in_either_list_is_kept():
    props = [
        {"name": "external", "types": ["BOOLEAN NOT NULL"]},
        {"name": "uid", "types": ["STRING NOT NULL"]},
    ]
    result = _filter_properties(props)
    names = {p["name"] for p in result}
    assert "external" in names


# ── Full compact_schema output ─────────────────────────────────────────────────

@pytest.fixture
def output():
    return compact_schema(RAW_SCHEMA_FIXTURE)


def test_relationship_with_properties(output):
    assert "EMPLOYED_AT {position_code:string}" in output


def test_relationship_without_properties(output):
    assert "(:Person)-[:MEMBER_OF]->(:" in output
    assert "{" not in output.split("MEMBER_OF")[1].split("\n")[0].replace("{position_code:string}", "")


def test_deduplication(output):
    assert "same properties as `:Document`" in output
    assert ":JournalArticle" in output


def test_source_label_dropped(output):
    assert ":SourceRecord" not in output


def test_source_relationship_dropped(output):
    assert "HARVESTED_FOR" not in output


def test_person_properties_present(output):
    assert "display_name:string" in output
    assert "display_name_variants:list<string>" in output
    assert "external:bool" in output
    assert "uid:string" in output


def test_dict_input():
    result = compact_schema(RAW_SCHEMA_FIXTURE)
    assert "# Compact Neo4j schema" in result


def test_json_string_input():
    result_dict = compact_schema(RAW_SCHEMA_FIXTURE)
    result_str = compact_schema(json.dumps(RAW_SCHEMA_FIXTURE))
    assert result_dict == result_str


def test_constraints_absent(output):
    assert "UNIQUENESS" not in output
    assert "person_uid_unique" not in output
