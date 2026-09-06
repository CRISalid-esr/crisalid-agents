# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies (run both — uv sync resets the venv and wipes the pipelines extras)
uv sync --extra chat-api --extra topic-matching --extra trm
uv pip install -r .openwebui-pipelines/requirements.txt

# Tests (offline: agents are driven by a scripted fake chat model)
uv run pytest

# Scaffold a new agent (package + OpenWebUI stub + smoke test)
uv run python scripts/create_new_agent.py <name> [--template dummy|mcp-toolbox] [--display-name ...] [--description ...]

# Run Pipelines server (production)
.openwebui-pipelines/start.sh

# Debug Pipelines server in IDE (PyCharm)
uv run python scripts/debug_openwebui_pipelines.py

# Horizon topic index: local OpenSearch, then parse-only check and ingestion of HORIZON_WP_DIR
docker compose -f docker/docker-compose.dev.yaml up -d
uv run python scripts/index_horizon_topics.py --dry-run --issues
uv run python scripts/index_horizon_topics.py --recreate

# Topic-to-Researcher Match batch (needs TRM_MODEL, the topic index, the embedding service and the MCP toolbox)
uv run python scripts/topic_researcher_match.py --list-clusters
uv run python scripts/topic_researcher_match.py --cluster CL2 [--topic ID] [--out DIR]

# Run chat API (MUI X Chat NDJSON streaming endpoint)
uv run uvicorn chat_api.main:app --port 9100 --reload

# Debug chat API in IDE (PyCharm)
uv run python scripts/debug_chat_api.py

