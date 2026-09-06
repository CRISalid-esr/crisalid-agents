# Topic matching — specification

## 1. Goal

Match Horizon Europe work programme (WP) topics against the researchers of our laboratories, in both directions:

| Use case | Name | Direction | Interface | Trigger |
|---|---|---|---|---|
| UC1 | **TRM** — Topic-to-Researcher Match | for every topic of a cluster, find relevant researchers among the members of our laboratories | none (batch) | script run manually by an administrator, writes JSON, Markdown and PDF files |
| UC2 | **PTM** — Project-to-Topic Match | for a project title or idea, find the best-fitting topics | chat: OpenWebUI (main UI) and chat API | user message |

Both rest on one prerequisite: a local **OpenSearch index of Horizon Europe WP topics** with hybrid (BM25 + dense
vector) search, one document per topic.

Delivery is three commits, one per step: (1) index and ingestion, (2) TRM, (3) PTM. Two branches in other
repositories support steps 1 and 2 (§9).

## 2. Sources

### 2.1 Files

`HORIZON_WP_DIR` (locally `data/cff/he/2026-27/`, git-ignored; downloaded manually on the server) holds the WP
part PDFs **of the current programme only**. When a new programme is published, the directory content is replaced
and the index is rebuilt from it: the index always mirrors the directory (§4.4). Current content — six parts,
1 582 pages, **409 topics**:

| File | Part | Cluster | Pages | Topics | Topic id prefix |
|---|---|---|---|---|---|
| `wp-4-health_…` | 4 | CL1 Health | 211 | 38 | `HORIZON-HLTH-` |
| `wp-5-culture-creativity-and-inclusive-society_…` | 5 | CL2 | 188 | 54 | `HORIZON-CL2-` |
| `wp-6-civil-security-for-society_…` | 6 | CL3 | 174 | 38 | `HORIZON-CL3-` |
| `wp-7-digital-industry-and-space_…` | 7 | CL4 | 314 | 77 | `HORIZON-CL4-` |
| `wp-8-climate-energy-and-mobility_…` | 8 | CL5 | 339 | 94 | `HORIZON-CL5-` |
| `wp-9-food-bioeconomy-natural-resources-agriculture-and-environment_…` | 9 | CL6 | 356 | 108 | `HORIZON-CL6-` |

The parser accepts any part that follows the same layout and derives the cluster from the topic id prefix
(`HLTH` → CL1), falling back to the part number in the file name.

### 2.2 Structure of a WP part (identical across the six files)

- **Running headers and footers** on every page: `Horizon Europe - Work Programme 2026-2027`, the cluster label,
  `Part N - Page p of P`. Footnotes sit at page bottom (a number alone on a line, then text) and may split a
  sentence or a table cell across pages. All are stripped before section parsing.
- **Table of contents** (pages 2–5): `Destination …` and `HORIZON-…: title …… page` lines with dot leaders. The TOC
  is the source of the topic → destination mapping, because destination heading styles differ per part
  (`Destination Innovative Research on …`, `Destination: …`, `Destination - …`, CL5 sub-headings).
- **Call overview tables** (one per call, 55 in total): `Call - <name>` / `HORIZON-CLn-2026-xx` (call id) /
  `Opening: 06 Jan 2026` / `Deadline(s): 21 Apr 2026` or `17 Mar 2026 (First Stage), 13 Oct 2026 (Second Stage)`,
  then rows `TOPIC-ID:` / action code / title / budget / contribution / expected projects. They provide `call_id`,
  `opening_date`, `deadlines[]` and `expected_projects` per topic.
