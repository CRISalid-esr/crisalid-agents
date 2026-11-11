# crisalid-agents

LangChain / LangGraph agents connected to the CRISalid ecosystem.

The goal of this project is to provide reusable Python agents that can interact with CRISalid services and data sources,
while remaining independent from any single chat interface. Every agent is exposed through one FastAPI service with two
generic routers — an OpenAI-compatible one for OpenWebUI and a MUI X Chat one for webapp frontends (sovisuplus) —
without any adapter code written per agent.

## Core idea

`crisalid-agents` separates three concerns:

1. **Agents** (`agents/<name>/`)

    * Plain LangChain / LangGraph code and business logic, one package per agent.
    * Connected to CRISalid data sources (MCP Toolbox over Neo4j / IKG, …).

2. **Framework** (`common/`)

    * The agent contract (`BaseAgent`: a stream of answer tokens, tool calls and tool results).
    * `LangGraphAgent`: turns any `MessagesState` graph into that event stream (lazy build, streaming).
    * Shared, opt-in helpers: `MCPToolboxClient`, `semantic_*` parameter embedding, the raw tool-call parser.
    * The registry discovering the agents, the identity of the signed-in person (`user_context`, `token_exchange`).

3. **Interface** (`chat_api/`)

    * One FastAPI service: `POST /agents/{name}/chat` (MUI X Chat NDJSON) and `POST /v1/chat/completions`
      (OpenAI-compatible, OpenWebUI). Both routers serve every registered agent; `AGENTS` restricts the set per
      deployment and `AGENT_ALLOW_LIST` the agents each client application may call.

```text
OpenWebUI (System OAuth connection)       Webapp backend (sovisuplus, MUI X Chat)
  │  Authorization: Bearer <user token>     │  Authorization: Bearer <user token>
  ▼                                         ▼
chat_api/  /v1/models, /v1/chat/completions (SSE)   /agents, /agents/{name}/chat (NDJSON)   port 9100
  │   verify token (iss, aud, exp) · allow list (azp) · token exchange for the MCP toolbox
  └──────────────────┬──────────────────────┘
                     ▼
        common/registry  →  agents/<name>/agent.py : create_agent()
                     │
                     ▼
        common/  BaseAgent · LangGraphAgent · MCPToolboxClient (user token bound to the request)
                     │
                     ▼
        LangGraph workflow → LangChain tools / MCP Toolbox / CRISalid services / Neo4j
```

## Creating a new agent

```bash
uv run python scripts/create_new_agent.py sorbobot \
    --display-name "Sorbobot" --description "Answers questions about Sorbonne research" \
    --template mcp-toolbox
```

This generates:

```text
agents/sorbobot/__init__.py
agents/sorbobot/agent.py              # the only file with logic: a LangGraphAgent subclass owning its graph
agents/sorbobot/system_prompt.md
agents/sorbobot/README.md
tests/test_sorbobot.py                # offline smoke test
```

Two templates exist:

* `dummy` (default) — a minimal LangGraph ReAct loop with one local tool; `agents/dummy_agent/` is its checked-in
  rendering and the reference to read first.
* `mcp-toolbox` — a copy of the `generic_agent` graph (ReAct loop over an MCP Toolbox toolset, retry, semantic
  parameter embedding, tool-output post-processing hook) with its own prompt and toolset
  (`<NAME>_MCP_TOOLBOX_URL` / `<NAME>_MCP_TOOLBOX_TOOLSET`, falling back to the `CRISALID_*` ones). The graph code
  is in the agent file, ready to be adapted.

Option `--force` overwrites existing files.

The agent is served as soon as its package exists under `agents/`: it appears in `GET /agents` and `GET /v1/models`,
at `POST /agents/<name>/chat`, and as the model `<name>` of `POST /v1/chat/completions`. Nothing else to register.

### What an agent looks like

```python
class SorbobotAgent(LangGraphAgent):
    def __init__(self, llm=None):
        super().__init__(name="sorbobot", display_name="Sorbobot", description="…")
        self._llm = llm

    async def build_graph(self):
        llm = self._llm or build_chat_model()
        # … plain LangGraph: StateGraph(MessagesState), "agent" and "tools" nodes, edges …
        return graph.compile()


def create_agent(llm=None):
    return SorbobotAgent(llm=llm)
```

