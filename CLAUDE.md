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

There is no test suite yet. There is no linter configured.

## Architecture

This project builds LangChain/LangGraph agents for querying the CRISalid institutional knowledge graph (Neo4j), and exposes them through OpenWebUI Pipelines.

The layering is strict: core agents have no knowledge of the interface that calls them.

```
openwebui_pipelines/   ← OpenWebUI adapters (convert messages, call agents)
neo4j_cypher_agent/    ← Agent 1: generates Cypher, executes it, answers in natural language
neo4j_mcp_toolbox_agent/ ← Agent 2: calls MCP Toolbox tools via LangGraph ReAct agent
common/                ← Shared LLM factory (OpenAI or ILAAS/vLLM)
```

### Agent 1 — Neo4j Cypher Agent

Uses LangChain's `GraphCypherQAChain`. Given a natural language question, it generates a Cypher query (guided by 15 few-shot examples in `fewshot_examples.json`), runs it against Neo4j, then synthesizes a natural-language answer.

- `cypher_qa_chain.py` — chain configuration, prompt, few-shot examples
- `lc_graph.py` — single-node LangGraph wrapping the chain

### Agent 2 — MCP Toolbox Agent

Connects at runtime to an external MCP Toolbox server (`CRISALID_MCP_TOOLBOX_URL`) and loads named toolsets (`CRISALID_MCP_TOOLBOX_TOOLSET`, default `"crisalid-restricted"`). Builds a LangGraph ReAct agent from those tools.

- `mcp_toolbox_client.py` — connects to toolbox, loads tools
- `lc_graph.py` — `MCPToolboxGraphFactory` (lazy init: graph built on first `ainvoke()`)

### OpenWebUI Pipelines

Each pipeline is a `Pipeline` class with a `pipe()` method. It converts OpenWebUI message format to LangChain `BaseMessage` types, then delegates to the corresponding agent's `ainvoke()`.

The pipelines server runs on `http://localhost:9099` and is treated by OpenWebUI as an OpenAI-compatible endpoint.

## Environment Variables

| Variable | Purpose |
|---|---|
| `LLM_PROVIDER` | `ilaas` (default) or `openai` |
| `ILAAS_API_URL` / `ILAAS_API_KEY` / `ILAAS_API_MODEL` | ILAAS/vLLM provider |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | OpenAI provider (default model: `gpt-4o-mini`) |
| `NEO4J_URI` / `NEO4J_USERNAME` / `NEO4J_PASSWORD` | Neo4j connection |
| `CRISALID_MCP_TOOLBOX_URL` | MCP Toolbox server URL |
| `CRISALID_MCP_TOOLBOX_TOOLSET` | Toolset name to load from MCP Toolbox |

## Conventions

- Do not add file-path comments at the top of source files (e.g. `# some/path/file.py`).
- LangGraph graphs are built manually with `StateGraph` + `ToolNode` — do not use `create_react_agent`.

## Neo4j / Cypher Reference

Authoritative Cypher queries for the CRISalid graph are at `~/PycharmProjects/crisalid-ikg/app/graph/neo4j/queries`. Test fixture data (Cypher) is at `~/WebstormProjects/crisalid-apollo/tests/data/graph.cypher`.