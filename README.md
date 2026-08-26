# crisalid-agents

LangChain / LangGraph agents connected to the CRISalid ecosystem.

The goal of this project is to provide reusable Python agents that can interact with CRISalid services and data sources,
while remaining independent from any single chat interface. Agents are currently exposed through two adapters — an
OpenWebUI Pipeline and a FastAPI chat API for webapp frontends (MUI X Chat) — and can later be exposed through other
interfaces such as LibreChat, a CLI, or background workers.

## Core idea

`crisalid-agents` separates two concerns:

1. **Core agents**

    * Built with LangChain and LangGraph.
    * Connected to CRISalid data sources such as Neo4j / IKG.
    * Reusable from different frontends and execution contexts.

2. **Interface adapters**

    * Convert incoming data to the format expected by the agent.
    * Two adapters exist: an OpenWebUI Pipeline (`openwebui_pipelines/`) and a FastAPI chat API (`chat_api/`).

Current architecture:

```text
OpenWebUI                      Webapp (MUI X Chat)
  │                              │
  ▼                              ▼
openwebui_pipelines/           chat_api/  (NDJSON streaming, port 9100)
  │                              │
  └──────────────┬───────────────┘
                 ▼
      core agents (neo4j_cypher_agent, crisalid_graph_agent)
                 │
                 ▼
      LangGraph workflow
                 │
                 ▼
      LangChain tools / MCP Toolbox / CRISalid services / Neo4j
```

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
├── neo4j_cypher_agent
├── other_awesome_agent
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

## Running the agent as a chat API (webapp frontends)

The second adapter, `chat_api/`, exposes `crisalid_graph_agent` to webapp frontends built with MUI X Chat.
`POST /chat` streams NDJSON message chunks (text deltas and tool invocations); `GET /health` is an unauthenticated
health check.

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
curl -N http://localhost:9100/chat \
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

Both install the core dependencies from `uv.lock` and copy the core agent packages; only the chat-api image installs
the `chat-api` extra. Runtime configuration is injected at deploy time through the env vars declared in each
Dockerfile.

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

NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=...
```

## Core agents

Two agents currently exist:

* **`neo4j_cypher_agent`** — uses LangChain's Neo4j integration: it generates a Cypher query from the question, runs
  it, and synthesizes a natural-language answer. The Cypher generation is guided by
  `neo4j_cypher_agent/fewshot_examples.json`.
* **`crisalid_graph_agent`** — a LangGraph ReAct agent that connects at runtime to an external MCP Toolbox server
  (`CRISALID_MCP_TOOLBOX_URL`) and calls the tools of a named toolset (`CRISALID_MCP_TOOLBOX_TOOLSET`). When the
  `KEYCLOAK_*` env vars are set, it authenticates to the toolbox with a Keycloak service account (client credentials).