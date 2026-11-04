# Sorbobot — specification

## 1. Purpose

Sorbobot is an agent that answers questions about the research expertise of an institution's researchers. This
expertise is inferred from the researchers' publications and from the OpenAlex taxonomy topics those publications are
linked to (see 2.2).

It can be asked to:

| Question | Example | What the user gets |
|---|---|---|
| Find the experts on a subject | "Who works on machine learning for medical imaging?" | the domains closest to the subject, then a ranked list of researchers with a score out of 100 |
| Learn a researcher's expertise | "What are Joe Dupont's areas of expertise?" | their main domains, then a few of their publications in those domains |
| Summarise a researcher's work | "Summarise their research" | a summary of the main research domains, grouped by role |
| Understand the research domains returned | "What does domain #2 of the previous answer correspond to?" | a description of the domain, based on the information held in the graph |

Like the other agents of the repository, Sorbobot is served in OpenWebUI and through the chat API.

### 1.1 What Sorbobot does not do

- It does not answer questions about the knowledge graph outside the scope of expertise.
- It never writes to the graph.
- It keeps nothing from one conversation to the next.
- It does not answer questions about political or religious orientations, about the personal life of researchers, or
  questions intended to evaluate, rank or disparage researchers or staff of the institution.
- It does not answer from the knowledge the LLM acquired during training, but only from the knowledge graph data,
  except for a short general definition ("what is a neural network?").

## 2. The services used

Sorbobot does not see the graph directly; it only reaches it through the tools of the `sorbobot` toolset of the MCP
Toolbox (`crisalid-ai-skills/mcp-toolbox/tools.yaml`) and it never writes Cypher queries.

### 2.1 The `sorbobot` toolset

The LLM does not see the toolbox's MCP tools directly. It only sees the four **LangGraph tools** described in §5. Each
LangGraph tool runs the corresponding business algorithm and calls, in the order that algorithm prescribes, the **MCP
tools** of the `sorbobot` toolset. When an MCP tool expects several identifiers, the code passes them as a single
comma-separated string.

OpenAlex topics:

- `get-concepts-by-uid(uids, similarity_threshold, sources)`: name, definition and publication count of the topics; returns
  `uid, name, description, nb_docs`. `nb_docs` only counts the links whose similarity reaches the threshold.
- `get-concept-hierarchy(uids)`: the topics' ancestors, used to build their path from Domain to Topic; returns
  `uid, ancestor_name, ancestor_type, dist` (`dist = 0` for the topic itself).
- `list-concept-siblings(uids)`: the siblings, i.e. the topics of the same SubField, excluding the given topics;
  returns `uid`.
- `search-concept-by-name(name)`: the concepts whose preferred label equals the name, ignoring case and surrounding
  whitespace (no fuzzy matching); returns `uid, name, type`. One name per call, because topic labels may contain
  commas. Used to find the uid of a domain quoted from an earlier answer (§5.4).

Data used to search for experts:

- `list-concept-expert-evidence(uids, similarity_threshold, sources)`: the evidence rows, one per internal researcher,
  publication and topic; returns `person_uid, display_name, doc_uid, doc_title, publication_date, topic_uid,
  similarity`.
- `get-person-publication-counts(person_uids)`: the total number of publications of each researcher, all subjects
  combined; returns `person_uid, nb_publications_total`. Used for ranking (§6.3).

Data used to look up a researcher:

- `search-person-by-name(name, max_results)`: fuzzy search on the name, most likely person first, externals included;
  returns `uid, display_name, external, score`.
- `list-person-research-concepts(person_uid, similarity_threshold, limit, sources)`: their topics, from most to least
  published; returns `domain_uid, domain_name, nb_publications`.
- `list-person-top-documents(person_uid, concept_uids, similarity_threshold, limit, sources)`: their publications on these
  topics, from best linked to least linked; returns `doc_uid, title, publication_date, concept_uids, roles`.