- **Topic block** (409 occurrences), always in this order:
  1. `Proposals are invited against the following topic(s):` (once per group);
  2. `TOPIC-ID: Title` — the title may wrap over several lines; ids may carry suffixes such as `-two-stage`;
  3. `Call: <call name>` (a name, not the id; the id comes from the overview table);
  4. `Specific conditions` two-column table with labels `Expected EU contribution per project`, `Indicative budget`,
     `Type of Action`, and optionally `Technology Readiness Level`, `Admissibility conditions`, `Eligibility
     conditions`, `Award criteria`, `Procedure`, `Legal and financial set-up of the Grant Agreements`. Labels wrap;
     amounts are sentences (`between EUR 5.00 and 6.00 million`, `around EUR 5.00 million`, `The total indicative
     budget for the topic is EUR 12.00 million`);
  5. `Expected Outcome:` paragraphs and bullets, sometimes `Focus 1` / `Focus 2` sub-blocks;
  6. `Scope:` paragraphs and bullets, until the next topic block, `Destination` heading or `Call -` overview.

Non-topic actions (public procurement, expert contracts, grants to identified beneficiaries) carry `Type of
Action:` lines but no `TOPIC-ID:` header and are ignored.

### 2.3 Researchers and publications

The CRISalid knowledge graph, through the MCP Toolbox (`crisalid-ai-skills/mcp-toolbox/tools.yaml`):

- `publications-by-theme(semantic_theme, semantic_theme_vector, limit ≤ 100, use_abstract, internal_only)` —
  vector search over document titles and abstracts; returns `uid, score, titles, abstracts,
  contributors[{uid, name, external, roles}]`. `internal_only` is added by this project (§9): when true, the Cypher
  keeps only documents with at least one contributor whose `external = false`, and returns only those contributors.
- `get-person-memberships(person_uid)` — research unit(s) of a researcher, used in the report.
- `list-person-publications`, `get-publication` — verification and justification.

A **member** of our laboratories is a `Person` with `external = false`: a person created from the institution's
own directory, as opposed to persons auto-created from harvested publications. No further membership or
employment condition applies.

## 3. Components reused from this repository

- `common/embedding.py` — OpenAI-compatible embedding client on the shared service, **bge-m3, 1024 dimensions**.
  OpenSearch vectors and the Neo4j `embeddable_embedding` index therefore share one model, and a query embedded
  once serves both.
- `common/langgraph_agent.py`, `common/registry.py`, adapters — PTM is a normal registered agent. The registry
  requires every package under `agents/` to expose `create_agent()`, so the TRM batch lives outside `agents/`.
- `common/mcp_toolbox_client.py` — TRM calls the toolbox tools programmatically (no LLM tool calling).
- `scripts/create_new_agent.py` — PTM is scaffolded with it (`dummy` template: LangGraph loop with local tools).
- `pypdf` (already in the venv, BSD licence) for PDF text extraction. Its `layout` extraction mode is used for the
  specific-conditions table (keeps the two columns apart), the default mode for prose.

Layout of the new code:

```
common/horizon/                  ← OpenSearch mapping and pipelines, parser, ingestion, search (commit 1)
trm/                             ← batch pipeline, prompts, report rendering (commit 2)
scripts/index_horizon_topics.py
scripts/topic_researcher_match.py
agents/project_topic_matching/   ← PTM chat agent (commit 3)
docker/docker-compose.dev.yaml   ← local OpenSearch
```

## 4. Step 1 — Horizon topic index

### 4.1 OpenSearch

- **OpenSearch ≥ 2.19**, single node, one named volume. In the deployment stack under profile
  **`horizon_europe_index`**; locally through `docker/docker-compose.dev.yaml`. Security plugin disabled locally,
  basic auth in production (`HORIZON_OS_URL`, `HORIZON_OS_USER`, `HORIZON_OS_PASSWORD`).
- Hybrid search relies on the free distribution only: the `k-NN` plugin (`knn_vector`, HNSW, Lucene engine,
  cosine), the `neural-search` plugin (`hybrid` query) and search pipelines combining the sub-query scores with
  reciprocal rank fusion (`score-ranker-processor`, `rank_constant = 60`) or min-max normalisation.
- Python dependency `opensearch-py[async]>=2.8` in a new optional group `topic-matching`, installed by both images:
  the pipelines image (OpenWebUI serves PTM) through an extra `pip install` line in `docker/pipelines.Dockerfile`,
  and the chat-api image (PTM through the API, TRM batch).
