"""Parser for Horizon Europe work programme (WP) part PDFs.

A WP part (one PDF per cluster, e.g. "wp-5-culture-creativity-and-inclusive-society_horizon-2026-2027_en.pdf")
is turned into one ``Topic`` per call topic. The layout is the same for every part:

- running headers ("Horizon Europe - Work Programme 2026-2027", the cluster label, "Part N - Page p of P") on
  every page and footnotes at the bottom of pages (continuous numbering over the whole document);
- a table of contents whose entries end with dot leaders and a page number;
- one overview table per call ("Call - <name>", the call id, "Opening:", "Deadline(s):", then one row per topic
  with its type of action, budget, expected EU contribution per project and number of projects);
- the topic blocks themselves: "TOPIC-ID: Title", "Call: <name>", the "Specific conditions" table, then the
  "Expected Outcome:" and "Scope:" sections.

``parse_pages`` works on the list of page texts (as produced by ``pypdf``) so tests and tools can feed text
directly; ``parse_pdf`` reads the PDF.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from common.horizon.models import ParseIssue, ParseResult, Passage, Topic

PARSER_VERSION = "1"

TOPIC_ID_RE = re.compile(r"^(HORIZON-[A-Z0-9]+(?:-[A-Za-z0-9]+)+):\s*(.*)$")
CALL_ID_RE = re.compile(r"^(HORIZON-[A-Z0-9]+(?:-[A-Za-z0-9]+)*)\s*$")
HEADER_RES = (
    re.compile(r"^Horizon Europe - Work Programme \d{4}\s*-\s*\d{4}\s*$"),
    re.compile(r"^Part \d+ - Page \d+ of \d+\s*$"),
)
# Some pypdf versions glue the first body line to the page-number line.
PAGE_LINE_PREFIX_RE = re.compile(r"^Part \d+ - Page \d+ of \d+\s+(?=\S)")
TOC_ENTRY_RE = re.compile(r"^(?P<text>.*?)\s*\.{2,}(?:\s+\.{2,})*\s*(?P<page>\d+)\s*$")
COVER_PART_RE = re.compile(r"^(\d{1,2})\.\s+(.+?)\s*$")
COVER_WP_RE = re.compile(r"Work Programme (\d{4})\s*-\s*(\d{4})")
DATE_RE = re.compile(r"\d{1,2} [A-Z][a-z]{2} \d{4}")
ACTION_CODE_RE = re.compile(r"\b(RIA|IA|CSA|COFUND|PCP|PPI)\b")
MAX_EXPECTED_PROJECTS = 30
EXPECTED_OUTCOME_RE = re.compile(r"^Expected [Oo]utcomes?\s*:?\s*(.*)$")
SCOPE_RE = re.compile(r"^Scope\s*:?\s*(.*)$")
FOCUS_RE = re.compile(r"^Focus \d+\b")
FOOTNOTE_MARKER_RE = re.compile(r"(?<=[a-z\)\]’”])\d{1,3}(?=[\s,.;:)])")
HYPHEN_SPACE_RE = re.compile(r"(\w) -(\w|,)")
HYPHEN_WRAP_RE = re.compile(r"(\w)- (?=[a-z])")
TRAILING_HYPHEN_RE = re.compile(r"(\w) -$")
BETWEEN_RE = re.compile(r"between EUR\s*(\d+(?:\.\d+)?)\s*and\s*(\d+(?:\.\d+)?)\s*million", re.I)
AROUND_RE = re.compile(r"(?:around|of)\s+EUR\s*(\d+(?:\.\d+)?)\s*million", re.I)
UP_TO_RE = re.compile(r"up to EUR\s*(\d+(?:\.\d+)?)\s*million", re.I)
LABEL_FRAGMENTS_RE = re.compile(r"\s*(?:Expected EU\s+)?contribution per\s+project\s*|\s*Indicative\s+budget\s*")
INDICATIVE_RE = re.compile(r"total indicative budget for the topic is EUR\s*(\d+(?:\.\d+)?)\s*million", re.I)
TRL_RE = re.compile(r"(Activities are expected to (?:start|achieve) .*?TRL\s*\d+(?:\s*-\s*\d+)?[^.]*)", re.I)
CONDITION_LABELS = (
    "Expected EU contribution per project",
    "Indicative budget",
    "Type of Action",
    "Technology Readiness Level",
    "Admissibility conditions",
    "Eligibility conditions",
    "Eligibility",
    "Award criteria",
    "Procedure",
    "Legal and financial set-up of the Grant Agreements",
)
ACTION_CODES = {
    "research and innovation actions": "RIA",
    "innovation actions": "IA",
    "coordination and support actions": "CSA",
    "programme co-fund actions": "COFUND",
    "programme co-fund action": "COFUND",
    "pre-commercial procurement": "PCP",
    "public procurement of innovative solutions": "PPI",
}
CLUSTER_BY_PREFIX = {"HLTH": "CL1"}
CLUSTER_BY_PART = {4: "CL1", 5: "CL2", 6: "CL3", 7: "CL4", 8: "CL5", 9: "CL6"}
MAX_PASSAGE_CHARS = 6000  # ≈ 1 500 tokens
FOOTNOTE_TOLERANCE = 6  # skipped footnote numbers tolerated when locating the next block


@dataclass
class _Line:
    page: int
    text: str


@dataclass
class _TocEntry:
    text: str
    page: int
    topic_id: str | None


@dataclass
class _OverviewRow:
    topic_id: str
    call_id: str
    call_name: str
    destination: str | None
    opening_date: date | None
    deadlines: list[date]
    action: str | None = None
    expected_projects: int | None = None


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _clean_text(text: str) -> str:
    text = FOOTNOTE_MARKER_RE.sub("", text)
    text = HYPHEN_SPACE_RE.sub(r"\1-\2", text)
    text = HYPHEN_WRAP_RE.sub(r"\1-", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def _parse_date(text: str) -> date | None:
    try:
        return datetime.strptime(text, "%d %b %Y").date()
    except ValueError:
        return None


def _million(value: str) -> int:
    return int(round(float(value) * 1_000_000))


# --------------------------------------------------------------------------- page cleaning


def _strip_headers(lines: list[str], cluster_label: str | None) -> list[str]:
    kept = []
    for i, line in enumerate(lines):
        if i < 5:
            line = PAGE_LINE_PREFIX_RE.sub("", line)
        text = line.strip()
        if i < 5 and (
            any(r.match(line) for r in HEADER_RES)
            or (cluster_label and len(text) >= 4 and text in cluster_label)
        ):
            continue
        kept.append(line)
    return kept


def _strip_footnotes(lines: list[str], next_footnote: int) -> tuple[list[str], int]:
    """Remove the footnote block at the bottom of a page.

    Footnotes are numbered continuously through the document: the block starts at the last line beginning
    with the next expected footnote number, and every further expected number found in the block advances
    the counter.
    """
    start = first = None
    for candidate in range(next_footnote, next_footnote + FOOTNOTE_TOLERANCE):
        start_re = re.compile(rf"^{candidate}\s+\S")
        found = [i for i, line in enumerate(lines) if start_re.match(line)]
        # Footnotes sit at the bottom of the page: keep the last occurrence, in the lower part of the page.
        if found and found[-1] >= len(lines) * 0.3:
            start, first = found[-1], candidate
            break
    if start is None:
        return lines, next_footnote
    block, body = lines[start:], lines[:start]
    n = first
    while any(re.match(rf"^{n + 1}\s+\S", line) for line in block):
        n += 1
    if body and body[-1].strip() == "":
        body = body[:-1]
    return body, n + 1


def _clean_pages(pages: list[str], cluster_label: str | None) -> list[_Line]:
    lines: list[_Line] = []
    next_footnote = 1
    for number, text in enumerate(pages, start=1):
        # Depending on the pypdf version, a space is inserted before hyphens ("HORIZON -CL2", "2026 -2027"):
        # normalise before any pattern matching.
        raw = [TRAILING_HYPHEN_RE.sub(r"\1-", HYPHEN_SPACE_RE.sub(r"\1-\2", line.rstrip())) for line in text.split("\n")]
        raw = _strip_headers(raw, cluster_label)
        raw, next_footnote = _strip_footnotes(raw, next_footnote)
        lines.extend(_Line(number, line) for line in raw if line.strip())
    return _merge_wrapped_ids(lines)


# A wrapped id ends with "-"; the overview cells may follow on the same line, sometimes glued to it
# ("…-two-IA 10.00 Around 2" once the space before the hyphen has been normalised).
WRAPPED_ID_RE = re.compile(
    r"^(HORIZON-[A-Za-z0-9-]+?-)(?:\s*((?:RIA|IA|CSA|COFUND|PCP|PPI)\s+\d+\.\d{2}.*))?$"
)
ID_TAIL_RE = re.compile(r"^([A-Za-z0-9-]+:)(.*)$")


def _merge_wrapped_ids(lines: list[_Line]) -> list[_Line]:
    # In the narrow overview-table column a long topic id wraps ("HORIZON-HLTH-2026-02-" / "DISEASE-12: …").
    merged: list[_Line] = []
    skip = False
    for i, line in enumerate(lines):
        if skip:
            skip = False
            continue
        text = line.text.strip()
        head = WRAPPED_ID_RE.match(text)
        tail = ID_TAIL_RE.match(lines[i + 1].text.strip()) if head and i + 1 < len(lines) else None
        if head and tail:
            rest = " ".join(part.strip() for part in (tail.group(2), head.group(2) or "") if part and part.strip())
            merged.append(_Line(line.page, f"{head.group(1)}{tail.group(1)} {rest}".strip()))
            skip = True
        else:
            merged.append(line)
    return merged


# --------------------------------------------------------------------------- cover and TOC


def _parse_cover(page: str) -> tuple[int | None, str | None, str | None]:
    part = label = work_programme = None
    lines = [HYPHEN_SPACE_RE.sub(r"\1-\2", line.strip()) for line in page.split("\n")]
    for i, line in enumerate(lines):
        if (m := COVER_WP_RE.search(line)) and work_programme is None:
            work_programme = f"{m.group(1)}-{m.group(2)}"
        if (m := COVER_PART_RE.match(line)) and part is None and not line.startswith("("):
            part, label = int(m.group(1)), m.group(2)
            # The label may wrap: continuation lines follow until a blank line or the decision reference.
            for extra in lines[i + 1:]:
                if not extra or extra.startswith("("):
                    break
                label = f"{label} {extra}"
    return part, label, work_programme


def _toc_pages(pages: list[str]) -> tuple[int, int]:
    """Return the (first, last) 1-based page numbers of the table of contents."""
    first = next((i for i, p in enumerate(pages, start=1) if "Table of contents" in p), None)
    if first is None:
        return 0, 0
    last = first
    for i in range(first + 1, len(pages) + 1):
        if any(TOC_ENTRY_RE.match(line.rstrip()) for line in pages[i - 1].split("\n")):
            last = i
        else:
            break
    return first, last


def _parse_toc(lines: list[_Line], first: int, last: int) -> list[_TocEntry]:
    entries: list[_TocEntry] = []
    pending: list[str] = []
    for line in lines:
        if line.page < first or line.page > last:
            continue
        if "Table of contents" in line.text:
            continue
        m = TOC_ENTRY_RE.match(line.text)
        if m:
            text = " ".join(pending + [m.group("text")]).strip()
            pending = []
            if not text:
                continue
            topic = TOPIC_ID_RE.match(text)
            entries.append(_TocEntry(text=text, page=int(m.group("page")), topic_id=topic.group(1) if topic else None))
        else:
            pending.append(line.text.strip())
    return entries


def _toc_destinations(entries: list[_TocEntry]) -> dict[str, str]:
    """Map topic id → destination from the TOC entries.

    Parts that name their destinations "Destination …" use those entries; parts that do not (CL5) use every
    non-topic entry after the "Destinations" section entry.
    """
    start = next((i for i, e in enumerate(entries) if _norm(e.text) == "destinations"), None)
    scoped = entries[start + 1:] if start is not None else entries
    named = [e for e in scoped if e.topic_id is None and _norm(e.text).startswith("destination")]
    use_named = bool(named)
    result: dict[str, str] = {}
    current: str | None = None
    for entry in scoped:
        if entry.topic_id:
            if current:
                result[entry.topic_id] = current
        elif not use_named or _norm(entry.text).startswith("destination"):
            if _norm(entry.text) in ("other actions", "budget") or _norm(entry.text).startswith("other actions"):
                current = None
            else:
                current = _clean_text(entry.text)
    return result


# --------------------------------------------------------------------------- call overview tables


def _parse_overview_cells(text: str) -> tuple[str | None, int | None]:
    """Extract the type of action and the expected number of projects from an overview row.

    The cells of a row are interleaved with the wrapped title by the text extraction, so the row is read as a
    token stream: the action code, then the first small integer after it (budgets are decimals, footnote
    markers are larger numbers).
    """
    m = ACTION_CODE_RE.search(text)
    if not m:
        return None, None
    projects = None
    for token in text[m.end():].split():
        if re.fullmatch(r"\d{1,2}", token) and int(token) <= MAX_EXPECTED_PROJECTS:
            projects = int(token)
            break
    return m.group(1), projects


def _parse_overviews(lines: list[_Line], body_start: int) -> tuple[dict[str, _OverviewRow], list[ParseIssue]]:
    rows: dict[str, _OverviewRow] = {}
    issues: list[ParseIssue] = []
    call_id = call_name = destination = None
    opening: date | None = None
    deadlines: list[date] = []
    row: _OverviewRow | None = None
    row_text: list[str] = []
    destination_open = False  # a "Destination …" row may wrap over the next line(s)

    def flush(page: int) -> None:
        nonlocal row, row_text
        if row is None:
            return
        joined = " ".join(row_text).strip()
        action, projects = _parse_overview_cells(joined)
        if action is None:
            issues.append(ParseIssue("warning", f"Unparsed overview row: {joined[:80]!r}", "", page, row.topic_id))
        row.action, row.expected_projects = action, projects
        rows.setdefault(row.topic_id, row)
        row, row_text = None, []

    it: Iterator[_Line] = iter(lines[body_start:])
    for line in it:
        text = line.text.strip()
        if text.startswith("Proposals are invited against the following topic"):
            # First topic block: the overview tables are over.
            flush(line.page)
            break
        if text.startswith("Call - "):
            flush(line.page)
            call_name = text[len("Call - "):].strip()
            call_id = None
            destination = None
            continue
        if call_name and call_id is None and CALL_ID_RE.match(text):
            call_id = text
            continue
        if text.startswith("Opening:"):
            flush(line.page)
            opening = next((d for d in (_parse_date(x) for x in DATE_RE.findall(text)) if d), None)
            continue
        if text.startswith("Deadline(s):"):
            flush(line.page)
            deadlines = [d for d in (_parse_date(x) for x in DATE_RE.findall(text)) if d]
            continue
        if text.startswith("Destination") and call_id:
            flush(line.page)
            destination = _clean_text(text)
            destination_open = True
            continue
        m = TOPIC_ID_RE.match(text)
        if destination_open and not m and destination:
            destination = _clean_text(f"{destination} {text}")
            continue
        destination_open = False
        if m and call_id:
            flush(line.page)
            row = _OverviewRow(m.group(1), call_id, call_name or "", destination, opening, list(deadlines))
            row_text = [m.group(2)]
            continue
        if row is not None:
            row_text.append(text)
    flush(lines[-1].page if lines else 0)
    return rows, issues


# --------------------------------------------------------------------------- topic blocks


def _is_topic_header(lines: list[_Line], index: int) -> bool:
    if not TOPIC_ID_RE.match(lines[index].text.strip()):
        return False
    return any(l.text.strip().startswith("Call:") for l in lines[index + 1:index + 8])


TABLE_LABELS = {_norm(label) for label in CONDITION_LABELS} | {"budget", "specific conditions", "eligibility conditions"}


def _heading_matcher(entries: list[_TocEntry]) -> "callable":
    """Return a predicate telling whether a body line starts a TOC heading that ends the current topic.

    Only the entries of the destinations part of the TOC count (the introduction has headings such as
    "Specific conditions for multi-actor projects" that also appear as table labels inside topics).
    """
    start = next((i for i, e in enumerate(entries) if _norm(e.text) == "destinations"), 0)
    headings = {
        _norm(e.text)
        for e in entries[start:]
        if e.topic_id is None and len(_norm(e.text)) >= 6 and _norm(e.text) not in TABLE_LABELS
    }

    def is_heading(line: str) -> bool:
        n = _norm(line)
        if len(n) < 6 or n in TABLE_LABELS:
            return False
        if n in headings:
            return True
        return len(n) >= 15 and any(h.startswith(n) for h in headings)

    return is_heading


def _to_paragraphs(lines: list[str]) -> str:
    paragraphs: list[str] = []
    current: list[str] = []
    for line in lines:
        text = line.strip()
        if not text:
            continue
        if text.startswith("•") or FOCUS_RE.match(text):
            if current:
                paragraphs.append(" ".join(current))
            current = [text]
        else:
            current.append(text)
    if current:
        paragraphs.append(" ".join(current))
    return "\n".join(_clean_text(p) for p in paragraphs)


def _chunk(text: str, limit: int = MAX_PASSAGE_CHARS) -> list[str]:
    """Split a section into chunks of at most ``limit`` characters at Focus and paragraph boundaries."""
    if len(text) <= limit and not any(FOCUS_RE.match(p) for p in text.split("\n")[1:]):
        return [text] if text else []
    blocks: list[list[str]] = [[]]
    for paragraph in text.split("\n"):
        if FOCUS_RE.match(paragraph) and blocks[-1]:
            blocks.append([])
        blocks[-1].append(paragraph)
    chunks: list[str] = []
    for block in blocks:
        current = ""
        for paragraph in _split_long_paragraphs(block, limit):
            candidate = f"{current}\n{paragraph}" if current else paragraph
            if len(candidate) > limit and current:
                chunks.append(current)
                current = paragraph
            else:
                current = candidate
        if current:
            chunks.append(current)
    return chunks


SENTENCE_END_RE = re.compile(r"(?<=[.;:!?])\s+(?=[A-Z•(])")


def _split_long_paragraphs(paragraphs: list[str], limit: int) -> list[str]:
    result: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph) <= limit:
            result.append(paragraph)
            continue
        current = ""
        for sentence in SENTENCE_END_RE.split(paragraph):
            candidate = f"{current} {sentence}" if current else sentence
            if len(candidate) > limit and current:
                result.append(current)
                current = sentence
            else:
                current = candidate
        if current:
            result.append(current)
    return result


def _passages(topic_title: str, expected_outcome: str, scope: str) -> list[Passage]:
    passages: list[Passage] = []
    order = 0
    for section, text in (("expected_outcome", expected_outcome), ("scope", scope)):
        for chunk in _chunk(text):
            passages.append(Passage(section=section, order=order, text=chunk))
            order += 1
    return passages


def _parse_conditions(lines: list[str]) -> dict:
    joined = _clean_text(" ".join(l.strip() for l in lines))
    result: dict = {
        "conditions_text": joined,
        "type_of_action_label": None,
        "contribution_min_eur": None,
        "contribution_max_eur": None,
        "indicative_budget_eur": None,
        "trl": None,
    }
    for i, line in enumerate(lines):
        text = line.strip()
        if text.startswith("Type of Action"):
            value = text[len("Type of Action"):].strip(" :")
            if not value and i + 1 < len(lines):
                value = lines[i + 1].strip()
            result["type_of_action_label"] = _clean_text(value) or None
            break
    # The wrapped label of the first table row ("Expected EU / contribution per / project") is interleaved
    # with the value cell by the text extraction; drop its fragments before reading the amounts.
    values = LABEL_FRAGMENTS_RE.sub(" ", joined)
    if m := BETWEEN_RE.search(values):
        result["contribution_min_eur"], result["contribution_max_eur"] = _million(m.group(1)), _million(m.group(2))
    elif m := AROUND_RE.search(values):
        result["contribution_min_eur"] = result["contribution_max_eur"] = _million(m.group(1))
    elif m := UP_TO_RE.search(values):
        result["contribution_max_eur"] = _million(m.group(1))
    if m := INDICATIVE_RE.search(values):
        result["indicative_budget_eur"] = _million(m.group(1))
    if m := TRL_RE.search(values):
        result["trl"] = m.group(1).strip()
    return result


def _split_block(block: list[_Line]) -> tuple[list[str], list[str], list[str], list[str]]:
    """Split a topic block (after the Call: line) into conditions, expected outcome and scope lines."""
    conditions: list[str] = []
    outcome: list[str] = []
    scope: list[str] = []
    target = conditions
    for line in block:
        text = line.text.strip()
        if target is conditions and (m := EXPECTED_OUTCOME_RE.match(text)):
            target = outcome
            if m.group(1):
                target.append(m.group(1))
            continue
        if target is not scope and (m := SCOPE_RE.match(text)) and (target is outcome or not conditions):
            target = scope
            if m.group(1):
                target.append(m.group(1))
            continue
        target.append(text)
    return conditions, outcome, scope, [l.text for l in block]


def _parse_topics(
    lines: list[_Line],
    body_start: int,
    is_heading,
    meta: dict,
    overview: dict[str, _OverviewRow],
    toc_destinations: dict[str, str],
    source_file: str,
) -> tuple[list[Topic], list[ParseIssue]]:
    topics: list[Topic] = []
    issues: list[ParseIssue] = []
    seen: set[str] = set()
    i = body_start
    n = len(lines)
    while i < n:
        if not _is_topic_header(lines, i):
            i += 1
            continue
        header = TOPIC_ID_RE.match(lines[i].text.strip())
        topic_id = header.group(1)
        first_page = lines[i].page
        title_parts = [header.group(2)]
        j = i + 1
        while j < n and not lines[j].text.strip().startswith("Call:"):
            title_parts.append(lines[j].text.strip())
            j += 1
        call_name = lines[j].text.strip()[len("Call:"):].strip() if j < n else None
        j += 1
        block_start = j
        in_sections = False
        while j < n and not _is_topic_header(lines, j):
            text = lines[j].text.strip()
            if EXPECTED_OUTCOME_RE.match(text) or SCOPE_RE.match(text):
                in_sections = True
            elif in_sections and is_heading(text):
                break
            j += 1
        block = lines[block_start:j]
        last_page = block[-1].page if block else first_page
        i = j

        if topic_id in seen:
            issues.append(ParseIssue("error", "Duplicate topic id in file", source_file, first_page, topic_id))
            continue
        seen.add(topic_id)

        cluster = _cluster_of(topic_id, meta["part"])
        if meta["cluster"] and cluster != meta["cluster"]:
            issues.append(
                ParseIssue("error", f"Topic cluster {cluster} does not match file cluster {meta['cluster']}",
                           source_file, first_page, topic_id)
            )
            continue

        conditions, outcome_lines, scope_lines, _ = _split_block(block)
        expected_outcome = _to_paragraphs(outcome_lines)
        scope = _to_paragraphs(scope_lines)
        if not expected_outcome:
            issues.append(ParseIssue("warning", "No Expected Outcome section", source_file, first_page, topic_id))
        if not scope:
            issues.append(ParseIssue("warning", "No Scope section", source_file, first_page, topic_id))
        parsed = _parse_conditions(conditions)
        row = overview.get(topic_id)
        if row is None:
            issues.append(ParseIssue("warning", "Topic absent from call overview tables", source_file, first_page, topic_id))
        if parsed["contribution_max_eur"] is None:
            issues.append(ParseIssue("warning", "No EU contribution amount found", source_file, first_page, topic_id))
        if parsed["indicative_budget_eur"] is None:
            issues.append(ParseIssue("warning", "No indicative budget found", source_file, first_page, topic_id))

        title = _clean_text(" ".join(title_parts))
        label = parsed["type_of_action_label"]
        code = (row.action if row else None) or (ACTION_CODES.get(_norm(label)) if label else None)
        topic = Topic(
            topic_id=topic_id,
            title=title,
            cluster=cluster,
            cluster_label=meta["cluster_label"] or "",
            work_programme=meta["work_programme"] or "",
            part=meta["part"] or 0,
            source_file=source_file,
            source_pages=(first_page, last_page),
            call_id=row.call_id if row else None,
            call_name=call_name,
            destination=(row.destination if row and row.destination else None) or toc_destinations.get(topic_id),
            type_of_action=code,
            type_of_action_label=label,
            stage="two-stage" if "two-stage" in topic_id.lower() or "two-stage" in (call_name or "").lower() else "single",
            contribution_min_eur=parsed["contribution_min_eur"],
            contribution_max_eur=parsed["contribution_max_eur"],
            indicative_budget_eur=parsed["indicative_budget_eur"],
            expected_projects=row.expected_projects if row else None,
            opening_date=row.opening_date if row else None,
            deadlines=list(row.deadlines) if row else [],
            trl=parsed["trl"],
            conditions_text=parsed["conditions_text"],
            expected_outcome=expected_outcome,
            scope=scope,
        )
        topic.passages = _passages(title, expected_outcome, scope)
        if topic.destination is None and topics and topics[-1].destination:
            topic.destination = topics[-1].destination
        if topic.destination is None:
            issues.append(ParseIssue("warning", "No destination found", source_file, first_page, topic_id))
        topics.append(topic)
    return topics, issues


def _cluster_of(topic_id: str, part: int | None) -> str:
    prefix = topic_id.split("-")[1]
    if prefix in CLUSTER_BY_PREFIX:
        return CLUSTER_BY_PREFIX[prefix]
    if re.fullmatch(r"CL\d", prefix):
        return prefix
    return CLUSTER_BY_PART.get(part or 0, prefix)


# --------------------------------------------------------------------------- entry points


def parse_pages(pages: list[str], source_file: str) -> ParseResult:
    part, cluster_label, work_programme = _parse_cover(pages[0]) if pages else (None, None, None)
    if part is None:
        if m := re.match(r"wp-(\d+)-", Path(source_file).name):
            part = int(m.group(1))
    cluster = CLUSTER_BY_PART.get(part or 0)
    meta = {"part": part, "cluster": cluster, "cluster_label": cluster_label, "work_programme": work_programme}
    issues: list[ParseIssue] = []
    if part is None or cluster_label is None:
        issues.append(ParseIssue("warning", "Cover page not recognised (part / cluster label)", source_file, 1))

    lines = _clean_pages(pages, cluster_label)
    toc_first, toc_last = _toc_pages(pages)
    entries = _parse_toc(lines, toc_first, toc_last) if toc_first else []
    if not entries:
        issues.append(ParseIssue("warning", "No table of contents found", source_file, toc_first or None))
    body_start = next((i for i, l in enumerate(lines) if l.page > toc_last), len(lines))

    overview, overview_issues = _parse_overviews(lines, body_start)
    for issue in overview_issues:
        issue.source_file = source_file
    issues.extend(overview_issues)

    topics, topic_issues = _parse_topics(
        lines, body_start, _heading_matcher(entries), meta, overview, _toc_destinations(entries), source_file
    )
    issues.extend(topic_issues)
    return ParseResult(
        source_file=source_file,
        part=part,
        cluster=cluster,
        cluster_label=cluster_label,
        work_programme=work_programme,
        topics=topics,
        issues=issues,
    )


def read_pdf_pages(path: Path) -> list[str]:
    from pypdf import PdfReader  # imported lazily: the search path does not need pypdf

    reader = PdfReader(str(path))
    return [page.extract_text() or "" for page in reader.pages]


def parse_pdf(path: Path) -> ParseResult:
    return parse_pages(read_pdf_pages(path), path.name)
