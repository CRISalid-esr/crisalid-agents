# crisalid-agents

LangChain / LangGraph agents connected to the CRISalid ecosystem.

The goal of this project is to provide reusable Python agents that can interact with CRISalid services and data sources,
while remaining independent from any single chat interface. Every agent is exposed through two generic adapters — an
OpenWebUI Pipeline and a FastAPI chat API for webapp frontends (MUI X Chat, sovisuplus) — without any adapter code
written per agent.

## Core idea

`crisalid-agents` separates three concerns:

1. **Agents** (`agents/<name>/`)

    * Plain LangChain / LangGraph code and business logic, one package per agent.
    * Connected to CRISalid data sources (MCP Toolbox over Neo4j / IKG, …).

2. **Framework** (`common/`)

    * The agent contract (`BaseAgent`: a stream of answer tokens, tool calls and tool results).
    * `LangGraphAgent`: turns any `MessagesState` graph into that event stream (lazy build, streaming).
    * Shared, opt-in helpers: `MCPToolboxClient`, `semantic_*` parameter embedding, the raw tool-call parser.
    * The registry discovering the agents, and the shared OpenWebUI pipeline code.

3. **Interface adapters**

    * OpenWebUI Pipeline (`openwebui_pipelines/`, a two-line stub per agent) and the FastAPI chat API (`chat_api/`).
    * Both serve every registered agent; the `AGENTS` env var restricts the set per deployment.

```text
OpenWebUI                                 Webapp backend (sovisuplus, MUI X Chat)
  │                                         │
  ▼                                         ▼
openwebui_pipelines/<name>_pipeline.py    chat_api/  POST /agents/{name}/chat  (NDJSON, port 9100)
  │                                         │
  └──────────────────┬──────────────────────┘
                     ▼
        common/registry  →  agents/<name>/agent.py : create_agent()
                     │
                     ▼
        common/  BaseAgent · LangGraphAgent · MCPToolboxClient
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
openwebui_pipelines/sorbobot_pipeline.py   # Pipeline = make_pipeline("sorbobot")
tests/test_sorbobot.py                # offline smoke test
```

Two templates exist:

* `dummy` (default) — a minimal LangGraph ReAct loop with one local tool; `agents/dummy_agent/` is its checked-in
  rendering and the reference to read first.
* `mcp-toolbox` — a copy of the `generic_agent` graph (ReAct loop over an MCP Toolbox toolset, retry, semantic
  parameter embedding, tool-output post-processing hook) with its own prompt and toolset
  (`<NAME>_MCP_TOOLBOX_URL` / `<NAME>_MCP_TOOLBOX_TOOLSET`, falling back to the `CRISALID_*` ones). The graph code
  is in the agent file, ready to be adapted.

Options: `--no-openwebui` skips the pipeline stub, `--force` overwrites existing files.

The agent is served as soon as its package exists under `agents/`: it appears in `GET /agents`, at
`POST /agents/<name>/chat`, and as a model of the Pipelines server. Nothing else to register.

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

`LangGraphAgent` turns the graph into the event stream both adapters consume (answer tokens, `ToolCall` emitted
before the tool runs, `ToolResult` after). Agents that are not LangGraph-shaped subclass `common.agent.BaseAgent`
directly and implement `astream()`.

## OpenWebUI Pipelines setup and Python dependency management

This project uses `uv`.

Example setup:

```bash
uv sync --extra chat-api
```

The `chat-api` extra pulls the FastAPI wrapper dependencies (fastapi, uvicorn). They are kept out of the core
dependencies on purpose: the OpenWebUI Pipelines Docker image ships its own fastapi/uvicorn versions and must not have
them overridden.

The local virtual environment is expected to be under:

```text
.venv/
```

The OpenWebUI Pipelines project is used as a local vendor dependency during development.

Clone it into a hidden directory at the project root:

```bash
git clone https://github.com/open-webui/pipelines.git .openwebui-pipelines
```

