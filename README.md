# crisalid-agents

LangChain / LangGraph agents connected to the CRISalid ecosystem.

The goal of this project is to provide reusable Python agents that can interact with CRISalid services and data sources,
while remaining independent from any single chat interface. Agents can be exposed through OpenWebUI Pipelines today, and
later through other interfaces such as LibreChat, a FastAPI service, a CLI, or background workers.

## Core idea

`crisalid-agents` separates two concerns:

1. **Core agents**

    * Built with LangChain and LangGraph.
    * Connected to CRISalid data sources such as Neo4j / IKG.
    * Reusable from different frontends and execution contexts.

2. **Interface adapters**

    * Convert incoming data to the format expected by the agent.
    * The current first adapter is an OpenWebUI Pipeline.

Current architecture:

```text
OpenWebUI
  │
  ▼
openwebui_pipelines/crisalid_agent.py
  │
  ▼
neo4j_cypher_agent.py (first example agent)
  │
  ▼
LangGraph workflow
  │
  ▼
LangChain tools / chains / CRISalid services / Neo4j
```

The current `neo4j_cypher_agent` is only a first working example.

## OpenWebUI Pipelines setup and Python dependency management

This project uses `uv`.

Example setup:

```bash
uv sync
```

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

## Debugging in PyCharm

A debug launcher is provided:

```text
scripts/debug_openwebui_pipelines.py
```

This script starts the Pipelines FastAPI app directly through Python, instead of going through `start.sh`. This makes
IDE breakpoints available.

Typical PyCharm configuration:

```text
Run → Edit Configurations → + → Python
```

Use:

```text
Script path:
  /path/to/projects/crisalid-agents/scripts/debug_openwebui_pipelines.py

Working directory:
  /path/to/projects/crisalid-agents

Python interpreter:
  /path/to/projects/crisalid-agents/.venv/bin/python
```

Then run the configuration in debug mode.

## LLM configuration

The project can use either the official OpenAI API or an OpenAI-compatible model provider such as vLLM / ILAAS.

Example `.env`:

```env
LLM_PROVIDER=openai

OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini

ILAAS_API_URL=https://your-ilaas-server/v1
ILAAS_API_KEY=...
ILAAS_API_MODEL=...

NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=...
```

For ILAAS / vLLM:

```env
LLM_PROVIDER=ilaas
ILAAS_API_URL=https://llm.ilaas.fr/v1
ILAAS_API_KEY=...
ILAAS_API_MODEL=...
```

## Neo4j QA agent

The current example agent uses LangChain's Neo4j integration and Cypher generation.

The Cypher generation is guided by:

```text
base_agent/fewshot_examples.json
```