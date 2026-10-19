# Sorbobot — specification

## 1. Goal

Sorbobot is a chat agent that helps find the research expertise of Sorbonne University in the CRISalid graph. It
can be asked:

- who works on a research topic;
- what a researcher's areas of expertise are;
- a summary of a researcher's work, or a precise question about it;
- what research domains cover and how they differ.

It is served in OpenWebUI and through the chat API, like the other agents of the repository.

## 2. Sources

### 2.1 crisalid-taxi

crisalid-taxi matches a text against the OpenAlex taxonomy. Sorbobot sends it the topic of the question, in
English, on `POST /api/v1/match/`, with a similarity threshold (0.56 by default) and a maximum number of topics
(100). It gets back the matched topics with their similarity, and only keeps the `HAS_TOPIC` links.

taxi does not handle a text shorter than 25 characters. In that case the request is not sent, and the tool asks the
LLM to rephrase with a fuller phrase.

All of this is handled in `taxi.py`.

### 2.2 The CRISalid graph, through the MCP Toolbox

The graph is read through the `sorbobot` toolset of the MCP Toolbox (crisalid-ai-skills). Sorbobot does not write
Cypher: it calls named tools. `graph.py` is a facade over them, with one method per tool and a timeout per call. On
the first message, it checks that the toolset has every tool it needs.

## 3. What is reused from the repository

The agent was created with `scripts/create_new_agent.py` and the `mcp-toolbox` template. It reuses `LangGraphAgent`
for the streaming to the adapters, `MCPToolboxClient` to load the toolset, `build_chat_model` for the LLM and
`fix_raw_tool_calls`.

Unlike the template, the LLM does not see the MCP tools. It only sees four business tools written in Python (see
5.1).

## 4. Searches

### 4.1 Experts of a topic

1. taxi returns the topics, and we keep the 20 closest.
2. Topics without any document in the graph are dropped.
3. An LLM judge scores each topic from 0 to 1 against the question. We keep the topics scored at least
   `SORBOBOT_JUDGE_THRESHOLD`. If fewer than `SORBOBOT_MIN_AUTHORS` (10 by default) researchers linked to these
   topics are found in the graph, the selection is done again with the threshold lowered to 0.5.
4. The number of researchers is adjusted by moving in the taxonomy: to the child concepts when there are more than
   `SORBOBOT_MAX_AUTHORS` (20 by default), to the sibling concepts when there are fewer than `SORBOBOT_MIN_AUTHORS`
   (10 by default). At most 4 steps, and we stop as soon as a step gives nothing.
5. The researchers are ranked (see 4.4). At most `SORBOBOT_SHOW_MAX_AUTHORS` (10 by default) are shown, with their
   three most recent publications, and at most `SORBOBOT_SHOW_MAX_DOMAINS` (5 by default) domains numbered `#1`,
   `#2`…, the closest to the question according to taxi first.

### 4.2 Expertise and profile of a researcher

A fuzzy search finds the researcher's name in the graph. We take the first internal researcher found, and name the
next ones as other possible candidates.

The expertise gives their domains (`#1`, `#2`…) and their publications on these domains, with their role on each one:
author, thesis supervisor or speaker.

The profile is used to summarise their work or to answer a question about it. A second LLM judge picks, among at
most 60 publications, the ones that best answer the question; their abstracts and keywords from the graph are added.

### 4.3 Domain exploration

Same first steps as in 4.1: taxi, topics with documents, judge. Up to `SORBOBOT_EXPLORE_MAX_DOMAINS` (10 by default)
domains are returned with their path in the taxonomy and their definition, the best scored by the LLM judge first.

### 4.4 Ranking of the researchers

Two methods, chosen with `SORBOBOT_RANKING_METHOD`:

- `count`: the number of publications on the selected domains;
- `weighted_relevance` (default): the sum of the weights of the publications (each one weighs according to how close
  it is to the query), weighted by a recency score. This total is multiplied by the share it represents in the whole
  output of the researcher, so that rather generalist researchers get less weight. The score is brought back to 100.

A publication without a date takes the median year. A topic added by the walk in the taxonomy, which taxi did not
score, takes the lowest similarity found by taxi.

## 5. The agent

### 5.1 Graph and tools

The graph is the usual loop of the framework: `agent -> tools -> agent`. The LLM only sees four tools:

| Tool | Used to |
|---|---|
| `find_domain_experts(topic)` | find the experts of a topic (4.1) |
| `get_person_expertise(person_name)` | give the expertise of a researcher (4.2) |
| `get_researcher_profile(person_name, question)` | summarise their work or answer a question about it (4.2) |
| `explore_domains(topic)` | describe domains (4.3) |

Each tool runs its search in Python and returns JSON that the LLM puts into shape.

The answer instructions are in `system_prompt.md`, and the prompts of the two judges in their own `.md` files.

### 5.2 When a service does not answer

We follow the same rule as `common/embedding.py`: a timeout for each call, an error specific to each service, and
a clear message rather than a crash.

- taxi unreachable or too slow (15 s): `TaxiUnavailableError`;
- MCP Toolbox unreachable or too slow (30 s): `GraphUnavailableError`;
- topics found by taxi but no document linked to them in the graph: the tool says so;
- topic too short or name not found: the tool tells the LLM what to do.

In all these cases, the tool returns to the LLM a JSON with an `error` or a `hint` field. The messages are in English
and written in `tools.py`.

### 5.3 Internationalisation

There are no translation files: the LLM handles the language.

The code, the prompts, the tool results and the error messages are in English. The system prompt asks to answer in
the language of the user's last message, and to translate the section titles of the answer layouts too. Names of
people, titles of publications and taxonomy paths stay as they are.

## 6. Configuration

Only `CRISALID_TAXI_URL` is required. Everything else has a default value and is set with `SORBOBOT_*` variables,
read on the first message. The full list is in the README of the agent.

| Variable | Default | Role |
|---|---|---|
| `CRISALID_TAXI_URL` | — | address of crisalid-taxi |
| `SORBOBOT_MCP_TOOLBOX_URL` / `SORBOBOT_MCP_TOOLBOX_TOOLSET` | `CRISALID_MCP_TOOLBOX_URL` / `sorbobot` | MCP Toolbox |
| `KEYCLOAK_*` | — | authentication to the toolbox, when needed |
| `SORBOBOT_JUDGE_THRESHOLD` | `0.7` | minimum score of the judge; `0` disables the judges |
| `SORBOBOT_TOPIC_LINK_THRESHOLD` | `0.57` | minimum similarity between a document and a topic |
| `SORBOBOT_RANKING_METHOD` | `weighted_relevance` | or `count` |
| `SORBOBOT_TAXI_TIMEOUT_SECONDS` / `_TOOLBOX_` / `_JUDGE_` | `15` / `30` / `30` | timeouts in seconds |