`LangGraphAgent` turns the graph into the event stream both routers consume (answer tokens, `ToolCall` emitted
before the tool runs, `ToolResult` after). Agents that are not LangGraph-shaped subclass `common.agent.BaseAgent`
directly and implement `astream()`.

## Setup and Python dependency management

This project uses `uv`:

```bash
uv sync --extra chat-api --extra topic-matching --extra trm
```

The `chat-api` extra pulls the FastAPI service dependencies (fastapi, uvicorn, python-dotenv); `topic-matching` the
Horizon topic index (opensearch-py, pypdf); `trm` the batch reports (jinja2, weasyprint). The virtual environment is
expected under `.venv/`. Create and fill in the `.env` file by copying `.env.sample`.

## Running the chat API

```bash
uv run uvicorn chat_api.main:app --port 9100 --reload
```

Routes:

| Route | Client | Authentication |
|---|---|---|
| `GET /health` | probes | none |
| `GET /agents` | sovisuplus | optional token; the allow list applies when one is sent |
| `POST /agents/{name}/chat` | sovisuplus (MUI X Chat body, NDJSON chunks) | Bearer token |
| `GET /v1/models` | OpenWebUI | optional token; the allow list applies when one is sent |
| `POST /v1/chat/completions` | OpenWebUI (`model` = agent name, SSE when `stream: true`) | Bearer token |

### Authentication

Clients send the signed-in person's Keycloak access token: `Authorization: Bearer <JWT>`. The token is verified
with the realm's JWKS: signature, `iss` = `KEYCLOAK_ISSUER`, `aud` ∋ `CHAT_API_AUDIENCE` (`crisalid-agents`), `exp`,
and at least `MIN_TOKEN_TTL_SECONDS` of remaining life (clients refresh and retry on `401 token_expiring`). Errors
are `401 {"error": "invalid_token" | "wrong_audience" | "token_expiring"}`.

The `azp` claim (the client application the token was issued to) is checked against `AGENT_ALLOW_LIST`
(`<client-id>:<agent>,<agent>;…`): `403 {"error": "agent_not_allowed"}` otherwise. An empty variable disables the
check (local development).

For the MCP toolbox, the chat API exchanges the user's token (RFC 8693, Keycloak standard token exchange, client
`KEYCLOAK_CLIENT_ID` / `KEYCLOAK_CLIENT_SECRET`) for a token with audience `MCP_AUDIENCE` (`crisalid-graph-mcp`) and binds
it to the request (`common/user_context.py`); `MCPToolboxClient` sends it with every tool call, so the toolbox can
inject the person's identity into its queries. Without `KEYCLOAK_CLIENT_ID` the exchange is skipped and tool calls
carry no token (toolbox running without authentication). Batch scripts keep the service-account grant
(`token_provider="service_account"`).

```bash
TOKEN=...   # a Keycloak access token with aud crisalid-agents (see the deployment documentation)
curl http://localhost:9100/agents -H "Authorization: Bearer $TOKEN"

curl -N http://localhost:9100/agents/generic_agent/chat \
  -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" \
  -d '{"message": {"role": "user", "parts": [{"type": "text", "text": "Who works on machine learning?"}]}}'

curl -N http://localhost:9100/v1/chat/completions \
  -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" \
  -d '{"model": "generic_agent", "stream": true, "messages": [{"role": "user", "content": "Who works on machine learning?"}]}'
```

## Running with OpenWebUI

OpenWebUI (0.13 or later) connects to the chat API as an OpenAI-compatible connection whose authentication type is
**System OAuth**: it forwards the signed-in person's access token with every request, so OpenWebUI must itself
authenticate its users with Keycloak (OIDC). See the OpenWebUI Keycloak SSO documentation for the provider side; the
Keycloak clients and scopes are described in the deployment documentation.

When Keycloak uses a locally issued certificate (`mkcert`), build a CA bundle and pass it with the standard SSL env
vars:

```bash
CAROOT="$(mkcert -CAROOT)"
cat /etc/ssl/certs/ca-certificates.crt "$CAROOT/rootCA.pem" > ~/.open-webui/ca-bundle.pem
```