- `list-document-summaries(uids)`: abstract and keywords of a profile's publications; returns `uid, abstract,
  keywords`.

An internal researcher is a person of the institution (`external = false`). Only the author (`aut`), thesis supervisor
(`ths`) and speaker (`spk`) roles are taken into account. A topic is said to be documented if it has at least one
linked publication whose similarity (the `score` value of the `HAS_TOPIC` relationship) is at least
`SORBOBOT_TOPIC_LINK_THRESHOLD`.

`HAS_TOPIC` links come from several sources, recorded in their `source` property: `crisalid` links carry the cosine
similarity computed by crisalid-taxi, `openalex` links carry the OpenAlex topic score, which lives on another scale.
The four tools above take a comma-separated `sources` parameter and only look at the links of these sources (empty
keeps every link). Sorbobot passes `SORBOBOT_TOPIC_SOURCES` (`crisalid` by default), so that the threshold applies to
a single scale.

Some MCP tools take extra parameters that Sorbobot leaves at their default: `limit` of
`list-concept-expert-evidence` (20000), `min_depth` of `list-person-research-concepts` (2) and `max_keywords` of
`list-document-summaries` (5). The tools return more columns than listed above (for example `type`, `best_similarity`,
`identifiers`, `laboratories`); the code reads only the columns it needs and ignores the others.

An empty toolbox response (`null`) means "nothing found" and must be read as an empty list.

On the first message, the agent must check that the `sorbobot` toolset contains at least the ten tools listed above.
If one is missing, this is a deployment error (§10.3). The deployed toolset may contain other tools (today
`list-concept-children`), which Sorbobot does not use.

### 2.2 The prerequisite: links between documents and topics

The search relies on the `(:Document)-[:HAS_TOPIC {score}]->(:Concept:Topic)` links. These links must have been
created beforehand.

### 2.3 crisalid-taxi

crisalid-taxi (`crisalid-taxi`) maps a text to topics of the OpenAlex taxonomy.

- Call: `POST /api/v1/match/`.
- What is sent: `{"inputs": [{"id": "query", "text": "<subject>"}], "similarity_threshold": 0.56, "max_topics": 100}`.
- What is received: for the `query` input, a list of matches `concept_uid`, `rel_type`, `value`. Only matches with
  `rel_type = "HAS_TOPIC"` are kept; `value` is the question similarity.
- taxi does not process a text shorter than `SORBOBOT_TAXI_MIN_QUERY_CHARS` characters. A query that does not meet this
  length must not be sent.

### 2.4 The LLM

An OpenAI-compatible endpoint, configured by `MODEL`. The same LLM is used by the agent and the judges. There is no
separate language-detection call: the language is determined during the agent's initial call, at the same time as the
understanding of the request and the possible choice of a LangGraph tool (§4.3).

## 3. Architecture

### 3.1 The principle

Sorbobot is not a loop in which the LLM freely chains tools. The LLM only sees the four LangGraph tools, which expose
the business operations. Each one runs a sequence of operations fixed in the code: calls to taxi, to the toolbox, to
the judges, then ranking. The LLM does four things:

1. detect the language of the last user message;
2. choose the LangGraph tool and fill in its parameters;
3. call a LangGraph tool again when its result contains a functional hint that calls for a new attempt (for example a
   subject that is too short);
4. write the answer from the result.

The search algorithms are deterministic for identical inputs and data. Tests can therefore verify them with a fake
LLM, without calling a real model.

### 3.2 The repository components that are reused

- `scripts/create_new_agent.py`: Sorbobot is created with the `dummy` template (a ReAct loop over local LangGraph
  tools, the shape of `agents/project_topic_matching/`), which also wires up both interfaces (OpenWebUI and chat API).
  The `mcp-toolbox` template does not fit: it hands the toolbox tools to the LLM, whereas Sorbobot calls them from
  code.
- `common/registry.py`: Sorbobot is registered like any other agent: `agents/sorbobot/agent.py` exposes
  `create_agent()`. This constructor reads no configuration; everything is read on the first message.
- `common/langgraph_agent.py`: Sorbobot inherits from `LangGraphAgent`, which builds the graph on first use and
  translates its stream into events for the interfaces (answer tokens, tool call, tool result). This class expects an
  LLM node followed by a `ToolNode`: Sorbobot keeps this shape, with its four business tools.
- `common/llm.py`: `build_chat_model()` creates the LLM client from `MODEL`, `API_KEY` and `LLM_API_BASE`, at
  temperature 0. The same client serves language detection, the agent and the judges.
- `common/mcp_toolbox_client.py`: loads the toolset, with Keycloak authentication if configured. The toolset's tools
  are called by the code, never by the LLM.
- `common/tool_calls.py`: `fix_raw_tool_calls` recovers tool calls that a model writes as text instead of using the
  intended protocol. It is wired behind the agent's LLM.

### 3.3 The components specific to Sorbobot

- Taxi client: calls crisalid-taxi (§2.3) and returns the topics sorted in descending order. It receives the URL, the
  threshold, `max_topics`, the minimum length and the timeout.
- MCP tool access: uses the client initialised by `common/mcp_toolbox_client.py` to call the MCP tools of the
  `sorbobot` toolset. It passes the parameters the calls need, applies the maximum allowed timeout and converts the
  responses into lists of rows usable by the search algorithms.
- Topic judge and publication judge: one LLM call each (§6.1, §6.2). They receive the LLM, their prompt and the
  timeout.
- Ranking: computes the researchers' score (§6.3). It only receives data and calls no service.
- Domain expert search (§5.1) and domain exploration (§5.4): receive the taxi client, the MCP tool access component,
  the topic judge and their settings.
- Researcher searches (§5.2, §5.3): receive the MCP tool access component, the publication judge and their settings.
- LangGraph tools for the LLM: the four tools of §5. Each uses its search algorithm and turns the result, or the
  error, into structured JSON. Technical errors carry a stable code; the code intercepts them before the final
  writing.
- i18n service: provides, in the language chosen for the answer, the predefined messages Sorbobot must display
  verbatim, notably the input-control messages and the error messages. It loads the JSON translation catalogues and
  lets the code look up each translation by key.
- Agent: on the first message, reads the configuration, creates all the components above, then builds the graph. For
  every valid message, the initial LLM call determines the answer language and chooses the action to perform. It
  intercepts technical errors before the final writing to display their translation directly. The translated message
  must be produced by the graph's `agent` node: `LangGraphAgent` only forwards to the interfaces the text written by
  that node. When the last tool result carries a technical error code, the `agent` node returns the translated text
  without calling the LLM. It does the same when the LLM is unavailable.

Each component receives only what it needs. The agent is the only place that creates and assembles the others, and no
object gathers all the dependencies in order to distribute them.

## 4. The journey of a request

### 4.1 Step 0: reception

The user writes in OpenWebUI or in an application that uses the chat API. In both cases, the agent receives the whole
conversation as a list of messages, from oldest to most recent. The message to answer is the last user message.

### 4.2 Step 1: input control

Before any LLM call, the code checks the last user message.

- **Empty message or whitespace only**: the code directly returns the translated text `input.empty`, without calling
  the LLM.
- **Message longer than `SORBOBOT_MAX_MESSAGE_CHARS`**: the code directly returns the translated text
  `input.too_long`, without calling the LLM or continuing the processing.

Both direct answers are written in `SORBOBOT_DEFAULT_LANGUAGE`: no LLM has run yet, so no language has been
detected for the turn.

### 4.3 Step 2: language detection and understanding of the request

If the message passed the input control, the LLM is called. During this same call, it determines the answer language,
then analyses the request to decide on the appropriate processing.

#### Determining the language

The LLM determines the answer language following this order of priority:

1. **Language of the previous turn**: if the history makes it possible to identify the answer language of the
   previous turn and that language is supported, it prefers it. This allows answering short messages such as "yes" or
   "ok" in the same language.
2. **Language of the current message**: if no previous answer language can be identified or reused, the LLM detects
   the language of the current message. If it is too short for reliable detection, it may rely on the user's previous
   messages (§8).
3. **Default language**: if neither method identified the language, or if the detected language is not in
   `SORBOBOT_SUPPORTED_LANGUAGES`, the code uses `SORBOBOT_DEFAULT_LANGUAGE`.

How the LLM reports the language: every LangGraph tool (the four tools of §5, not the MCP tools) takes a
`language` parameter (an ISO 639-1 code such as `en` or `fr`) that the LLM fills with the answer language it
determined. When the LLM answers conversationally without
a tool, it simply writes in that language; the code does not need it. The code systematically validates the language
code received in a tool call and applies the fallback rule above (unknown or unsupported code → default language).
The chosen language is used for all answers intended for the user during the current turn, including the translated
messages returned directly by the code in case of a technical error. If no tool was called in the turn when a
technical error occurs (the LLM itself is unavailable on the initial call), the default language is used.

#### Understanding the request

After determining the answer language, the LLM decides how to process the request:

| Case | Expected LLM behaviour |
|---|---|
| Unintelligible text (meaningless sequence of characters) | Explain in one sentence that it did not understand and recall what Sorbobot can do. |
| Out-of-scope question, for example a question about the knowledge graph | State in one sentence that the request is out of scope and recall what Sorbobot can do. |
| Short general definition | Answer directly, without calling a tool, in a few sentences. |
| Attempt to hijack the agent ("ignore your instructions") | Do not follow the hijacking request and recall what Sorbobot can do. |
| Request suited to the agent's capabilities | Choose the appropriate LangGraph tool and prepare its parameters (§4.4). |

These behaviours are defined in the system prompt. When a conversational answer is enough (unintelligible text,
out-of-scope question and short general definition), the LLM answers without calling a LangGraph tool. When a search
is needed, it selects the appropriate LangGraph tool and prepares the required parameters.

### 4.4 Step 3: choosing the LangGraph tool and preparing the parameters

The LLM chooses the tool according to what the question asks, not according to the words it shares with a domain
name.

| The question asks | LangGraph tool |
|---|---|
| Who works on a subject ("who", "which researchers", "experts in")? | `find_domain_experts` |
| What does a named researcher work on? | `get_person_expertise` |
| A summary of a named researcher's work, or a question about their work | `get_researcher_profile` |
| Which domains exist on a subject, what they cover or what they mean | `explore_domains` |

#### Rephrasing the research theme

Before the first call to `find_domain_experts` or `explore_domains`, the LLM rephrases the research theme in English as
a descriptive expression suited to a scientific search. It expands known acronyms and makes overly general subjects
more specific, without inventing a speciality the user did not mention.

The rephrasing must meet the minimum length `SORBOBOT_TAXI_MIN_QUERY_CHARS` (25 by default). This minimum length does
not replace the semantic rephrasing: a subject can exceed this threshold and still be too vague.

Examples:

| User's subject | Subject rephrased in English |
|---|---|
| `NLP` | `natural language processing` |
| `CNN` | `convolutional neural networks` |
| `biology` | `biology and life sciences` |
| `physics` | `physics and physical sciences` |

These examples illustrate the rule; they are not an exhaustive list. If an acronym is ambiguous in context, the LLM
must not arbitrarily assign it a meaning. For `explore_domains`, if the user refers to domains already displayed in
the conversation, the LLM passes their exact names in `domain_names` instead of rephrasing a `topic`.

Once the tool is chosen and the parameters prepared, the LangGraph tool validates the parameters and then runs the
corresponding business algorithm.

### 4.5 Step 4: execution and writing of the answer

The LangGraph tool runs the business algorithm, which may call Taxi, the MCP tools, the judges and the ranking. It
returns a business result or a functional hint as JSON, which the LLM uses to write the answer in the language
determined at the start of the turn.

If a tool returns a technical error, the `agent` node recognises the error code and directly returns the corresponding
translated message, without a new LLM call (§3.3 i18n service).

### 4.6 Follow-ups

The history is used to understand what the user is talking about, never to answer from memory:

- **"yes", "ok" after a proposal from the agent**: do what was proposed, i.e. call the corresponding tool. After an
  expertise, a proposal of a summary calls `get_researcher_profile`, which alone provides the summaries and the
  references.
- **"and #2?", "what does #2 cover?"**: find domain #2 in the previous answer and call the tool with its exact name.
- **"she", "he", "these domains"**: find the person or the domains in the previous turns and call the tool with their
  explicit names.

A researcher, a publication or a domain mentioned in a previous turn is not verified data for the current turn. Any
factual answer about these elements must rely on a tool call made during the current turn.

### 4.7 The full sequence

```text
User
  |-> OpenWebUI or Chat API
  |-> Sorbobot agent: receives the full history
  |-> Step 1: input control by the code
       |-> empty or whitespace-only message
       |     |-> i18n message input.empty in the default language
       |     |-> end of processing, no LLM call
       |-> message too long
       |     |-> i18n message input.too_long in the default language
       |     |-> end of processing, no LLM call
       |-> valid message
       |     |-> Step 2: initial LLM call
       |          |-> detection of the answer language (passed as the
       |          |   `language` parameter of any tool call, validated by the code)
       |          |-> understanding of the request
       |               |-> out-of-scope or unintelligible request
       |               |     |-> conversational answer, no tool
       |               |-> short general definition
       |               |     |-> direct answer, no tool
       |               |-> hijacking attempt
       |               |     |-> ignore the attempt and recall the scope
       |               |-> search needed
       |                    |-> Step 3: choice of a LangGraph tool
       |                    |     |-> preparation and validation of the parameters
       |                    |     |-> rephrasing in English if needed
       |                    |     |-> execution of the business algorithm
       |                    |          |-> possible calls to Taxi, the MCP tools,
       |                    |             the judges and the ranking
       |                    |          |-> business result or functional hint:
       |                    |             JSON passed to the LLM
       |                    |          |-> technical error:
       |                    |             error code recognised by the agent node
       |                    |             and i18n message returned directly
       |                    |-> Step 4: writing of the answer
       |                         |-> the LLM writes from the JSON
       |                            in the chosen language
       |                         |-> in case of a technical error, the code returns
                                   the translated message without a new LLM call