- Two search pipelines are created at ingestion time: `horizon-hybrid-rrf` (default) and `horizon-hybrid-minmax`
  (selected by `HORIZON_SEARCH_PIPELINE`).

### 4.2 Index `horizon-topics`

`_id = topic_id` (e.g. `HORIZON-CL2-2026-01-HERITAGE-02`). `index.knn = true`, `english` analyzer on text fields
(topics are English only).

| Field | Type | Source |
|---|---|---|
| `topic_id` | keyword | topic header |
| `call_id` | keyword (`HORIZON-CL2-2026-01`) | call overview table |
| `call_name` | keyword | `Call:` line |
| `cluster` | keyword (`CL1`…`CL6`) | id prefix, else part number |
| `cluster_label` | keyword | cover page |
| `work_programme` | keyword (`2026-2027`) | cover page |
| `part` | integer | file name |
| `destination` | text + keyword | TOC (last `Destination` entry preceding the topic) |
| `title` | text, boosted ×3 in BM25 | topic header |
| `type_of_action` | keyword (`RIA`, `IA`, `CSA`, `COFUND`, …) | overview table code, specific conditions label |
| `stage` | keyword (`single`, `two-stage`) | id suffix / call name |
| `contribution_min_eur`, `contribution_max_eur` | long | "between EUR a and b million" / "around EUR a million" (min = max) |
| `indicative_budget_eur` | long | "total indicative budget … EUR x million" |
| `expected_projects` | integer | overview table |
| `opening_date` | date | overview table |
| `deadlines` | date[] (1 or 2) | overview table |
| `trl` | text | specific conditions, when present |
| `conditions_text` | text, not searched | remaining specific conditions (eligibility, procedure, legal…), for display |
| `expected_outcome` | text | section |
| `scope` | text | section |
| `content` | text | `title + expected_outcome + scope`: the substantive research content, BM25 target |
| `content_vector` | knn_vector, dim 1024, cosine, HNSW | embedding of `content` (whole-topic representation; bge-m3 accepts 8 192 tokens, enough for every topic seen) |
| `passages` | nested `{section: keyword, order: integer, text: text, vector: knn_vector 1024}` | one entry per section-level passage (§4.3); lets a query about one aspect of a topic match that aspect instead of the diluted whole |
| `source_file`, `source_pages` (first–last), `source_sha256`, `parser_version`, `embedding_model`, `run_id`, `indexed_at` | keyword / int / date | provenance, idempotence, stale-document sweep |

### 4.3 Parser (`common/horizon/wp_parser.py`)

Input: one WP part PDF. Output: `list[Topic]` and `list[ParseIssue]`.

Pipeline: pages → strip headers, footers and footnotes → parse cover (WP, part, cluster label) → parse TOC
(destinations, topic ids in order) → parse call overview tables (`call_id`, dates, per-topic action code, budget,
expected projects) → split the body into topic blocks on `TOPIC-ID: title` lines that appear after the TOC and are
followed by a `Call:` line within a few lines → parse the specific-conditions table by its known labels →
`Expected Outcome:` / `Scope:` → build the passages → assemble `Topic`, joined with TOC and overview data.

**Passages.** Each topic yields: one passage per `Expected Outcome` and `Scope` section, one per `Focus n`
sub-block when present, and long sections split at paragraph boundaries into chunks of at most ~1 500 tokens. The
topic title is prepended to every passage text before embedding so a chunk keeps its context. Typical yield: 3–6
passages per topic, about 2 000 in total.

Validation: missing scope or expected outcome, unparseable amount or date → **warning** (topic indexed, field
null); id not matching the part's cluster, duplicate id in one file → **error** (topic skipped). Both are listed in
the report with file and page numbers.

### 4.4 Ingestion script (`scripts/index_horizon_topics.py`)

```
uv run python scripts/index_horizon_topics.py [--wp-dir $HORIZON_WP_DIR] [--file …] [--recreate] [--dry-run] [--report out.json]
```

