# Sorbobot

Answers questions about the research expertise of an institution's researchers, read from the CRISalid knowledge
graph: the experts of a research theme (ranked, with a score out of 100), the expertise of a named researcher, a
summary of their work, and the meaning of the OpenAlex research domains returned. Specification:
`specs/22-sorbobot-implementation/prompt.md`.

## How it works

The graph is the ReAct loop of the framework (`agent → tools → agent`). The LLM only sees four business tools; each
runs a deterministic search in Python over crisalid-taxi (text → OpenAlex topics) and the `sorbobot` MCP toolset, and
answers JSON that the LLM turns into the answer. The LLM never writes Cypher and never calls the MCP tools itself.

| Tool | Module | MCP tools |
|---|---|---|
| `find_domain_experts(topic, language)` | `experts.py`: taxi topics → documented topics → topic judge → evidence rows → siblings when too few researchers → ranking | `get-concepts-by-uid`, `get-concept-hierarchy`, `list-concept-expert-evidence`, `list-concept-siblings`, `get-person-publication-counts` |
| `get_person_expertise(person_name, language)` | `researchers.py`: first internal researcher of that name → top domains → authored publications | `search-person-by-name`, `list-person-research-concepts`, `get-concepts-by-uid`, `get-concept-hierarchy`, `list-person-top-documents` |
| `get_researcher_profile(person_name, question, language)` | `researchers.py`: same, every role, publication judge, abstracts and keywords, numbered references | the same plus `list-document-summaries` |
| `explore_domains(topic \| domain_names, language)` | `domains.py`: taxi topics → documented → judged, or displayed names resolved to topics | `search-concept-by-name`, `get-concepts-by-uid`, `get-concept-hierarchy` |

The LLM detects the answer language and passes it as the `language` parameter of every tool call. The `agent` node
handles, without calling the LLM, the empty or too long messages, the technical errors returned by the tools
(`taxi_unavailable`, `graph_unavailable`) and the failures of the LLM itself, with the messages of
`locales/<lang>.json`. Functional hints (`topic_too_short`, `no_document`, `no_expert`, `person_not_found`,
`no_domain`) are explained by the LLM.

## Files

| File | Role |
|---|---|
| `agent.py` | `SorbobotAgent`: builds the components on the first message, the graph, the input control and the error messages |
| `tools.py` | The four tools, turning results, hints and technical errors into JSON |
| `experts.py`, `researchers.py`, `domains.py` | One module per search |
| `topics.py` | Steps shared by the expert search and the exploration: taxi topics, documented topics, judged topics |
| `ranking.py` | Ranking of the researchers (`weighted_relevance`, `count`), pure functions |
| `judges.py` | LLM judges of topics and publications, tolerant parsing of their scores |
| `taxi.py`, `toolbox.py` | Clients of crisalid-taxi and of the MCP toolset (one toolbox client per event loop) |
| `i18n.py`, `locales/` | Predefined messages shown as they are, one JSON catalogue per language |
| `config.py`, `models.py` | Settings read from the environment, rows and helpers |
| `system_prompt.md`, `topic_judge_prompt.md`, `publication_judge_prompt.md` | Prompts |

## Configuration

Read from the environment on the first message; an invalid value is a deployment error.

| Variable | Default | Purpose |
|---|---|---|
| `CRISALID_TAXI_URL` | **required** | crisalid-taxi base URL |
| `SORBOBOT_MCP_TOOLBOX_URL` / `SORBOBOT_MCP_TOOLBOX_TOOLSET` | `CRISALID_MCP_TOOLBOX_URL` / `sorbobot` | MCP Toolbox |
| `KEYCLOAK_*` | – | service-account authentication to the toolbox |
| `SORBOBOT_TAXI_TIMEOUT_SECONDS`, `SORBOBOT_TOOLBOX_TIMEOUT_SECONDS`, `SORBOBOT_JUDGE_TIMEOUT_SECONDS` | 15, 30, 30 | timeouts |
| `SORBOBOT_TAXI_MAX_TOPICS`, `SORBOBOT_TAXI_MIN_QUERY_CHARS`, `SORBOBOT_SEMANTIC_THRESHOLD` | 100, 25, 0.56 | taxi request |
| `SORBOBOT_MAX_SEARCH_TOPICS`, `SORBOBOT_TOPIC_LINK_THRESHOLD` | 20, 0.57 | topics kept from taxi, minimum publication-to-topic similarity |
| `SORBOBOT_TOPIC_SOURCES` | `crisalid` | `HAS_TOPIC` link sources taken into account (comma-separated; empty keeps every source) |
| `SORBOBOT_JUDGE_THRESHOLD` | 0.6 | minimum judge score; `0` disables both judges |
| `SORBOBOT_MIN_AUTHORS`, `SORBOBOT_MAX_JUDGED_SIBLINGS` | 2, 100 | sibling widening |
| `SORBOBOT_RANKING_METHOD` | `weighted_relevance` | or `count` |
| `SORBOBOT_SHOW_MAX_AUTHORS`, `SORBOBOT_SHOW_MAX_DOMAINS`, `SORBOBOT_SHOW_MAX_PERSON_PUBLICATIONS`, `SORBOBOT_PROFILE_MAX_PUBLICATIONS`, `SORBOBOT_EXPLORE_MAX_DOMAINS` | 10, 5, 20, 20, 10 | result sizes |
| `SORBOBOT_MAX_MESSAGE_CHARS` | 1000 | maximum length of a user message |
| `SORBOBOT_SUPPORTED_LANGUAGES`, `SORBOBOT_DEFAULT_LANGUAGE` | `en,fr`, `en` | answer languages (each needs a catalogue in `locales/`) |

## Run it

```bash
uv run uvicorn chat_api.main:app --port 9100             # chat API: POST /agents/sorbobot/chat
uv run pytest tests -k sorbobot                          # offline tests (fake LLM, taxi and toolbox)
```