```bash
SSL_CERT_FILE=~/.open-webui/ca-bundle.pem \
REQUESTS_CA_BUNDLE=~/.open-webui/ca-bundle.pem \
DATA_DIR=~/.open-webui \
ENABLE_OAUTH_SIGNUP=true \
ENABLE_OAUTH_ID_TOKEN_COOKIE=false \
OAUTH_CLIENT_ID=open-webui \
OAUTH_CLIENT_SECRET=<client-secret> \
OPENID_PROVIDER_URL=https://<keycloak-host>/realms/<realm>/.well-known/openid-configuration \
OAUTH_PROVIDER_NAME=Keycloak \
OPENID_REDIRECT_URI=http://localhost:8080/oauth/oidc/callback \
uvx --python 3.11 open-webui@latest serve
```

Then, in **Admin panel → Settings → Connections**, add an OpenAI API connection with URL `http://localhost:9100/v1`,
authentication **System OAuth**, no key, "Forward cookies" off. Each agent appears as a model named after its package.
OpenWebUI's own background tasks (chat title, tags, follow-up suggestions) must use a direct LLM model, not an agent.

## Docker image

One image, built from the repository root:

```bash
docker build -f docker/chat-api.Dockerfile -t crisalid-agents-chat-api .
```

It installs the core dependencies and the `chat-api`, `topic-matching` and `trm` extras from `uv.lock`, ships every
agent under `agents/` plus `trm/` and `scripts/` (batch entry points), and trusts any CA certificate mounted under
`/usr/local/share/ca-certificates/`. Runtime configuration is injected at deploy time (see `.env.sample`); set
`AGENTS` to restrict the agents a deployment serves and `AGENT_ALLOW_LIST` the agents each client may call.

## Horizon Europe topic index

`common/horizon/` builds and queries an OpenSearch index of Horizon Europe work programme (WP) topics, one document
per topic, with hybrid search (BM25 + dense vectors on the whole topic and on its section-level passages, fused by
reciprocal rank fusion). It feeds the topic-matching features (topic-to-researcher batch, Horizon topic finder agent).

```bash
# 1. Local OpenSearch (≥ 2.19, k-NN and neural-search plugins are in the default image)
docker compose -f docker/docker-compose.dev.yaml up -d

# 2. Work programme PDFs of the current programme in HORIZON_WP_DIR (git-ignored), one PDF per cluster part
ls data/cff/he/2026-27/

# 3. Parse only (no OpenSearch needed), then index
uv run python scripts/index_horizon_topics.py --dry-run --issues
uv run python scripts/index_horizon_topics.py --recreate --report reports/ingest.json
```

The index mirrors the directory: each run stamps the topics it finds with a run id and, after a complete run, deletes
the topics of files that are no longer there (previous programme). A topic is re-embedded only when its source file,
the parser version or the embedding model changed, so reruns are cheap and an interrupted run can simply be started
again. `--file <pdf>` indexes one file without the stale sweep; the report lists parse warnings and errors with file
and page numbers, and the script exits non-zero on errors only.

Requires the `topic-matching` extra (`uv sync --extra chat-api --extra topic-matching`) and the `EMBEDDING_*` service (bge-m3, `EMBEDDING_DIMENSIONS=1024` sizes the vector fields).
Configuration: `HORIZON_WP_DIR`, `HORIZON_OS_URL`, `HORIZON_OS_USER` / `HORIZON_OS_PASSWORD`, `HORIZON_OS_INDEX`,
`HORIZON_SEARCH_PIPELINE` (`horizon-hybrid-rrf` or `horizon-hybrid-minmax`).

## Topic-to-Researcher Match (TRM)

`trm/` matches every Horizon Europe topic of a cluster with the researchers of our laboratories, from their
publications in the CRISalid knowledge graph. It is a batch run by an administrator, not a chat agent: for each topic
the LLM (`TRM_MODEL`, dedicated) writes publication-like search queries in English and French, the toolbox tool
`publications-by-theme` (with `internal_only`) retrieves matching publications of the laboratories' members, the
candidates are ranked by reciprocal rank fusion over the queries, the LLM verifies each candidate's publications one
by one, and the retained researchers are scored, normalised per topic and reported with a justification and a pair
id (`<topic_id>|<person_uid>`) for later feedback.

