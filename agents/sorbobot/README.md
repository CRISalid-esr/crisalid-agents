# Sorbobot

Finds the Sorbonne University experts of a research topic, describes and summarises the expertise of a researcher, and
discusses research domains.

## How it works

The graph is the ReAct loop of the framework (`agent → tools → agent`). The LLM only sees four business tools: each runs
a deterministic search in Python over crisalid-taxi and the `sorbobot` MCP toolset, and returns JSON the LLM turns into
an answer. The LLM never writes Cypher.


| Tool                                            | Search                                                                                                        | MCP tools                                                                                                                                                         |
| ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `find_domain_experts(topic)`                    | `experts.py`: taxi topics → topics holding documents → LLM judge (0.7, once 0.5) → concept walk → ranking | `get-concepts-by-uid`, `get-concept-hierarchy`, `list-concept-expert-evidence`, `list-concept-siblings`, `list-concept-children`, `get-person-publication-counts` |
| `get_person_expertise(person_name)`             | `researchers.py`: first internal candidate → domains `#1`… → best publications                             | `search-person-by-name`, `list-person-research-concepts`, `get-concept-hierarchy`, `list-person-top-documents`                                                    |
| `get_researcher_profile(person_name, question)` | `researchers.py`: same person and domains → publication judge → abstracts, numbered by role                 | the same, plus`list-document-summaries`                                                                                                                           |
| `explore_domains(topic)`                        | `domains.py`: taxi topics → topics holding documents → LLM judge → definitions                             | `get-concepts-by-uid`, `get-concept-hierarchy`                                                                                                                    |

**Nothing is stored between requests.** OpenWebUI and the chat API send the whole conversation with every message, so
follow-ups ("and the #2?", "yes", "what methods does she use?") are resolved by the LLM from the previous turns.

## Files


| File                                                                         | Role                                                                                      |
| ---------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| `agent.py`                                                                   | `SorbobotAgent`: builds the services and the graph; `create_agent()`                      |
| `tools.py`                                                                   | The four tools, turning results and expected failures into JSON                           |
| `experts.py`, `researchers.py`, `domains.py`                                 | One module per search                                                                     |
| `topics.py`                                                                  | Steps shared by the expert search and the exploration: matched, documented, judged topics |
| `judges.py`                                                                  | LLM judges of topics and publications, and the parsing of their scores                    |
| `ranking.py`                                                                 | Ranking of the researchers (`count`, `weighted_relevance`), pure functions                |
| `graph.py`, `taxi.py`                                                        | Clients of the MCP toolset and of crisalid-taxi                                           |
| `services.py`, `config.py`, `models.py`                                      | Shared collaborators, settings, result dataclasses                                        |
| `system_prompt.md`, `concept_judge_prompt.md`, `publication_judge_prompt.md` | Prompts                                                                                   |

## Configuration


| Variable                                                     | Default                                 | Purpose                                                                                        |
| ------------------------------------------------------------ | --------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `SORBOBOT_MCP_TOOLBOX_URL` / `SORBOBOT_MCP_TOOLBOX_TOOLSET`  | `CRISALID_MCP_TOOLBOX_URL` / `sorbobot` | MCP Toolbox                                                                                    |
| `KEYCLOAK_*`                                                 | –                                      | Service-account authentication to the toolbox                                                  |
| `CRISALID_TAXI_URL`                                          | **required**                            | crisalid-taxi base URL                                                                         |
| `SORBOBOT_TAXI_TIMEOUT_SECONDS` / `SORBOBOT_TAXI_MAX_TOPICS` | `15` / `100`                            | Taxi request                                                                                   |
| `SORBOBOT_TAXI_MIN_QUERY_CHARS`                              | `25`                                    | Taxi's`MIN_INPUT_LENGTH`: a shorter topic matches nothing, so the tool asks the LLM to rephrase |
| `SORBOBOT_SEMANTIC_THRESHOLD`                                | `0.56`                                  | Minimum query-to-topic similarity (applied by taxi)                                            |
| `SORBOBOT_MAX_SEARCH_TOPICS`                                 | `20`                                    | Topics a search starts from                                                                    |
| `SORBOBOT_TOPIC_LINK_THRESHOLD`                              | `0.57`                                  | Minimum document-to-topic similarity                                                           |
| `SORBOBOT_JUDGE_THRESHOLD`                                   | `0.7`                                   | Relevance a topic needs;`0` disables both judges. A second pass at `0.5` is fixed in `config.py` |
| `SORBOBOT_TOOLBOX_TIMEOUT_SECONDS`                           | `30`                                    | Deadline of one MCP Toolbox call                                                               |
| `SORBOBOT_JUDGE_TIMEOUT_SECONDS`                             | `30`                                    | Deadline of one judge call; past it the search goes on without the judgment                    |
| `SORBOBOT_MIN_AUTHORS` / `SORBOBOT_MAX_AUTHORS`              | `10` / `20`                             | Researcher count the concept walk aims at                                                      |
| `SORBOBOT_RANKING_METHOD`                                    | `weighted_relevance`                    | Or`count`                                                                                      |
| `SORBOBOT_SHOW_MAX_AUTHORS` / `SORBOBOT_SHOW_MAX_DOMAINS`    | `10` / `5`                              | Result sizes                                                                                   |
| `SORBOBOT_SHOW_MAX_PERSON_PUBLICATIONS`                      | `20`                                    | Publications listed with a researcher's expertise                                              |
| `SORBOBOT_PROFILE_MAX_PUBLICATIONS`                          | `20`                                    | Publications given to a profile                                                                |
| `SORBOBOT_EXPLORE_MAX_DOMAINS`                               | `10`                                    | Domains described by an exploration                                                            |

Settings are read when the graph is first built, so the agent can be created without them.

## Run it

```bash
uv run pytest tests/test_sorbobot*.py                   # offline tests
uv run uvicorn chat_api.main:app --port 9100             # POST /agents/sorbobot/chat
uv run python scripts/debug_openwebui_pipelines.py      # model "Sorbobot" in OpenWebUI
```