```

## 5. The LangGraph tools exposed to the LLM

Every tool also takes the `language` parameter described in §4.3; it is not repeated below. Every tool result
carries the validated code in a `language` field (next to the fields shown), which the `agent` node reads to display
technical errors in the language of the turn. The `topic_too_short` hint also carries `min_chars`, and the
`person_not_found` hint the `person_name` that was searched.

### 5.1 `find_domain_experts(topic, language)`

**Role.** Find the internal researchers who work on a research theme.

**Parameter.** `topic`: the subject, in English, as a descriptive sentence of at least `SORBOBOT_TAXI_MIN_QUERY_CHARS`
characters.

**Preconditions.** Before calling taxi, the LangGraph tool checks that `topic` contains at least
`SORBOBOT_TAXI_MIN_QUERY_CHARS` characters. If the subject is too short, the code returns the functional hint
`topic_too_short`. The LLM then rephrases the subject and calls the LangGraph tool again. The code never sends taxi a
subject that does not meet this minimum length.

**Algorithm.**

1. Send the theme to taxi. Keep the returned topics, sorted by descending question similarity, and retain only the
   first `SORBOBOT_MAX_SEARCH_TOPICS` (20 by default).
2. For these topics, read in parallel their information (`get-concepts-by-uid`, with the `HAS_TOPIC` link threshold)
   and their paths (`get-concept-hierarchy`).
3. Discard the topics without publications (`nb_docs = 0`). If taxi found topics but none has a publication in the
   graph, stop and return the `no_document` hint.
4. Have the remaining topics scored by the topic judge (§6.1), in a single LLM call. Keep those whose score is at
   least `SORBOBOT_JUDGE_THRESHOLD` (0.6 by default). A topic the judge did not score is discarded. If the judge is
   disabled (`SORBOBOT_JUDGE_THRESHOLD` = 0) or down, keep all the topics of step 3. The "kept topics" below are the
   topics that pass this step, whether judged or not.
5. Read the evidence rows of these topics (`list-concept-expert-evidence`, with the `HAS_TOPIC` link threshold).
6. If fewer than `SORBOBOT_MIN_AUTHORS` distinct researchers (2 by default) appear in the evidence, and at least one
   topic was kept at step 4, widen to the siblings:
   1. read the siblings of the kept topics (`list-concept-siblings`);
   2. read their information and their paths, discard those without publications, and keep only the
      `SORBOBOT_MAX_JUDGED_SIBLINGS` (100 by default) richest in publications;
   3. have them scored by the topic judge, in a single call, on the same question;
   4. if the judge is enabled but its judgement failed, add no sibling;
   5. keep the siblings scored at least `SORBOBOT_JUDGE_THRESHOLD`; if there are none, change nothing. If the judge
      is disabled, keep all the documented siblings of step 6.2 (the widening stays active);
   6. re-read the evidence rows for the topics of step 4 and the kept siblings; if this read returns nothing, keep
      the evidence of step 5.
   One step is enough: the siblings of a sibling are topics already examined.
7. Read the total number of publications of each researcher in the evidence (`get-person-publication-counts`).
8. Rank the researchers (§6.3) and keep the first `SORBOBOT_SHOW_MAX_AUTHORS` (10 by default) for the answer.
9. Build the list of domains to display: first the topics of step 4, in descending order of taxi similarity, then the
   kept siblings, from best to worst judge score; at most `SORBOBOT_SHOW_MAX_DOMAINS` (5 by default). Number them
   `#1`, `#2`…