```bash
uv run python scripts/topic_researcher_match.py --list-clusters
uv run python scripts/topic_researcher_match.py --cluster CL2                       # one cluster
uv run python scripts/topic_researcher_match.py --topic HORIZON-CL2-2026-01-HERITAGE-02   # one topic
uv run python scripts/topic_researcher_match.py --all                               # every cluster
# in the deployment: docker compose exec crisalid-agents-chat-api python scripts/topic_researcher_match.py --cluster CL2
```

Outputs per cluster in `TRM_OUTPUT_DIR` (`--out`): `trm-<cluster>-<date>.json` (source of truth, with the run
parameters and per-topic errors), `.md`, and `.pdf` (WeasyPrint, `trm` extra; skipped with `--no-pdf` or when the
library is missing). A failed topic is recorded and never aborts the cluster; `--cluster` / `--topic` allow cheap
reruns. Tuning: `TRM_MAX_QUERIES`, `TRM_MAX_CANDIDATES`, `TRM_MIN_SCORE`, `TRM_MAX_RESEARCHERS`, `TRM_CONCURRENCY`
(`--min-score`, `--max-researchers`, `--max-candidates`, `--model` override them). `TRM_CONTRIBUTOR_ROLES` (default
`aut,ctb`) lists the contribution roles that credit a researcher with a publication, so that thesis advisors and
jury members are not counted as authors; empty keeps every role. Requires the topic index
(`HORIZON_OS_*`), the embedding service and the MCP toolbox (`CRISALID_MCP_TOOLBOX_*`, Keycloak optional).

## Tests

```bash
uv run pytest
```

The suite is offline: agents are driven by a scripted fake chat model (`tests/fake_llm.py`), tokens are signed with a
key generated for the session (`tests/jwt_fixtures.py`), the token exchange is mocked, and both routers are
exercised end-to-end (event stream, `<details>` blocks, NDJSON chunks and SSE, authentication, allow list,
scaffolder).

## Debugging in PyCharm

`scripts/debug_chat_api.py` starts the chat API on port 9100 directly through Python (without uvicorn's `--reload`
subprocess), which makes IDE breakpoints available.

Typical PyCharm configuration:

```text
Run → Edit Configurations → + → Python
  Script path:        /path/to/projects/crisalid-agents/scripts/debug_chat_api.py
  Working directory:  /path/to/projects/crisalid-agents
  Python interpreter: /path/to/projects/crisalid-agents/.venv/bin/python
```

Then run the configuration in debug mode.

## LLM configuration

The project can use either the official OpenAI API or any OpenAI-compatible model provider such as vLLM / ILAAS.

Example `.env` (see `.env.sample` for the full list):

```env
# Omit LLM_API_BASE to use the official OpenAI API
LLM_API_BASE=https://llm.ilaas.fr/v1
MODEL=mistral-medium-250523
API_KEY=...

CRISALID_MCP_TOOLBOX_URL=http://127.0.0.1:5000
CRISALID_MCP_TOOLBOX_TOOLSET=crisalid-restricted
```

## Agents

* **`dummy_agent`** — the reference agent: a `LangGraphAgent` with one local tool (`count_words`). Read
  `agents/dummy_agent/agent.py` first; it is the checked-in rendering of the `dummy` scaffold template.
* **`project_topic_matching`** — the Horizon topic finder (PTM): a `LangGraphAgent` with three local tools over the
  Horizon Europe topic index (`common/horizon/`, `HORIZON_OS_*`). From a project description it writes 3–5 English
  queries, runs one hybrid search per query and presents the best-fitting work programme topics with call, deadlines,
  EU contribution and the passage that matches; follow-up questions read the full topic text. See
  `agents/project_topic_matching/README.md`.
* **`generic_agent`** — a `LangGraphAgent` owning its ReAct loop; it connects at runtime to an external MCP Toolbox server
  (`CRISALID_MCP_TOOLBOX_URL`) and calls the tools of a named toolset (`CRISALID_MCP_TOOLBOX_TOOLSET`), with a
  system prompt per toolset and a compacted rendering of the graph schema tool. Its tool calls carry the token of the
  signed-in person exchanged by the chat API, so that the toolbox can resolve "me" / "my" questions.
