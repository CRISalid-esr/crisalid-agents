# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
uv sync --extra chat-api --extra topic-matching --extra trm

# Tests (offline: agents are driven by a scripted fake chat model)
uv run pytest

# Scaffold a new agent (package + smoke test)
uv run python scripts/create_new_agent.py <name> [--template dummy|mcp-toolbox] [--display-name ...] [--description ...]

# Horizon topic index: local OpenSearch, then parse-only check and ingestion of HORIZON_WP_DIR
docker compose -f docker/docker-compose.dev.yaml up -d
uv run python scripts/index_horizon_topics.py --dry-run --issues
uv run python scripts/index_horizon_topics.py --recreate

# Topic-to-Researcher Match batch (needs TRM_MODEL, the topic index, the embedding service and the MCP toolbox)
uv run python scripts/topic_researcher_match.py --list-clusters
uv run python scripts/topic_researcher_match.py --cluster CL2 [--topic ID] [--out DIR]

# Run chat API (MUI X Chat NDJSON routes for sovisuplus, OpenAI-compatible routes for OpenWebUI)
uv run uvicorn chat_api.main:app --port 9100 --reload

# Debug chat API in IDE (PyCharm)
uv run python scripts/debug_chat_api.py

# Build the deployment image (from the repo root)
docker build -f docker/chat-api.Dockerfile -t crisalid-agents-chat-api .
```

There is no linter configured.

## Architecture

LangChain/LangGraph agents for the CRISalid ecosystem, exposed through one FastAPI service with two routers (MUI X
Chat for sovisuplus, OpenAI-compatible for OpenWebUI). Agents contain only LangGraph code and business logic;
routers, streaming, authentication, discovery and Docker packaging are shared.

```
common/                ← framework: agent contract, LangGraph/MCP Toolbox base classes, registry, user context, token exchange
common/horizon/        ← Horizon Europe WP topic index: PDF parser, OpenSearch mapping, ingestion, hybrid search
trm/                   ← Topic-to-Researcher Match batch (LangGraph expand → retrieve → verify → score, reports)
agents/<name>/         ← one package per agent (agent.py with create_agent(), system_prompt.md, README.md)
chat_api/              ← FastAPI (port 9100): auth.py (bearer JWT), allow_list.py, deps.py, main.py (/agents routes),
                         openai_compat.py (/v1/models, /v1/chat/completions), streaming.py (NDJSON chunks)
