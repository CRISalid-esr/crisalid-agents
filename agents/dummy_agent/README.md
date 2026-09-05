# Dummy agent

Reference agent: answers questions and counts words with a local tool.

Generated from the `dummy` template of `scripts/create_new_agent.py`.

What this package contains:

- `agent.py` — a `LangGraphAgent` subclass whose `build_graph()` holds plain LangGraph code
  (state, nodes, edges, tools), plus the `create_agent()` factory used by the registry.
- `system_prompt.md` — the system prompt, loaded next to the code.

What the framework provides (nothing to write per agent):

- `astream()` / `stream()` / `ainvoke()` / `invoke()` — the event stream (tokens, tool
  calls, tool results) consumed by both adapters, see `common/agent.py`.
- The OpenWebUI pipeline (`openwebui_pipelines/dummy_agent_pipeline.py`, a two-line stub over
  `common/openwebui.py`) and the chat API route `POST /agents/dummy_agent/chat` (`chat_api/`).
- Discovery through `common/registry.py`: the agent is served because its package exists
  under `agents/`.

Run it:

```bash
# OpenWebUI Pipelines server (model "Dummy agent")
uv run python scripts/debug_openwebui_pipelines.py

# Chat API
uv run uvicorn chat_api.main:app --port 9100
curl -N http://localhost:9100/agents/dummy_agent/chat -H "Content-Type: application/json" \
  -H "x-api-key: key1" \
  -d '{"message": {"role": "user", "parts": [{"type": "text", "text": "How many words in: the quick brown fox?"}]}}'
```
