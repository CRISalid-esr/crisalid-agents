import json
import re

ALWAYS_KEEP_PROPERTIES = {
    "uid", "uri", "display_name", "display_names", "name",
    "first_name", "last_name", "acronym", "type", "value", "language",
    "document_type", "publication_date", "issued", "harvester", "upw_oa_status",
    "oa_status", "source", "source_identifier", "roles", "role", "rank",
    "titles", "publisher", "issn_l", "latitude", "longitude",
}

DEFAULT_DROP_PROPERTIES = {
    "oa_computation_timestamp", "oa_computed_status",
    "oa_doaj_success_status", "oa_upw_success_status",
    "publication_date_start", "publication_date_end",
    "to_be_deleted", "to_be_recomputed", "raw_issued",
    "hal_collection_codes", "hal_submit_type", "last_checked",
    "identifier_signature", "source_organization_uids", "excluded_identifiers",
    "parameters", "path", "application", "status", "timestamp",
}

_BASE_TYPE_MAP = {
    "STRING": "string",
    "BOOLEAN": "bool",
    "INTEGER": "int",
    "FLOAT": "float",
    "ZONED DATETIME": "datetime",
}


def _simplify_type(raw: str) -> str | None:
    t = re.sub(r"\s+NOT NULL", "", raw).strip()
    m = re.match(r"^LIST<(.+)>$", t)
    if m:
        inner = _simplify_type(m.group(1))
        return None if inner is None else f"list<{inner}>"
    if t == "NOTHING":
        return None
    return _BASE_TYPE_MAP.get(t, t.lower())


def _compact_types(types: list[str]) -> str:
    simplified = [s for raw in types if (s := _simplify_type(raw)) is not None]
    return simplified[0] if simplified else "list"


def _format_count(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}k"
    return str(count)


def _filter_properties(raw_props: list[dict]) -> list[dict]:
    result = []
    for p in raw_props:
        if p["name"] in DEFAULT_DROP_PROPERTIES:
            continue
        result.append({"name": p["name"], "type": _compact_types(p["types"])})
    return result


def _property_fingerprint(props: list[dict]) -> frozenset:
    return frozenset((p["name"], p["type"]) for p in props)


def compact_schema(raw: dict | str) -> str:
    if isinstance(raw, str):
        raw = json.loads(raw)

    seen_fingerprints: dict[frozenset, str] = {}
    node_lines: list[str] = []

    for label in raw.get("nodeLabels", []):
        name = label["name"]
        if name.startswith("Source"):
            continue
        props = _filter_properties(label.get("properties", []))
        count_str = _format_count(label["count"])
        fp = _property_fingerprint(props)
        if fp and fp in seen_fingerprints:
            first = seen_fingerprints[fp]
            node_lines.append(f"- `:{name}` ~{count_str} — same properties as `:{first}`")
        else:
            if fp:
                seen_fingerprints[fp] = name
            if props:
                prop_str = ", ".join(f"{p['name']}:{p['type']}" for p in props)
                node_lines.append(f"- `:{name}` ~{count_str} — {prop_str}")
            else:
                node_lines.append(f"- `:{name}` ~{count_str}")

    rel_lines: list[str] = []
    for rel in raw.get("relationships", []):
        start = rel["startNode"]
        end = rel["endNode"]
        if start.startswith("Source") or end.startswith("Source"):
            continue
        props = _filter_properties(rel.get("properties", []))
        count_str = _format_count(rel["count"])
        rel_type = rel["type"]
        if props:
            prop_str = ", ".join(f"{p['name']}:{p['type']}" for p in props)
            pattern = f"`(:{start})-[:{rel_type} {{{prop_str}}}]->(:{end})`"
        else:
            pattern = f"`(:{start})-[:{rel_type}]->(:{end})`"
        rel_lines.append(f"- {pattern} ~{count_str}")

    sections = ["# Compact Neo4j schema", "", "## Node labels"]
    sections.extend(node_lines)
    sections += ["", "## Relationships"]
    sections.extend(rel_lines)

    return "\n".join(sections)