10. For each expert: the rounded score, the tags of the displayed domains they have published on, their number of
    publications on the subject, their total, and their two most recent publications.

**Output.**

```json
{ "topic": "<the subject sent to taxi>",
  "ranking_method": "weighted_relevance | count",
  "domains": [
    { "tag": "#1",
      "uid": "<topic uid>",
      "path": "<Domain> [Domain] > <Field> [Field] > <SubField> [SubField] > <Topic> [Topic]",
      "nb_publications": "<publications linked to the topic>",
      "definition": "<topic definition, 300 characters at most>" } ],
  "experts": [
    { "person_uid": "<uid>",
      "name": "<display name>",
      "score": "<integer from 0 to 100>",
      "tags": ["#1", "#3"],
      "nb_publications_on_topic": "<publications on the subject>",
      "nb_publications_total": "<all their publications>",
      "recent_publications": [{ "title": "<title>", "year": "<year>" }] } ] }
```

If no researcher is found, the tool returns `{"topic": "<the subject sent to taxi>", "experts": [], "hint": "no_expert"}`.
The LLM explains this result in the language of the turn. Domains are only returned if the tool finds at least one
expert.

**Edge cases.** Functional hints are interpreted by the LLM, which explains the result in the language of the turn.
Technical errors are handled by the code and are not presented as an absence of results.

| Situation | Result |
|---|---|
| subject too short after rephrasing | `topic_too_short` hint; the LLM rephrases then calls the tool again |
| taxi unavailable | technical error `taxi_unavailable`, displayed by the code |
| toolbox unavailable | technical error `graph_unavailable`, displayed by the code |
| topics without any publication | `no_document` hint |
| no researcher | `no_expert` hint |
| judge down | the search continues without it; no sibling is added |

### 5.2 `get_person_expertise(person_name, language)`

**Role.** Give the domains of a named internal researcher, and their publications in those domains.

**Parameter.** `person_name`: the researcher's name, as the user wrote it.