scripts/               ← create_new_agent.py (+ templates/), IDE debug launchers
```

### Agent contract (`common/agent.py`)

`BaseAgent(name, display_name, description)` with `astream(messages) -> AsyncIterator[str | ToolCall | ToolResult]`.
`str` items are answer tokens; `ToolCall` is emitted as soon as the LLM decides a call (before it runs); `ToolResult`
once it completes. `ainvoke` / `invoke` / `stream` (sync bridge, used by tests) / `aclose` have defaults.

- `common/langgraph_agent.py` — `LangGraphAgent`: implement `build_graph()` (a `MessagesState` graph with `agent` and
  `tools` nodes); translation of LangGraph stream parts into events is done here. Class attributes tune node names,
  an optional tool-result post-processing node, and argument suffixes hidden from UIs.
- `common/mcp_toolbox_client.py` (toolset loading; token provider `delegated` = the signed-in person's exchanged token
  read from `common/user_context.py` at each tool call, or `service_account` = client credentials, batch only),
  `common/token_exchange.py` (RFC 8693 exchange of the user's token for the toolbox audience, cached per token),
  `common/tool_rendering.py` (`<details>` tool blocks and status events for OpenWebUI), `common/embedding.py`
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

- `agents/project_topic_matching/` — Horizon topic finder (PTM): `dummy`-style ReAct loop whose three local tools
  (`search_horizon_topics`, `get_horizon_topic`, `list_horizon_clusters`) wrap `common.horizon.search.HorizonSearch`;
  the OpenSearch client is opened lazily in `build_graph()` and closed in `aclose()`. `create_agent(llm=…, search=…)`
  accepts a fake search for tests. Needs `HORIZON_OS_*` and `EMBEDDING_*` at runtime.

- `agents/sorbobot/` — research expertise over the CRISalid graph (spec in `specs/22-sorbobot-implementation/`):
  `dummy`-style loop whose four local tools (`find_domain_experts`, `get_person_expertise`, `get_researcher_profile`,
  `explore_domains`) run deterministic searches over crisalid-taxi (`taxi.py`) and the `sorbobot` MCP toolset
  (`toolbox.py`, one client per event loop) with LLM judges (`judges.py`) and a pure ranking (`ranking.py`). The LLM
  passes the answer language in every tool call; the `agent` node answers input-control, technical-error and LLM
  failures itself from `locales/<lang>.json` (`i18n.py`). `create_agent(llm=…, settings=…, taxi=…, toolbox=…)` takes
  fakes for tests (`tests/sorbobot_fakes.py`). Needs `CRISALID_TAXI_URL` at runtime.

### Chat API

One FastAPI app (`chat_api/main.py`, lifespan, `/health`, `/agents`, `/agents/{name}/chat` → `streaming.py` NDJSON
chunks for MUI X Chat) including the OpenAI-compatible router `chat_api/openai_compat.py` (`/v1/models`,
`/v1/chat/completions`, SSE `chat.completion.chunk` lines, tool executions as `<details>` blocks plus OpenWebUI status
events). Called server-to-server (sovisuplus backend, OpenWebUI), no CORS.

- Inbound auth (`chat_api/auth.py`): `Authorization: Bearer <Keycloak access token>` verified with the realm JWKS
  (`iss` = `KEYCLOAK_ISSUER`, `aud` ∋ `CHAT_API_AUDIENCE`, `exp`, at least `MIN_TOKEN_TTL_SECONDS` left). Errors are
  `401 {"error": "invalid_token" | "wrong_audience" | "token_expiring"}` (`ApiError` handler). `require_user` on chat
  routes, `optional_user` on listings (the allow list applies only when a token is sent).
- Allow list (`chat_api/allow_list.py`): `AGENT_ALLOW_LIST` = `<client-id>:<agent>,…;…` keyed on the `azp` claim;
  `403 {"error": "agent_not_allowed"}`; empty variable = no restriction (local development).
- Per turn (`chat_api/deps.py`): resolve the agent, check the allow list, exchange the token for the toolbox
  (`toolbox_token_for`, skipped when `KEYCLOAK_CLIENT_ID` is unset), run the agent inside `stream_as_user` /
  `bind_user` so the toolbox client finds the token. Tests sign tokens with a session key (`tests/jwt_fixtures.py`)
  and install fakes with `set_verifier` / `set_allow_list` / `set_exchanger`.
- Its deps (fastapi, uvicorn, python-dotenv) live in the `chat-api` optional-dependency group; `pyjwt[crypto]` and
  `httpx` are core.

### Docker

One image built from the repo root, `docker/chat-api.Dockerfile` (base `python:3.11-slim`, copies `common/`,
`agents/`, `chat_api/`, `trm/`, `scripts/`, installs the `chat-api`, `topic-matching` and `trm` extras; the
entrypoint trusts CA certificates mounted under `/usr/local/share/ca-certificates/`). Restrict what a deployment
serves with `AGENTS` and what each client may call with `AGENT_ALLOW_LIST`.

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
| `KEYCLOAK_ISSUER` | Keycloak issuer URL, public hostname (e.g. `https://keycloak.example.com/realms/my-realm`); required by the chat API (JWKS) |
| `CHAT_API_AUDIENCE` | Expected `aud` of inbound tokens (default `crisalid-agents`) |
| `MIN_TOKEN_TTL_SECONDS` | Minimum remaining life of an inbound token (default `120`) |
| `AGENT_ALLOW_LIST` | Agents each client application (`azp` claim) may call, `client:agent,agent;client:…`; empty = no restriction |
| `KEYCLOAK_CLIENT_ID` / `KEYCLOAK_CLIENT_SECRET` | The `crisalid-agents` confidential client: token exchange for the toolbox (chat) and service account (batch); omit to send no token to the toolbox |
| `MCP_AUDIENCE` | Audience of the exchanged toolbox token (default `crisalid-graph-mcp`) |
| `KEYCLOAK_SSL_VERIFY` | Set to `false` to skip TLS verification (local dev with self-signed certs) |
| `EMBEDDING_*` | Embedding service used for `semantic_*` tool parameters (see `.env.sample`) |
| `HORIZON_WP_DIR` | Directory of the current programme's work programme PDFs (default `data/cff/he/2026-27`) |
| `HORIZON_OS_URL`, `HORIZON_OS_USER`, `HORIZON_OS_PASSWORD` | OpenSearch endpoint of the Horizon topic index |
| `HORIZON_OS_INDEX`, `HORIZON_SEARCH_PIPELINE` | Index name (`horizon-topics`) and search pipeline (`horizon-hybrid-rrf` / `horizon-hybrid-minmax`) |
| `CRISALID_TAXI_URL` | crisalid-taxi base URL (required by `sorbobot`) |
| `SORBOBOT_*` | Sorbobot tuning: timeouts, taxi request, thresholds, sibling widening, ranking method, result sizes, languages (see `.env.sample`) |
| `TRM_MODEL` | Dedicated model of the topic-to-researcher batch (required by `scripts/topic_researcher_match.py`) |
| `TRM_MAX_QUERIES`, `TRM_MAX_CANDIDATES`, `TRM_MIN_SCORE`, `TRM_MAX_RESEARCHERS`, `TRM_CONCURRENCY`, `TRM_OUTPUT_DIR` | TRM tuning (defaults 10 / 25 / 0.35 / 15 / 4 / `reports`) |

## Conventions

- Do not add file-path comments at the top of source files (e.g. `# some/path/file.py`).
- LangGraph graphs are built manually with `StateGraph` + `ToolNode` — do not use `create_react_agent`.
- New agents are created with `scripts/create_new_agent.py`; they must not import from `chat_api/`, and the chat API
  must not import a specific agent (go through `common.registry`). Batch pipelines that are
  not chat agents (`trm/`) live at the repo root, never under `agents/`.
- Use `uv run pytest`; tests never call a real LLM (see `tests/fake_llm.py`) nor a real OpenSearch (fake client in
  `tests/test_horizon_ingest.py`); `uv run pytest --run-pdf` additionally parses the real PDFs of `HORIZON_WP_DIR`.

## Neo4j / Cypher Reference

Authoritative Cypher queries for the CRISalid graph are at `~/PycharmProjects/crisalid-ikg/app/graph/neo4j/queries`. Test fixture data (Cypher) is at `~/WebstormProjects/crisalid-apollo/tests/data/graph.cypher`.
