# $display_name

$description

Generated from the `dummy` template of `scripts/create_new_agent.py`.

What this package contains:

- `agent.py` — a `LangGraphAgent` subclass whose `build_graph()` holds plain LangGraph code
  (state, nodes, edges, tools), plus the `create_agent()` factory used by the registry.
- `system_prompt.md` — the system prompt, loaded next to the code.

What the framework provides (nothing to write per agent):

- `astream()` / `stream()` / `ainvoke()` / `invoke()` — the event stream (tokens, tool
  calls, tool results) consumed by both routers, see `common/agent.py`.
- The chat API routes `POST /agents/$name/chat` (MUI X Chat) and `POST /v1/chat/completions` with
  `model: "$name"` (OpenAI-compatible, OpenWebUI), see `chat_api/`.
- Discovery through `common/registry.py`: the agent is served because its package exists
  under `agents/`.

Run it:

```bash
# Chat API (TOKEN: a Keycloak access token with aud crisalid-agents)
uv run uvicorn chat_api.main:app --port 9100
curl -N http://localhost:9100/agents/$name/chat -H "Content-Type: application/json" \
  -H "Authorization: Bearer $$TOKEN" \
  -d '{"message": {"role": "user", "parts": [{"type": "text", "text": "How many words in: the quick brown fox?"}]}}'
```