**Algorithm.**

1. Clean the name given by the user: lowercase it (so that `AND`, `OR`, `NOT` are not read as operators), replace
   the full-text search special characters with spaces: `+ - & | ! ( ) { } [ ] ^ " ~ * ? : \ /`, then collapse
   consecutive spaces and trim (the MCP tool splits the name on single spaces to build its Lucene query; an empty
   token would break it). If the cleaned name is empty, return the `person_not_found` hint.
2. Search the name (`search-person-by-name`, 10 results at most). Take the first **internal** researcher, as the most
   likely one. If there is none, return the `person_not_found` hint.
3. Read their topics (`list-person-research-concepts`, with the link threshold, at most `SORBOBOT_SHOW_MAX_DOMAINS`).
4. In parallel, read the paths of these topics and their publications on these topics (`list-person-top-documents`,
   at most `SORBOBOT_SHOW_MAX_PERSON_PUBLICATIONS`, 20).
5. Keep only the publications where the researcher has the author role (`aut`): the MCP tool returns every role, so
   the code filters, and the list may end up shorter than the limit.
6. Merge the duplicates (§6.4).
7. Number the topics `#1`, `#2`… in the returned order (from most to least published).
8. For each publication: its title, its year and the tags of its topics.

**Output.**

```json
{ "person_uid": "<uid>",
  "name": "<display name>",
  "domains": [
    { "tag": "#1", "uid": "<topic uid>", "name": "<topic name>", "path": "<full path>",
      "nb_publications": "<their publications on this topic>", "definition": "<definition, 300 characters at most>" } ],
  "publications": [
    { "title": "<title>", "year": "<year>", "tags": ["#1", "#2"] } ] }
```

**Edge cases.** Name not found: `person_not_found` hint. Toolbox unavailable: technical error `graph_unavailable`,
displayed by the code.

### 5.3 `get_researcher_profile(person_name, question, language)`

**Role.** Gather what is needed to summarise a researcher's work or answer a question about it.

**Parameters.** `person_name`: the name, taken from the question or the conversation. `question`: the user's
question, as they asked it.

**Algorithm.**

1. Steps 1 to 4 of §5.2, but reading up to 60 publications and keeping every role (no author-only filter).
2. Assign each publication a role: a publication can have several roles, so the first one found in the order author,
   thesis supervisor, speaker is kept; otherwise `other`.
3. Merge the duplicates (§6.4) **before** the judge, so that a work is presented and numbered only once.
4. If more than `SORBOBOT_PROFILE_MAX_PUBLICATIONS` publications (20) remain, have the publication judge (§6.2) pick
   the 20 best; otherwise keep them all.
5. Read their abstracts and keywords (`list-document-summaries`). Cut each abstract to 300 characters, on a word
   boundary, appending "…".
6. Order them by role (author, thesis supervisor, speaker, other), then number them `[1]`, `[2]`… in that order.

**Output.**

```json
{ "person_uid": "<uid>",
  "name": "<display name>",
  "question": "<the user's question>",
  "domains": ["<same fields as §5.2>"],
  "publications": [
    { "reference": "[1]",
      "role": "author | thesis_supervisor | speaker | other",
      "title": "<title>", "year": "<year>", "tags": ["#1"],
      "keywords": ["<keyword>", "…"],
      "abstract": "<abstract, 300 characters at most>" } ] }
```

**Edge cases.** Those of §5.2. If the publication judge fails, the first 20 are kept, which are the best linked to the
researcher's domains.

### 5.4 `explore_domains(topic, domain_names, language)`

**Role.** Describe research domains: what they cover and how they differ.

**Parameters.** Exactly one of the two is expected; `domain_names` wins when both are given.

- `topic` (optional): in English, a domain as a sentence of at least `SORBOBOT_TAXI_MIN_QUERY_CHARS` characters.
- `domain_names` (optional): a list of exact names of domains displayed earlier in the conversation, copied as
  displayed (the LLM finds them in the history).

**Algorithm.**

1. With `domain_names`: resolve each name with `search-concept-by-name` (one call per name, in parallel), keep the
   rows of type `Topic`, drop the names that resolve to nothing. If nothing resolves, return the `no_domain` hint.
   Then read in parallel the information and paths of the resolved topics (`get-concepts-by-uid`,
   `get-concept-hierarchy`), without calling taxi or the judge, and keep them in the order the names were given.
2. With `topic` only: apply steps 1 to 4 of §5.1 (taxi, documented topics, judge), then sort the kept topics by
   descending judge score, then by question similarity. With neither parameter: return the `topic_too_short` hint.
3. Keep the first `SORBOBOT_EXPLORE_MAX_DOMAINS` (10 by default), numbered `#1`, `#2`…, with their path and their
   definition cut to 300 characters.

**Output.**

```json
{ "topic": "<the subject sent to taxi, or null when domain names were given>",
  "domains": [
    { "tag": "#1", "uid": "<topic uid>", "name": "<topic name>", "path": "<full path>",
      "nb_publications": "<publications linked to the topic>", "definition": "<definition, 300 characters at most>" } ] }
```

**Edge cases.** Those of §5.1; if no domain is kept, or no name resolves: `no_domain` hint.

## 6. The common rules

### 6.1 The topic judge

- It receives the question and a numbered list of topics, each described by its path.
- It must score each topic from 0 to 1, with two decimals, using the whole scale:

  | Score | The topic is… |
  |---|---|
  | 0.85 to 1 | exactly the subject of the question |
  | 0.60 to 0.84 | a direct part or a close variant of the subject |
  | 0.30 to 0.59 | related, but broader, or only occasionally used for this subject |
  | 0 to 0.29 | unrelated to the subject |

The `SORBOBOT_JUDGE_THRESHOLD` threshold is used to filter and keep the most relevant to the query.

