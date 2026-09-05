# $display_name

$description

Generated from the `mcp-toolbox` template of `scripts/create_new_agent.py`: a ReAct agent
over the tools of an MCP Toolbox toolset.

What this package contains:

- `agent.py` — a `LangGraphAgent` subclass owning the whole graph (toolset loading, LLM chain with
  retry and semantic parameter embedding, agent/tools loop, `postprocess_tool_message()` hook), plus
  the `create_agent()` factory. Copied from `agents/generic_agent`; adapt it freely.
- `system_prompt.md` — the system prompt, loaded next to the code.

Configuration (env vars):

- `${NAME}_MCP_TOOLBOX_URL` / `${NAME}_MCP_TOOLBOX_TOOLSET` — this agent's toolbox and
  toolset; when unset, `CRISALID_MCP_TOOLBOX_URL` / `CRISALID_MCP_TOOLBOX_TOOLSET` apply.
- `KEYCLOAK_*` — service-account authentication to the toolbox (see the project README).

What the framework provides (nothing to write per agent):

- The event stream consumed by both adapters (`common/langgraph_agent.py`), the toolbox client
  (`common/mcp_toolbox_client.py`), the embedding provider and the raw tool-call parser.
- The OpenWebUI pipeline (`openwebui_pipelines/${name}_pipeline.py`) and the chat API route
  `POST /agents/$name/chat`.

Run it:

```bash
uv run python scripts/debug_openwebui_pipelines.py      # OpenWebUI Pipelines server
uv run uvicorn chat_api.main:app --port 9100             # chat API
```
