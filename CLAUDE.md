# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies (run both — uv sync resets the venv and wipes the pipelines extras)
uv sync
uv pip install -r .openwebui-pipelines/requirements.txt

# Run Pipelines server (production)
.openwebui-pipelines/start.sh

# Debug Pipelines server in IDE (PyCharm)
uv run python scripts/debug_openwebui_pipelines.py
```

**After any `uv add`, `uv remove`, or `uv sync`, re-run the `uv pip install` line** — `uv sync` resets the venv to exactly `pyproject.toml` and removes the OpenWebUI Pipelines extras installed separately.

There is no linter configured.

## Architecture

This project builds LangChain/LangGraph agents for querying the CRISalid institutional knowledge graph (Neo4j), and exposes them through OpenWebUI Pipelines.

The layering is strict: core agents have no knowledge of the interface that calls them.

```
openwebui_pipelines/   ← OpenWebUI adapters (convert messages, call agents)
neo4j_cypher_agent/    ← Agent 1: generates Cypher, executes it, answers in natural language
crisalid_graph_agent/  ← Agent 2: calls MCP Toolbox tools via LangGraph ReAct agent
sorbobot_agent/        ← Agent 3: domain-expert / person-expertise / database-query agent
common/                ← Shared LLM factory (any OpenAI-compatible endpoint)
```

### Agent 1 — Neo4j Cypher Agent

Uses LangChain's `GraphCypherQAChain`. Given a natural language question, it generates a Cypher query (guided by 15 few-shot examples in `fewshot_examples.json`), runs it against Neo4j, then synthesizes a natural-language answer.

- `cypher_qa_chain.py` — chain configuration, prompt, few-shot examples
- `lc_graph.py` — single-node LangGraph wrapping the chain

### Agent 2 — CRISalid Graph Agent (`crisalid_graph_agent/`)

Connects at runtime to an external MCP Toolbox server (`CRISALID_MCP_TOOLBOX_URL`) and loads named toolsets (`CRISALID_MCP_TOOLBOX_TOOLSET`, default `"crisalid-restricted"`). Builds a LangGraph ReAct agent from those tools.

- `mcp_toolbox_client.py` — connects to toolbox, loads tools
- `lc_graph.py` — `MCPToolboxGraphFactory` (lazy init: graph built on first `ainvoke()`)
- `crisalid_graph_agent.py` — `CrisalidGraphAgent` (public interface: `invoke`, `astream`, `stream`)

### Agent 3 — SorboBot (`sorbobot-agent`, external package)

Domain-expert / person-expertise / database-query agent for the Sorbonne
research graph. Source and docs live in the sibling `sorbobot-agent` repo
(see its own CLAUDE.md). Uses `MCP_TOOLBOX_URL`/`MCP_TOOLBOX_TOOLSET`
(`sorbobot` toolset) and `CRISALID_TAXI_URL` for semantic domain
matching. No Keycloak support — runs unauthenticated only.

### OpenWebUI Pipelines

Each pipeline is a `Pipeline` class with a `pipe()` method. It converts OpenWebUI message format to LangChain `BaseMessage` types, then delegates to the corresponding agent's `ainvoke()`.

The pipelines server runs on `http://localhost:9099` and is treated by OpenWebUI as an OpenAI-compatible endpoint.

## Environment Variables

| Variable | Purpose |
|---|---|
| `MODEL` | Model name (e.g. `mistral-medium-250523`, `gpt-4o-mini`) |
| `API_KEY` | API key for the LLM endpoint |
| `LLM_API_BASE` | Base URL of the OpenAI-compatible endpoint (omit for OpenAI default) |
| `NEO4J_URI` / `NEO4J_USERNAME` / `NEO4J_PASSWORD` | Neo4j connection |
| `CRISALID_MCP_TOOLBOX_URL` | MCP Toolbox server URL (crisalid_graph_agent) |
| `CRISALID_MCP_TOOLBOX_TOOLSET` | Toolset name to load from MCP Toolbox (crisalid_graph_agent) |
| `MCP_TOOLBOX_URL` | MCP Toolbox server URL (SorboBot, `sorbobot` toolset) |
| `MCP_TOOLBOX_TOOLSET` | Toolset name to load from MCP Toolbox (SorboBot) |
| `CRISALID_TAXI_URL` | crisalid-taxi service URL, for SorboBot's semantic domain matching |
| `KEYCLOAK_ISSUER` | Keycloak issuer URL (e.g. `https://keycloak.example.com/realms/my-realm`); omit to disable auth |
| `KEYCLOAK_CLIENT_ID` | Service account client ID |
| `KEYCLOAK_CLIENT_SECRET` | Service account client secret |
| `KEYCLOAK_SSL_VERIFY` | Set to `false` to skip TLS verification (local dev with self-signed certs) |

## Conventions

- Do not add file-path comments at the top of source files (e.g. `# some/path/file.py`).
- LangGraph graphs are built manually with `StateGraph` + `ToolNode` — do not use `create_react_agent`.

## Neo4j / Cypher Reference

Authoritative Cypher queries for the CRISalid graph are at `~/PycharmProjects/crisalid-ikg/app/graph/neo4j/queries`. Test fixture data (Cypher) is at `~/WebstormProjects/crisalid-apollo/tests/data/graph.cypher`.