- It must answer with a lone JSON object: `{"scores": [{"id": 1, "score": 0.91}, …]}`.
- Reading the answer must be tolerant, as an LLM does not always follow the format:
  - take the first JSON object of the text, even if surrounded by sentences or a code fence;
  - also accept the form `{"scores": {"1": 0.91}}`;
  - clamp each score between 0 and 1;
  - if the numbering starts at 0, shift the whole answer by one;
  - ignore numbers outside the list.
- One call per step, never several in parallel.
- If it exceeds `SORBOBOT_JUDGE_TIMEOUT_SECONDS`, fails or answers something unreadable, the judgement counts as "no
  judgement"; the search must never fail because of the judge.
- When `SORBOBOT_JUDGE_THRESHOLD = 0`, the code disables both judges. The sibling widening of §5.1 step 6 stays
  active and keeps all the documented siblings within `SORBOBOT_MAX_JUDGED_SIBLINGS`; the publication judge of §5.3
  keeps the first `SORBOBOT_PROFILE_MAX_PUBLICATIONS` publications.

### 6.2 The publication judge

- It receives the question and a numbered list of publications: title, date, roles of the researcher.
- It must score each publication from 0 to 1 on how much it helps answer the question: 0.85 to 1 if it answers it
  directly, 0.60 to 0.84 if it contributes to it, 0.30 to 0.59 if it only touches part of it, below if it does not
  help. For a general summary, it must favour publications that together cover the researcher's different themes
  rather than near-duplicates.
- Same answer format and same reading as §6.1.
- The best-scored publications are kept, in their **original order**. If it fails, the first ones are kept.

### 6.3 Ranking the researchers

Two methods, chosen by `SORBOBOT_RANKING_METHOD`:

- `count`: the number of publications on the subject, scaled to 100 for the best researcher.
- `weighted_relevance` (default): the sum of the weights of the researcher's publications on the subject. Each
  publication weighs according to its closeness to the question, and this weight decreases with its age. This total is
  multiplied by the share it represents in the researcher's whole output, so that rather generalist researchers weigh
  less. The score is scaled to 100.

```text
weight(d) = max over t of [ sim_q(t) × sim_d(d, t) ]  ×  2^(−age(d) / 5)
P(a)      = Σ weight(d), for the publications d of a on the subject
S(a)      = P(a) / (N(a) + 1)
score(a)  = 100 × ( P(a) / max P ) × ( S(a) / max S )
```

- `d`: a publication; `t`: a topic of the search that `d` is linked to; `a`: a researcher.
- `sim_q(t)`: the similarity between the question and the topic, given by taxi.
- `sim_d(d, t)`: the similarity of the link between the publication and the topic.
- `age(d)`: the current year minus the publication year. The weight is halved every five years.
- `N(a)`: the researcher's total number of publications, all subjects combined. The `+ 1` prevents a researcher with a
  single publication from getting the maximum.
- `max P` and `max S`: the largest values among the ranked researchers.

Fallback values:

- a publication without a date takes the median year of the ranked publications;
- a topic without a question similarity (a sibling) takes the lowest similarity given by taxi;
- a researcher whose total is unknown takes their number of publications on the subject as total.

### 6.4 Duplicate publications

The graph sometimes contains two documents for the same work, for example two records of the same book coming from
two catalogues. They must be merged.

- **In the expertise and the profile.** The first row encountered is kept, as it is the best linked to the subject,
  and the topics (and, in the profile, the roles) of its duplicates are added to it, without repetition and in order.
  In the profile, the merge takes place before the publication judge.
- **In the expert ranking.** A work counts only once in the number of publications on the subject, in the weight `P`
  and in the recent publications. For each topic, the best similarity among its duplicates is kept.

### 6.5 Tags and references

- The tags `#1`, `#2`… number the domains of an answer, in the order they are displayed. An expert or a publication
  carries the tags of the displayed domains it is linked to. Tags sort as numbers (`#2` before `#10`).
- The references `[1]`, `[2]`… number the publications of a profile. They are the only references the answer may
  cite.

## 7. The answers

### 7.1 General rules

- Every researcher, publication, title, year, domain, path and number displayed must be copied from the result of a
  tool called during the current turn.
- If the tool returns nothing, say so.
- The conversational answer is written in the language determined at the start of the turn, section headings
  included. The following stay in their original form: people's names, publication titles, taxonomy paths and
  formulas. Predefined messages displayed directly by the code go through the i18n service (§8).
- No institution name: we speak of experts found in the CRISalid knowledge graph.
- The layout is produced by the LLM, from the templates of the system prompt below. The templates are written in
  English in the prompt; the LLM translates them into the detected language.

### 7.2 Experts on a subject

```text
We found experts in the CRISalid knowledge graph on: <subject as the user wrote it>.
Here are the OpenAlex domains closest to the subject (publications in the graph):

#1 <full path> -> <nb_publications> publications
#2 …

Here are the most relevant researchers:

- **<name>** (score <score>) #1 #3
- …

<ranking block>
```

The ranking block explains the method: a bold heading, the formula alone on its line, then one bullet per symbol,
written by the LLM in the user's language. The formulas (for `weighted_relevance` and for `count`) are given in the
prompt and copied verbatim. No list of publications and no closing question.

### 7.3 A researcher's expertise

```text
**Research domains of <name>** (publications in the graph)

#1 <full path> -> <nb_publications> publications
#2 …

**Publications**
- **<title>** (<year>) -> #1 #3
```

The tool only returns publications where the researcher has the author role (`aut`), so every publication of the
result is displayed. The answer ends with the offer of a summary of their work.

### 7.4 A researcher's profile

For a summary:

```text
<name> works mainly on <two or three themes, in plain words>.

**Publications**
- <theme>: <what these publications study, in one sentence> [1] [3]
- …

**Supervised theses**
- <theme>: <what these theses study> [5]

**Talks**
- <theme>: <what these talks present> [7]

**Other contributions**
- <theme>: <what it is about> [9]

**References**
[1] <title> (<year>)
[3] …
```