- **The index mirrors the directory.** Each run has a `run_id`. Topics found in the files are indexed or refreshed
  with it; at the end of a complete run over the whole directory, every document carrying an older `run_id` is
  deleted (`delete_by_query`) and listed in the report as removed. This wipes the previous programme's topics when
  the directory is replaced. `--file` runs and interrupted runs never sweep. `--recreate` drops index and pipelines
  first.
- **Idempotent**: `_id = topic_id`; a topic is re-embedded and re-indexed only if `source_sha256`,
  `parser_version` or `embedding_model` differ from the stored document (checked with `mget` before embedding);
  otherwise only `run_id` is updated by a partial update, without any embedding call.
- **Resumable**: state is the index itself; an interrupted run leaves a consistent index and a rerun completes it.
  A failed embedding marks the topic as failed in the report and does not stop the run.
- **Bulk**: `helpers.async_bulk`, chunk 50 (each document carries one content vector and several passage vectors);
  the content and passage texts of a batch of topics are embedded together, `EMBEDDING_BATCH_SIZE` texts per
  request, with bounded concurrency.
- **Report** (`--report`, plus console summary): files scanned; per-file counts of topics parsed, indexed,
  unchanged, failed and skipped; removed stale documents; warnings and errors with file and page; embedding
  failures; duration; per-cluster totals read back from the index. Exit code 0 only if no error occurred.
- `--dry-run` parses and reports without touching OpenSearch.

### 4.5 Search module (`common/horizon/search.py`)

- `search_topics(query, *, cluster=None, type_of_action=None, top_k=10) -> list[TopicHit]`: one `hybrid` query
  with three sub-queries — `multi_match` on `title^3, content`; `knn` on `content_vector`; nested `knn` on
  `passages.vector` (score mode `max`, `inner_hits` limited to the best passage) — fused by the search pipeline,
  `k = top_k × 4` for both vector sub-queries, filters applied to every sub-query. All indexed topics are searched;
  there is no deadline filter. Each `TopicHit` carries the topic fields, the fused score and the best-matching
  passage (section and text), which PTM uses as the "why it fits" excerpt.
- `search_topics_multi(queries, …)`: runs several queries and fuses their hit lists with a client-side reciprocal
  rank fusion so a topic hit by several queries ranks first; returns per-query ranks for explanation.
- `get_topic(topic_id)`, `list_clusters()` (terms aggregation with labels and counts), `iter_topics(cluster)`.

## 5. Step 2 — TRM batch

```
uv run python scripts/topic_researcher_match.py --list-clusters
uv run python scripts/topic_researcher_match.py --cluster CL2 [--topic HORIZON-CL2-2026-01-HERITAGE-02 …] \
    [--out $TRM_OUTPUT_DIR] [--min-score 0.35] [--max-researchers 15] [--dry-run]
uv run python scripts/topic_researcher_match.py --all      # every cluster
```

Package `trm/`, shipped in the chat-api image, run manually by an administrator (`docker compose exec` or
`docker compose run` on the chat-api service) for one cluster, a few topics or all clusters. A LangGraph
graph (`expand → retrieve → verify → score`) runs once per topic; every LLM call is a single-shot structured prompt
to `TRM_MODEL`, a model dedicated to this batch and configured independently of the chat model. Failures are per
topic and never abort a cluster; `--cluster` and `--topic` allow cheap reruns.

### 5.1 Query generation (`trm/expansion_prompt.md`)

One LLM call per topic, JSON output:

```json
{ "summary": "2–3 sentences on the research content, no policy wording",
  "queries": {
    "topic":      ["<title> — <scope excerpt>"],
    "summary":    ["<summary>"],
    "variations": ["publication-like phrasing 1", "…"]
  },
  "researcher_terms": {
    "en": ["terms a researcher in this field would use in a paper title or abstract", "…"],
    "fr": ["les mêmes, formulés comme dans un titre ou un résumé d'article en français", "…"]
  } }
```

Three query families (the topic itself, its summary, variations) plus researcher-oriented terms in English and
French: publications in the graph are French and English, and French-heavy fields (humanities, law) are better
recalled with French queries. The prompt requires publication-like phrasing, since the WP is written for policy
officers while the graph holds paper titles and abstracts. Total queries capped by `TRM_MAX_QUERIES`.