Install the OpenWebUI Pipelines server dependencies into the current uv virtual environment:

```bash
uv pip install -r .openwebui-pipelines/requirements.txt
```

> **Note:** `uv sync` (run automatically by `uv add` / `uv remove`) resets the virtual environment to `pyproject.toml`
> only, removing any extras installed with `uv pip install`. Re-run the line above after every dependency change.

This is required because .openwebui-pipelines/start.sh launches the Pipelines FastAPI server from the cloned vendor
project. The crisalid-agents project dependencies and the Pipelines server dependencies live in the same local .venv
during development.
The resulting layout should be similar to:

```text
.
├── .openwebui-pipelines
├── .venv
├── agents
├── chat_api
├── common
├── openwebui_pipelines
├── pyproject.toml
└── scripts
```

Create and fill in the .env file by copying the .env.sample.

## Running the agent as an OpenWebUI Pipeline

1) Run the Pipelines server from the cloned Pipelines directory:

```bash
cd .openwebui-pipelines/

PROJECT_ROOT="/path/to/the/directory/of/crisalid-agents"

PYTHONPATH="$PROJECT_ROOT:$PYTHONPATH" \
PIPELINES_DIR="$PWD/../openwebui_pipelines" \
PIPELINES_API_KEY="my-secret-api-key" \
./start.sh --mode run
```

The Pipelines server should start on:

```text
http://localhost:9099
```


**WARNING**

Should the initial start fail in importing a pipeline, said pipeline will be moved to a folder called "failed" in the folder "openwebui_pipelines". The pipeline will then not be loaded again until moved again out of "failed" and in the "openwebui_pipelines" folder.


2) Then OpenWebUI itself should be launched separately in another terminal.

### Without Keycloak authentication

```bash
DATA_DIR=~/.open-webui uvx --python 3.11 open-webui@0.9.5 serve --port 8081
```

It can be accessed at:

```text
http://localhost:8081
```

### With Keycloak authentication

See the [OpenWebUI Keycloak SSO documentation](https://docs.openwebui.com/features/authentication-access/auth/sso/keycloak/) for the full configuration reference.

When Keycloak uses a self-signed or locally-trusted certificate (e.g. via `mkcert`), build a CA bundle that includes both the system CAs and the local root CA, then pass it to OpenWebUI via the standard SSL env vars:

```bash
CAROOT="$(mkcert -CAROOT)"
cat /etc/ssl/certs/ca-certificates.crt "$CAROOT/rootCA.pem" > ~/.open-webui/ca-bundle.pem
```

Then start OpenWebUI with the Keycloak OIDC parameters:

```bash
SSL_CERT_FILE=~/.open-webui/ca-bundle.pem \
REQUESTS_CA_BUNDLE=~/.open-webui/ca-bundle.pem \
DATA_DIR=~/.open-webui \
ENABLE_OAUTH_SIGNUP=true \
OAUTH_CLIENT_ID=<client-id> \
OAUTH_CLIENT_SECRET=<client-secret> \
OPENID_PROVIDER_URL=https://<keycloak-host>/realms/<realm>/.well-known/openid-configuration \
OAUTH_PROVIDER_NAME=Keycloak \
OPENID_REDIRECT_URI=http://localhost:8081/oauth/oidc/callback \
uvx --python 3.11 open-webui@0.9.5 serve --port 8081
```

It can be accessed at:

```text
http://localhost:8081
```

3) In OpenWebUI, add an OpenAI-compatible connection pointing to:

```text
http://localhost:9099
```

using the same API key as above:

```text
my-secret-api-key
```

## OpenWebUI settings

OpenWebUI may call the selected model for auxiliary tasks such as:

* chat title generation,
* tag generation,
* follow-up suggestions.

These OpenWebUI-generated calls should be disabled in OpenWebUI settings while using CRISalid
agent pipelines.

## Running the agents as a chat API (webapp frontends)