For a specific question:

```text
<one to three sentences that answer the question, each ending with its references> [2] [4]
```

- For a summary: one sentence on their main research themes, then one bold section per role present, in the order
  publications, supervised theses, talks, others; 1 to 4 bullets per section, which group the publications by theme
  and end with their references.
- For a specific question: 1 to 3 sentences. If the publications do not allow an answer, say so.
- The references are those of the tool result (`[1]`, `[2]`…); no other may be cited.
- For a summary and for a specific question, the answer ends with a bold **References** heading listing every cited
  reference, one per line, in numerical order: `[n] <title> (<year>)`, title and year as the tool returns them. This
  list is not counted in the 220 words.
- An absent role has no section, no heading and no sentence saying there is nothing.
- A supervised thesis or a talk is never presented as a publication of the researcher.
- Domains are named in plain words, without path or tag. 220 words at most.

### 7.5 Domains

```text
<one sentence that answers the question>

**#1 <name>**
<full path>
<one or two sentences that rephrase the definition>
```

For a single domain: no number, 2 to 4 sentences. No outside knowledge, no closing question.

## 8. Internationalisation

Conversational answers are left to the LLM, and the predefined messages that must be displayed verbatim are
translated in the code.

- **Choice of the language.** Follows the language detection process of §4.2.
- **One detection per turn.** The code keeps the chosen language in the context of the current turn and reuses it for
  all messages intended for the user. It must not be stored in a global variable or shared between conversations.
- **Keys, not texts.** Each predefined message has a stable key, for example `input.empty`, `input.too_long` or
  `errors.llm_unavailable`. The code uses the keys and does not depend on the translated text.
- **One catalogue per language.** Two languages are supported: English ('en') and French ('fr'). Their catalogues are
  `agents/sorbobot/locales/en.json` and `agents/sorbobot/locales/fr.json`. English is the default language.
- **Translation in the code.** The messages displayed directly by the application, notably technical errors, are
  translated by the i18n service without asking the LLM to rephrase them. An LLM error must never require another LLM
  call in order to be displayed.
- **Errors and hints for the LLM.** The tools return stable codes and, if needed, parameters. Functional hints, such
  as `topic_too_short` or `no_expert`, may be interpreted by the LLM. Technical errors, such as `taxi_unavailable` or
  `graph_unavailable`, are recognised by the code and displayed with their translation; they are not presented as an
  absence of results.
- **Language of the data.** People's names, publication titles, paths and domain names coming from the graph stay in
  their original form.
- Translated texts may contain variables, for example `{max_chars}`, filled in by the code. The inserted values must
  be escaped or treated as text.
- A translation missing in French falls back to the English translation and is logged. A key missing from the English
  catalogue is a deployment error (§10.3), detected on the first message: no fallback text is written in the code.
- A test checks that each catalogue contains all the keys of the English catalogue. It reports missing translations
  without failing the tests. A missing or invalid JSON file, however, is a deployment error.

## 9. Texts outside the code

| Text | Where it lives |
|---|---|
| system prompt (instructions, tool choice, answer templates) | a markdown file |
| topic judge prompt, publication judge prompt | one markdown file each |
| predefined messages shown directly to the user | the JSON language catalogues (§8) |
| codes and parameters of the errors or hints returned by the tools | in the tools' result JSON |
| tool descriptions (read by the LLM) | the docstrings of the LangGraph tools |
| log messages | in the code |

Translation texts are referenced by their key. All catalogues and prompts are loaded on the first message.

## 10. Error handling

### 10.1 The rule

A timeout for every call, a dedicated error for every service, and a clear message instead of a crash. No automatic
retry towards taxi or the toolbox. The only retries concern the LLM: up to three when it returns an empty answer, and
those the OpenAI client already performs on network errors and 5xx.

### 10.2 The table

| Situation | Detected by | Behaviour | Text |
|---|---|---|---|
| empty message | input control | direct answer in the default language, no LLM | `input.empty` |
| message too long | input control | direct answer in the default language, no LLM | `input.too_long` |
| subject too short before the taxi call | LangGraph tool, before the service call | functional hint to the LLM, which rephrases then calls the tool again | `topic_too_short` |
| taxi unreachable, too slow (15 s) or malformed response | taxi client | direct translated answer, no new LLM generation | code `taxi_unavailable`, key `errors.taxi_unavailable` |
| toolbox unreachable, too slow (30 s) or unreadable response | MCP tool access component | direct translated answer, no new LLM generation | code `graph_unavailable`, key `errors.graph_unavailable` |
| topics found, no linked publication | search (§5.1 step 3) | hint to the LLM; it explains the result without presenting it as a failure | `no_document` |
| name not found | researcher search | hint to the LLM; it explains that the researcher was not found | `person_not_found` |
| no expert | expert search | hint to the LLM | `no_expert` |
| no domain, or no displayed domain name resolves | exploration | hint to the LLM | `no_domain` |
| judge down or too slow (30 s) | judge | no judgement, the search continues | none |
| LLM: empty answer three times | LLM node | direct translated answer, without showing the technical cause; in the turn's language if a tool call already fixed it, otherwise the default language | key `errors.llm_empty_response` |
| LLM unavailable (5xx, 429, 404, network error) | LLM node | direct translated answer, without showing the technical cause; same language rule | key `errors.llm_unavailable` |

### 10.3 Deployment errors

An invalid configuration, a missing or invalid prompt file, a missing or invalid JSON catalogue, or an incomplete
toolset is a deployment error. The agent must not present this error as a business result: it fails the
initialisation and states clearly what must be fixed.

## 11. Asynchrony and lifecycle

- All code that waits on a service is asynchronous.
- **Under OpenWebUI**, each conversation turn runs on a **new** event loop, closed at the end of the turn. No
  loop-bound object may survive from one turn to the next. In particular, the taxi client opens and closes its HTTP
  connection on every call.