### 5.2 Retrieval

For each query: embed once, call `publications-by-theme(limit=100, use_abstract=true, internal_only=true)`.
Aggregation over the queries of a topic:

- contributors are members by construction (`internal_only`);
- publication score `s(p)`: reciprocal rank fusion over the per-query rank lists (a paper hit by several queries
  outranks a single high hit), scaled by its best cosine score;
- researcher pre-score `S₀(r) = Σ_{p ∈ top-5(r)} s(p)`: sum of the five best papers, so volume alone does not win;
- the top `TRM_MAX_CANDIDATES` researchers go to verification.

### 5.3 Verification (`trm/verification_prompt.md`)

One LLM call per candidate, with the topic summary and expected outcomes and the candidate's matched papers
(title, year, abstract if present, at most 8). JSON output:

```json
{ "publications": [{"uid": "…", "relevant": true, "reason": "…"}],
  "overall": "strong | plausible | weak | none", "justification": "2 sentences" }
```

A candidate is retained if at least one paper is relevant and `overall ≠ none`. Calls run `TRM_CONCURRENCY` at a
time with retries.

### 5.4 Scoring and report

- `score(r) = Σ_{relevant p} s(p) × recency(p)`, with `recency = 1.0` (≤ 5 years), `0.6` (5–10 years), `0.3`
  (older); normalised to [0, 1] by the topic's best score so `--min-score` means the same across topics. The
  `overall` verdict is displayed next to the score, not folded into it.
- For retained researchers, `get-person-memberships` supplies the research unit(s) shown in the report.
- Per topic: ranked researchers above the threshold, at most `--max-researchers`, each with unit(s), relevant
  papers, justification, and a **pair id** `<topic_id>|<person_uid>`.
- Per cluster, in `--out`: `trm-<cluster>-<date>.json` (source of truth, including run parameters, model names,
  timings and the per-topic error list), `trm-<cluster>-<date>.md` and `trm-<cluster>-<date>.pdf`.
- PDF rendering: Jinja2 → HTML → WeasyPrint (Pango/Cairo packages added to `chat-api.Dockerfile`); the Markdown
  file is rendered from the same template data. Files are collected manually on the server.

### 5.5 Feedback loop

Reviewers record verdicts in a CSV with columns `pair_id, verdict (accept|reject), comment`. A later commit reads
it to compute precision per cluster and per score band, so `TRM_MIN_SCORE` is tuned on evidence, and to inject a
few accepted and rejected examples as few-shot guidance in the verification prompt. The pair id format is fixed from
the first TRM release so early reports stay usable.

### 5.6 Cost envelope

Per cluster of ~70 topics: ~70 expansion calls and ~70 × 25 verification calls, about 1 800 LLM calls, ~700
embeddings and ~700 toolbox queries. All six clusters: ~10 000 LLM calls per full run, a few euros with a
mid-size model and one to two hours at concurrency 4.

## 6. Step 3 — PTM chat agent

Scaffolded with:

```
uv run python scripts/create_new_agent.py project_topic_matching --template dummy \
    --display-name "Horizon topic finder" \
    --description "Finds the Horizon Europe work programme topics that best fit a project idea."
```

which creates `agents/project_topic_matching/{agent.py, system_prompt.md, README.md}`, the OpenWebUI stub and
`tests/test_project_topic_matching.py`. The placeholder `count_words` tool is replaced by:

- `search_horizon_topics(query, cluster=None, type_of_action=None, top_k=10)` — the hybrid search of §4.5; returns
  id, title, call, destination, type of action, deadlines, budget and the best-matching passage.
- `get_horizon_topic(topic_id)` — full expected outcome and scope, for follow-up questions.
- `list_horizon_clusters()` — so the user can narrow the search.

Flow, driven by `system_prompt.md`:

1. The user submits a project title, optionally an abstract, in French or English.
2. The agent generates 3–5 English queries following the same expansion instructions as TRM (shared text embedded
   in the prompt; topics are English, so no French queries here) and issues them as successive
   `search_horizon_topics` calls, visible as OpenWebUI tool blocks like those of the generic agent.
3. The agent merges the results.
4. Presentation: at most five topics, each with id, title, call and deadline(s), and one sentence on why it fits
   drawn from the best-matching passage; an explicit caveat when scores are low; follow-ups answered from
   `get_horizon_topic`. The answer language follows the user's.

The agent depends on `common/horizon` only. Tests use `ScriptedChatModel` and a fake search function injected
through `create_agent(llm=…, search=…)`.

## 7. Configuration

| Variable | Purpose | Default |
|---|---|---|
| `HORIZON_WP_DIR` | directory of the current programme's WP part PDFs | `data/cff/he/2026-27` |
| `HORIZON_OS_URL` | OpenSearch URL | `http://localhost:9200` |
| `HORIZON_OS_USER`, `HORIZON_OS_PASSWORD` | OpenSearch basic auth (optional) | — |
| `HORIZON_OS_INDEX` | index name | `horizon-topics` |
| `HORIZON_SEARCH_PIPELINE` | `horizon-hybrid-rrf` or `horizon-hybrid-minmax` | `horizon-hybrid-rrf` |
| `EMBEDDING_*` | existing embedding service; `EMBEDDING_DIMENSIONS=1024` drives the mapping | existing |
| `TRM_MODEL` | model for TRM expansion and verification calls | required by the TRM script |
| `TRM_MAX_QUERIES`, `TRM_MAX_CANDIDATES`, `TRM_MIN_SCORE`, `TRM_MAX_RESEARCHERS`, `TRM_CONCURRENCY` | TRM tuning | 10 / 25 / 0.35 / 15 / 4 |
| `TRM_OUTPUT_DIR` | report directory | `./reports` |

## 8. Tests (offline, as the rest of the repository)

- Parser: fixtures under `tests/fixtures/horizon/` — extracted text of two topics per part style (CL2, HLTH, CL4
  two-stage), a TOC and an overview table excerpt; asserts ids, wrapped titles, call ids, destinations, amounts,
  dates, sections, passage splitting, and that TOC and overview occurrences are not taken as topic blocks. One
  opt-in test (`--run-pdf`) parses the real PDFs when present and checks the total of 409 topics.
- Ingestion: fake OpenSearch client (in-memory) — idempotence (a second run embeds nothing), resumability
  (interruption after N documents, rerun completes), stale sweep (a document from an earlier run disappears only
  after a complete run), report contents.
- Search: client-side multi-query fusion unit-tested on synthetic rank lists; the hybrid query body snapshot-tested.
- TRM: `ScriptedChatModel` for expansion and verification, fake toolbox client with canned `publications-by-theme`
  answers — scoring order, thresholds, pair ids, Markdown rendering; PDF rendering smoke-tested when WeasyPrint is
  importable.
- PTM: same pattern as `tests/test_dummy_agent.py` with a fake search function.
- An `opensearch` pytest marker for optional integration tests against a live local instance, skipped by default.

## 9. Branches and commits

This repository, branch `topic-matching`:

0. `docs: topic matching spec` — this file and the `.gitignore` update
1. `feat(horizon): WP topic index — parser, OpenSearch mapping and pipelines, idempotent bulk ingestion, hybrid search`
   — includes the dev compose file, the `topic-matching` dependency group, `.env.sample` and README updates
2. `feat(trm): topic-to-researcher matching batch with JSON/Markdown/PDF report`
3. `feat(project_topic_matching): Horizon topic finder chat agent`

`crisalid-deployment`, branch `horizon-europe-index`: OpenSearch service under profile `horizon_europe_index`,
its volume and environment. Paired with commit 1.

`crisalid-ai-skills`, branch `publications-by-theme-internal-only`: `internal_only` boolean on
`publications-by-theme` in `tools.yaml` and `tools-auth.yaml`, applied both to the document filter and to the
returned contributors, with a test. Paired with commit 2.