# Build deployment images (one per adapter, from the repo root)
docker build -f docker/pipelines.Dockerfile -t crisalid-agents-owui .
docker build -f docker/chat-api.Dockerfile -t crisalid-agents-chat-api .
```

**After any `uv add`, `uv remove`, or `uv sync`, re-run the `uv pip install` line** — `uv sync` resets the venv to exactly `pyproject.toml` and removes the OpenWebUI Pipelines extras installed separately.

There is no linter configured.

## Architecture

LangChain/LangGraph agents for the CRISalid ecosystem, exposed through two generic adapters. Agents contain only
LangGraph code and business logic; adapters, streaming, discovery and Docker packaging are shared.

```
common/                ← framework: agent contract, LangGraph/MCP Toolbox base classes, registry, adapters' shared code
common/horizon/        ← Horizon Europe WP topic index: PDF parser, OpenSearch mapping, ingestion, hybrid search
trm/                   ← Topic-to-Researcher Match batch (LangGraph expand → retrieve → verify → score, reports)
agents/<name>/         ← one package per agent (agent.py with create_agent(), system_prompt.md, README.md)
openwebui_pipelines/   ← one two-line stub per agent: Pipeline = make_pipeline("<name>")
chat_api/              ← FastAPI: GET /agents, POST /agents/{name}/chat (MUI X Chat NDJSON, port 9100)
scripts/               ← create_new_agent.py (+ templates/), IDE debug launchers
```

### Agent contract (`common/agent.py`)

`BaseAgent(name, display_name, description)` with `astream(messages) -> AsyncIterator[str | ToolCall | ToolResult]`.
`str` items are answer tokens; `ToolCall` is emitted as soon as the LLM decides a call (before it runs); `ToolResult`
once it completes. `ainvoke` / `invoke` / `stream` (sync bridge for OpenWebUI threads) / `aclose` have defaults.

- `common/langgraph_agent.py` — `LangGraphAgent`: implement `build_graph()` (a `MessagesState` graph with `agent` and
  `tools` nodes); translation of LangGraph stream parts into events is done here. Class attributes tune node names,
  an optional tool-result post-processing node, and argument suffixes hidden from UIs.
- `common/mcp_toolbox_client.py` (toolset loading, optional Keycloak service-account auth), `common/embedding.py`
  (embedding provider) and `common/tool_calls.py` (Mistral raw tool-call recovery) and `common/semantic_params.py` (`semantic_*`
  parameter embedding and vector stripping) are shared, opt-in helpers.
  They hold no graph logic: every agent owns its graph in `agents/<name>/agent.py`.
- `common/registry.py` — discovers `agents/*/agent.py:create_agent()`; `AGENTS` env var restricts the served set.
  Instances are cached per process and closed on shutdown.

### Horizon topic index (`common/horizon/`)

`wp_parser.py` turns a work programme part PDF (pypdf text) into `Topic` objects: cover → TOC (destinations) → call
overview tables (call id, dates, expected projects) → topic blocks (`TOPIC-ID: title`, `Call:`, specific conditions,
Expected Outcome, Scope) with section-level passages. `mapping.py` (index + RRF/min-max search pipelines),
`ingest.py` (`HorizonIngester`: idempotent bulk indexing keyed on file hash / parser version / embedding model, stale
sweep after a complete run) and `search.py` (`HorizonSearch`: hybrid query, multi-query RRF fusion, cluster listing).
Driven by `scripts/index_horizon_topics.py`. Deps in the `topic-matching` extra (`opensearch-py`, `pypdf`);
`docker/docker-compose.dev.yaml` runs a local OpenSearch.

### TRM batch (`trm/`)

Not a chat agent (lives outside `agents/`, no `create_agent`). `pipeline.py` builds a LangGraph graph per run with
four nodes over injected services: `llm.py` (single-shot JSON prompts `expansion_prompt.md` /
`verification_prompt.md`, `StructuredLLM` with JSON retries), `sources.py` (`ToolboxResearcherSource`: programmatic
`publications-by-theme` with `internal_only` — filtered client-side when the toolbox lacks the parameter — and
`get-person-memberships`), `scoring.py` (pure functions: RRF over queries × best cosine, top-5 pre-score, recency,
per-topic normalisation, `pair_id`). `runner.py` iterates the topics of a cluster from the OpenSearch index and
`report.py` renders JSON / Markdown / PDF (Jinja2 templates in `trm/templates/`, WeasyPrint in the `trm` extra).
`TRM_MODEL` is required; every `TRM_*` setting is in `trm/config.py`.

### Agents

- `agents/dummy_agent/` — reference agent: `LangGraphAgent` subclass with one local tool (`count_words`). It is the
  checked-in rendering of the `dummy` scaffold template (a test enforces they stay identical: regenerate with
  `create_new_agent.py dummy_agent --force ...` after editing the template).
- `agents/generic_agent/` — ReAct loop over the `CRISALID_MCP_TOOLBOX_TOOLSET` toolset: LLM chain with retry, raw
  tool-call recovery and `semantic_*` parameter embedding, `agent → tools → postprocess_tools` graph, prompt chosen per
  toolset, schema tool output compacted by `schema_postprocessor.py`. The `mcp-toolbox` scaffold template is a copy of
  this graph with a no-op post-processing hook.

### Adapters

- OpenWebUI: `common/openwebui.py` holds the whole pipeline (message conversion, `<details>` tool blocks with
  truncation, spinner status events). The Pipelines server loads every top-level `*.py` in its pipelines directory,
  which is why the shared code lives in `common/` and `openwebui_pipelines/` only contains stubs. Server on port 9099.
- Chat API: `chat_api/main.py` (routes, auth, lifespan) and `chat_api/streaming.py` (events → NDJSON chunks). Called
  server-to-server by the sovisuplus backend (no CORS). Inbound auth in `chat_api/auth.py`: `x-api-key` header vs the
  comma-separated `API_KEYS` env var, toggled by `ENABLE_API_KEYS`. Its deps (fastapi, uvicorn, python-dotenv) live in
  the `chat-api` optional-dependency group, **not** in core: the pipelines image must not override the fastapi/uvicorn
  versions of its base image.

### Docker

Two images built from the repo root, each shipping every agent: `docker/pipelines.Dockerfile` (base
`ghcr.io/open-webui/pipelines`, copies `common/`, `agents/`, `openwebui_pipelines/*.py`) and
`docker/chat-api.Dockerfile` (base `python:3.11-slim`, copies `common/`, `agents/`, `chat_api/`, installs the
`chat-api` extra). Restrict what a deployment serves with `AGENTS`.

## Environment Variables

| Variable | Purpose |
|---|---|
| `AGENTS` | Comma-separated agent names to serve (default: every package under `agents/`) |
| `MODEL` | Model name (e.g. `mistral-medium-250523`, `gpt-4o-mini`) |
| `API_KEY` | API key for the LLM endpoint |
| `LLM_API_BASE` | Base URL of the OpenAI-compatible endpoint (omit for OpenAI default) |
| `CRISALID_MCP_TOOLBOX_URL` | MCP Toolbox server URL (default for every MCP Toolbox agent) |
| `CRISALID_MCP_TOOLBOX_TOOLSET` | Toolset name to load from MCP Toolbox |
| `<NAME>_MCP_TOOLBOX_URL` / `<NAME>_MCP_TOOLBOX_TOOLSET` | Per-agent overrides for agents generated from the `mcp-toolbox` template |
| `KEYCLOAK_ISSUER` | Keycloak issuer URL (e.g. `https://keycloak.example.com/realms/my-realm`); omit to disable outbound toolbox auth |
| `KEYCLOAK_CLIENT_ID` | Service account client ID (outbound toolbox auth) |
| `KEYCLOAK_CLIENT_SECRET` | Service account client secret (outbound toolbox auth) |
| `KEYCLOAK_SSL_VERIFY` | Set to `false` to skip TLS verification (local dev with self-signed certs) |
| `EMBEDDING_*` | Embedding service used for `semantic_*` tool parameters (see `.env.sample`) |
| `HORIZON_WP_DIR` | Directory of the current programme's work programme PDFs (default `data/cff/he/2026-27`) |
| `HORIZON_OS_URL`, `HORIZON_OS_USER`, `HORIZON_OS_PASSWORD` | OpenSearch endpoint of the Horizon topic index |
| `HORIZON_OS_INDEX`, `HORIZON_SEARCH_PIPELINE` | Index name (`horizon-topics`) and search pipeline (`horizon-hybrid-rrf` / `horizon-hybrid-minmax`) |
| `TRM_MODEL` | Dedicated model of the topic-to-researcher batch (required by `scripts/topic_researcher_match.py`) |
| `TRM_MAX_QUERIES`, `TRM_MAX_CANDIDATES`, `TRM_MIN_SCORE`, `TRM_MAX_RESEARCHERS`, `TRM_CONCURRENCY`, `TRM_OUTPUT_DIR` | TRM tuning (defaults 10 / 25 / 0.35 / 15 / 4 / `reports`) |
| `ENABLE_API_KEYS` | Chat API inbound auth toggle; on unless set to `false` |
| `API_KEYS` | Comma-separated valid values for the chat API `x-api-key` header |

## Conventions

- Do not add file-path comments at the top of source files (e.g. `# some/path/file.py`).
- LangGraph graphs are built manually with `StateGraph` + `ToolNode` — do not use `create_react_agent`.
- New agents are created with `scripts/create_new_agent.py`; they must not import from `openwebui_pipelines/` or
  `chat_api/`, and adapters must not import a specific agent (go through `common.registry`). Batch pipelines that are
  not chat agents (`trm/`) live at the repo root, never under `agents/`.
- Use `uv run pytest`; tests never call a real LLM (see `tests/fake_llm.py`) nor a real OpenSearch (fake client in
  `tests/test_horizon_ingest.py`); `uv run pytest --run-pdf` additionally parses the real PDFs of `HORIZON_WP_DIR`.

## Neo4j / Cypher Reference

Authoritative Cypher queries for the CRISalid graph are at `~/PycharmProjects/crisalid-ikg/app/graph/neo4j/queries`. Test fixture data (Cypher) is at `~/WebstormProjects/crisalid-apollo/tests/data/graph.cypher`.
