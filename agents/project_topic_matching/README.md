# Horizon topic finder

Finds the Horizon Europe work programme topics that best fit a project idea (PTM, Project-to-Topic Match).

Generated from the `dummy` template of `scripts/create_new_agent.py`, then given three local tools over the
Horizon topic index of `common/horizon/` (OpenSearch, hybrid keyword + vector search):

- `search_horizon_topics(query, cluster?, type_of_action?, top_k?)` — one English query, best-matching topics with
  their metadata and the passage that matches the query best;
- `get_horizon_topic(topic_id)` — the full text of one topic for follow-up questions;
- `list_horizon_clusters()` — the clusters present in the index.

The system prompt (`system_prompt.md`) makes the model write 3–5 English queries per project description, issue one
`search_horizon_topics` call per query (visible as tool blocks in OpenWebUI), merge the results and present at most
five topics with id, title, call, deadlines, EU contribution and one sentence on why each fits. The answer follows the
user's language.

Requirements: the topic index (`scripts/index_horizon_topics.py`, `HORIZON_OS_*`) and the embedding service
(`EMBEDDING_*`) used to build it. The OpenSearch connection is opened on first use and closed with the agent.

Run it:

```bash
# OpenWebUI Pipelines server (model "Horizon topic finder")
uv run python scripts/debug_openwebui_pipelines.py

# Chat API
uv run uvicorn chat_api.main:app --port 9100
curl -N http://localhost:9100/agents/project_topic_matching/chat -H "Content-Type: application/json" \
  -H "x-api-key: key1" \
  -d '{"message": {"role": "user", "parts": [{"type": "text", "text": "Machine learning to restore damaged medieval manuscripts"}]}}'
```