- **Under the chat API**, a single loop serves all conversations: several requests may be in progress at the same time
  on the same agent. Nothing request-specific may be stored in the agent.
- **On the first message**, the agent builds its components. If two first messages arrive at the same time, the first
  built result is kept and shared; no asyncio lock may be used, as it would be bound to a single loop.
- **In parallel**: only independent reads from the toolbox (topic information and paths; a researcher's paths and
  publications). Steps that depend on one another stay in order. A single judge call per step.
- **Cancellation** (the user closes the conversation) must propagate: it must not be swallowed by error handling.
- **On close**, the agent releases the toolbox connection.

## 12. Configuration

Settings are read from the environment on the first message. Each search setting is tuned with `SORBOBOT_<NAME>`.

| Variable | Default | Role | Validation |
|---|---|---|---|
| `CRISALID_TAXI_URL` | — | address of crisalid-taxi | **required** |
| `SORBOBOT_MCP_TOOLBOX_URL` | `CRISALID_MCP_TOOLBOX_URL` | address of the toolbox | |
| `SORBOBOT_MCP_TOOLBOX_TOOLSET` | `sorbobot` | toolset to load | |
| `KEYCLOAK_*` | — | toolbox authentication, if it is protected | |
| `SORBOBOT_TAXI_TIMEOUT_SECONDS` | 15 | timeout of a taxi call | > 0 |
| `SORBOBOT_TOOLBOX_TIMEOUT_SECONDS` | 30 | timeout of a toolbox call | > 0 |
| `SORBOBOT_JUDGE_TIMEOUT_SECONDS` | 30 | timeout of a judge call | > 0 |
| `SORBOBOT_TAXI_MAX_TOPICS` | 100 | topics requested from taxi | > 0 |
| `SORBOBOT_TAXI_MIN_QUERY_CHARS` | 25 | minimum length of a subject | > 0 |
| `SORBOBOT_SEMANTIC_THRESHOLD` | 0.56 | minimum question similarity, applied by taxi | between 0 and 1 |
| `SORBOBOT_MAX_SEARCH_TOPICS` | 20 | taxi topics kept | > 0 |
| `SORBOBOT_TOPIC_LINK_THRESHOLD` | 0.57 | minimum similarity between a publication and a topic | between 0 and 1 |
| `SORBOBOT_TOPIC_SOURCES` | `crisalid` | `HAS_TOPIC` sources taken into account, comma-separated; empty keeps every link | – |
| `SORBOBOT_JUDGE_THRESHOLD` | 0.6 | minimum judge score; 0 disables the judges | between 0 and 1 |
| `SORBOBOT_MIN_AUTHORS` | 2 | below this, the expert search widens to the siblings | > 0 |
| `SORBOBOT_MAX_JUDGED_SIBLINGS` | 100 | siblings scored in one call | > 0 |
| `SORBOBOT_RANKING_METHOD` | `weighted_relevance` | or `count` | one of the two |
| `SORBOBOT_SHOW_MAX_AUTHORS` | 10 | experts displayed | > 0 |
| `SORBOBOT_SHOW_MAX_DOMAINS` | 5 | domains displayed | > 0 |
| `SORBOBOT_SHOW_MAX_PERSON_PUBLICATIONS` | 20 | publications of an expertise | > 0 |
| `SORBOBOT_PROFILE_MAX_PUBLICATIONS` | 20 | publications of a profile | > 0 |
| `SORBOBOT_EXPLORE_MAX_DOMAINS` | 10 | domains of an exploration | > 0 |
| `SORBOBOT_MAX_MESSAGE_CHARS` | 1000 | maximum length of a user message | > 0 |
| `SORBOBOT_SUPPORTED_LANGUAGES` | `en,fr` | languages allowed for answers and translations | non-empty list of language codes; each code must have a catalogue |
| `SORBOBOT_DEFAULT_LANGUAGE` | `en` | language used if detection fails or the language is not supported | must belong to `SORBOBOT_SUPPORTED_LANGUAGES` |

Fixed values, not configurable because they do not depend on the deployment: 2 recent publications per expert, 60
publications submitted to the judge for a profile, 300 characters for an abstract or a definition.

An invalid value (wrong type) is a deployment error (§10.3).

## 13. Logging

Each expert search must log at INFO, on one line: the subject, the duration, the number of topics returned by taxi,
of documented topics, of topics kept by the judge, of siblings added, of evidence rows and of experts.

Must also be logged:

- at INFO: the number of topics returned by taxi;
- at WARNING: an unavailable service (with the cause), topics without publications, a judge that is down, too slow or
  unreadable, a judge that did not score every item, a failed LLM call.

API keys, passwords and tokens must never appear in the logs.

## 14. Tests and acceptance criteria

### 14.1 Automated tests

All without network: fake LLM, fake taxi client, fake toolbox, fake judges. At minimum:

- the configuration: reading, conversion, rejection of invalid values;
- the taxi client, against a small local HTTP server: request sent, Topic filtering, sorting, failures (500 error,
  unreadable response, timeout), several requests on successive loops;
- the MCP tool access component: `null` responses, incomplete rows, timeout, missing tool;
- the judges: every answer form of §6.1, judge down;
- the ranking: both methods, the fallbacks, the duplicates;
- each search: judge threshold, unscored topic, topics without publications, siblings kept and rejected, failed
  sibling judgement, homonyms, roles (author-only filter of the expertise, role assignment of the profile),
  duplicates, resolution of displayed domain names (found, not found, non-Topic concept);
- the tools: every row of the table of §10.2;
- the agent: the order of events (tool call announced before its execution), the LLM unavailable, two simultaneous
  first messages on two loops, validation of the `language` parameter and fallback to the default language;
- the texts: every key exists in every catalogue; a missing translation falls back to English; a technical error is
  displayed in the chosen language without a new LLM call.