The second adapter, `chat_api/`, exposes every registered agent to webapp frontends built with MUI X Chat
(sovisuplus). `POST /agents/{name}/chat` streams NDJSON message chunks (text deltas and tool invocations),
`GET /agents` lists the served agents, and `GET /health` is an unauthenticated health check.

```bash
uv run uvicorn chat_api.main:app --port 9100 --reload
```

### Authentication

The chat API is meant to be called server-to-server from the internal Docker network only, never directly from the
browser (there is no CORS support). Inbound requests are authenticated with the same scheme as crisalid-apollo: the
`x-api-key` header is checked against the comma-separated `API_KEYS` env var, and the check is enabled unless
`ENABLE_API_KEYS` is set to `false`:

```env
ENABLE_API_KEYS="true"
API_KEYS="key1,another_key"
```

Proper OIDC end-user authentication is planned for later.

```bash
curl http://localhost:9100/agents -H "x-api-key: key1"

curl -N http://localhost:9100/agents/generic_agent/chat \
  -H "Content-Type: application/json" \
  -H "x-api-key: key1" \
  -d '{"message": {"role": "user", "parts": [{"type": "text", "text": "Who works on machine learning?"}]}}'
```

## Docker images

Each adapter has its own image, built from the repository root:

```bash
# OpenWebUI Pipelines wrapper (base image ghcr.io/open-webui/pipelines, port 9099)
docker build -f docker/pipelines.Dockerfile -t crisalid-agents-owui .

# FastAPI chat API wrapper (base image python:3.11-slim, port 9100)
docker build -f docker/chat-api.Dockerfile -t crisalid-agents-chat-api .
```

Both install the core dependencies and the `topic-matching` extra from `uv.lock` and ship every agent under
`agents/`; only the chat-api image installs the `chat-api` and `trm` extras (plus `trm/` and `scripts/`, for the
batch entry points). Runtime configuration is injected at deploy time (see `.env.sample`); set `AGENTS` to restrict
the agents a deployment serves.

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

Requires the `topic-matching` extra (`uv sync --extra chat-api --extra topic-matching`, then the pipelines
requirements line) and the `EMBEDDING_*` service (bge-m3, `EMBEDDING_DIMENSIONS=1024` sizes the vector fields).
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
(`--min-score`, `--max-researchers`, `--max-candidates`, `--model` override them). Requires the topic index
(`HORIZON_OS_*`), the embedding service and the MCP toolbox (`CRISALID_MCP_TOOLBOX_*`, Keycloak optional).

## Tests

```bash
uv run pytest
```

The suite is offline: agents are driven by a scripted fake chat model (`tests/fake_llm.py`) and both adapters are
exercised end-to-end (event stream, OpenWebUI `<details>` blocks, NDJSON chunks, auth, scaffolder).

## Debugging in PyCharm

Two debug launchers are provided:

```text
scripts/debug_openwebui_pipelines.py   # Pipelines server on port 9099
scripts/debug_chat_api.py              # chat API on port 9100
```

These scripts start the corresponding FastAPI app directly through Python (without `start.sh` or uvicorn's `--reload`
subprocess). This makes IDE breakpoints available.

Typical PyCharm configuration:

```text
Run → Edit Configurations → + → Python
```

Use:

```text
Script path:
  /path/to/projects/crisalid-agents/scripts/debug_openwebui_pipelines.py
  (or scripts/debug_chat_api.py)

Working directory:
  /path/to/projects/crisalid-agents

Python interpreter:
  /path/to/projects/crisalid-agents/.venv/bin/python
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
* **`generic_agent`** — a `LangGraphAgent` owning its ReAct loop; it connects at runtime to an external MCP Toolbox server
  (`CRISALID_MCP_TOOLBOX_URL`) and calls the tools of a named toolset (`CRISALID_MCP_TOOLBOX_TOOLSET`), with a
  system prompt per toolset and a compacted rendering of the graph schema tool. When the `KEYCLOAK_*` env vars are
  set, it authenticates to the toolbox with a Keycloak service account (client credentials).
