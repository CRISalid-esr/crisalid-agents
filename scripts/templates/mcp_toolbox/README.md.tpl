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
- Tool calls carry the signed-in person's token exchanged by the chat API (`common/user_context.py`); see the
  project README for the Keycloak configuration.

What the framework provides (nothing to write per agent):

- The event stream consumed by both routers (`common/langgraph_agent.py`), the toolbox client
  (`common/mcp_toolbox_client.py`), the `semantic_*` embedding helpers and the raw tool-call parser.
- The chat API routes `POST /agents/$name/chat` (MUI X Chat) and `POST /v1/chat/completions` with
  `model: "$name"` (OpenAI-compatible, OpenWebUI).

Run it:

```bash
uv run uvicorn chat_api.main:app --port 9100             # chat API
```
