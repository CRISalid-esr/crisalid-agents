# Prompt — factorize common components (branch `factorize-common-components`)

This file is the implementation prompt for the refactoring carried out on this branch.
It is written to be handed to a coding agent (or a developer) as a self-contained brief.
Open questions at the end must be answered before implementation starts.

---

## 1. Context

Repository: `crisalid-agents` — LangChain / LangGraph agents for the CRISalid ecosystem.

Current layout (relevant parts):

```
common/                 build_chat_model() (OpenAI-compatible LLM), embedding provider
generic_agent/   the one real agent: ReAct loop over MCP Toolbox tools
                        (GenericAgent: ainvoke / invoke / astream / stream / aclose)
neo4j_cypher_agent/     legacy, considered junk — see open question Q6
openwebui_pipelines/    generic_agent_pipeline.py — OpenWebUI Pipelines adapter
chat_api/               FastAPI adapter streaming MUI X Chat NDJSON chunks (port 9100)
docker/                 pipelines.Dockerfile (base ghcr.io/open-webui/pipelines, port 9099)
                        chat-api.Dockerfile  (base python:3.11-slim, port 9100)
scripts/                debug_openwebui_pipelines.py, debug_chat_api.py
tests/                  test_schema_postprocessor.py only
```

Both adapters are hard-wired to `GenericAgent`. Everything a new agent would need
(message conversion, the `str | tool_call | tool_result` event stream, `<details>` rendering
for OpenWebUI, NDJSON chunking, lifespan, auth, Dockerfiles) lives inside those two
adapter files and would have to be copy-pasted for each new agent.

Facts about the runtime that constrain the design:

- The OpenWebUI Pipelines server loads only `*.py` files found at the **top level** of
  `PIPELINES_DIR` (no recursion). A pipeline file whose import fails is moved to
  `PIPELINES_DIR/failed/`. It also supports `type = "manifold"` pipelines: one file that
  exposes several models via a `pipelines` list.
- `pipe()` runs in a threadpool thread with no event loop, hence the sync `stream()` bridge.
- The chat-api extra (fastapi, uvicorn, python-dotenv) must stay out of core dependencies:
  the pipelines image must not override its base image's fastapi/uvicorn.
- Project conventions (CLAUDE.md): LangGraph graphs are built manually with `StateGraph`
  + `ToolNode` (never `create_react_agent`); no file-path comments at the top of files.
- `uv sync` wipes the extra `.openwebui-pipelines/requirements.txt` install; re-run
  `uv pip install -r .openwebui-pipelines/requirements.txt` after any `uv sync/add/remove`.

Local verification environment available while working on this branch:

- Neo4j is up via the CRISalid deployment docker stack.
- The MCP Toolbox server is up **without authentication** (Keycloak is down):
  `npx @toolbox-sdk/server --config tools.yaml` from `~/code/crisalid-ai-skills/mcp-toolbox`.
  The `KEYCLOAK_*` lines in `.env` must therefore stay disabled so the toolbox client does not
  try to fetch a token.

## 2. Goal

Other teams must be able to add a new agent (e.g. `sorbobot`, `ptr`) by writing **only
LangChain/LangGraph code and business logic**. They must not copy the OpenWebUI pipeline
boilerplate nor the FastAPI script. We keep exactly **two outputs** (OpenWebUI pipeline,
FastAPI chat endpoint) and **two Docker images**, both generic over the set of agents.

Deliverables:

1. A shared agent contract and the shared streaming/adaptation code, in `common/`.
2. Generic adapters: the OpenWebUI pipeline layer and the chat API no longer import a
   specific agent; they discover agents through a registry.
3. `generic_agent` migrated onto the contract with no behaviour change.
4. A `dummy_agent` — minimal, heavily commented LangGraph agent used as the reference
   example for other teams and as the offline test fixture.
5. A `scripts/create_new_agent.py` scaffolding script with a few options.
6. Generic Dockerfiles, updated CI matrix if needed, tests, README and CLAUDE.md updates.

## 3. Target layout

```
common/
  llm.py                 unchanged
  embedding.py           unchanged
  agent.py               BaseAgent contract + event types (ToolCall / ToolResult)
  langgraph_agent.py     LangGraphAgent: generic MessagesState graph -> event stream
  registry.py            agent discovery: name -> factory
  messages.py            role/content dicts -> LangChain BaseMessage list (shared by both adapters)
agents/                  one sub-package per agent (see Q1 for whether to move here)
  dummy_agent/
    __init__.py
    agent.py             build_graph() + create_agent()  (~60 lines, commented)
    system_prompt.md
    README.md
  generic_agent/  moved from the top level, contents unchanged except the new contract
openwebui_pipelines/
  _crisalid_pipeline.py  shared Pipeline base: message conversion, <details> rendering,
                         spinner status events, on_shutdown (NOT loaded as a pipeline: see Q3)
  generic_agent_pipeline.py    3-line stub -> make_pipeline("generic_agent")
  dummy_agent_pipeline.py             3-line stub
chat_api/
  main.py                generic app; agent(s) resolved through the registry
  streaming.py           event stream -> MUI X Chat NDJSON chunks (moved out of main.py)
  auth.py                unchanged
docker/
  pipelines.Dockerfile   COPY common/ agents/ openwebui_pipelines/*.py ; no per-agent ENV list
  chat-api.Dockerfile    COPY common/ agents/ chat_api/
scripts/
  create_new_agent.py
  templates/agent/...    string.Template files used by the scaffolder (no new dependency)
tests/
  test_registry.py, test_langgraph_agent.py, test_openwebui_pipeline.py, test_chat_api.py
  (all offline: dummy agent driven by a LangChain fake chat model)
specs/factorize-common-components/prompt.md   this file
```

## 4. Agent contract (`common/agent.py`)

What the two adapters depend on, and nothing more:

```python
@dataclass
class ToolCall:   id: str; name: str; args: dict
@dataclass
class ToolResult: id: str; name: str; args: dict; result: str | dict | list

AgentEvent = str | ToolCall | ToolResult     # str = one token of the final answer

class BaseAgent(ABC):
    name: str                 # registry key, url slug, pipeline id
    display_name: str         # shown in OpenWebUI model list / FastAPI title
    description: str = ""

    @abstractmethod
    async def astream(self, messages: list[BaseMessage]) -> AsyncIterator[AgentEvent]: ...

    async def ainvoke(self, messages) -> AIMessage      # default: concatenates str events
    def invoke(self, messages) -> AIMessage             # default: asyncio.run(ainvoke)
    def stream(self, messages) -> Iterator[AgentEvent]  # default: the existing new_event_loop bridge
    async def aclose(self) -> None                      # default: no-op
```

Using dataclasses instead of the current `dict["type"]` items keeps the adapters simple and
lets tests assert on types. `generic_agent.astream` currently yields dicts; migrate it.

## 5. `LangGraphAgent` (`common/langgraph_agent.py`)

The ~100 lines of `GenericAgent.astream` that translate `graph.astream(stream_mode=
["messages","updates"], version="v2")` into events are generic to any `MessagesState` graph
with an `agent` node and a `tools` node. Move them here:

```python
class LangGraphAgent(BaseAgent):
    def __init__(self, name, display_name, graph_builder: Callable[[], Awaitable[CompiledStateGraph]],
                 *, agent_node="agent", tools_node="tools",
                 tool_result_postprocess_node: str | None = None,
                 hide_arg_suffixes: tuple[str, ...] = ())
```

- Lazy build on first use (current behaviour), cached compiled graph.
- `tool_result_postprocess_node`: when set, tool results are buffered after the tools node and
  flushed (with content overridden) after that node completes — this is what
  `postprocess_schema` needs today. When unset, tool results are emitted as soon as the tools
  node completes.
- `hide_arg_suffixes=("_vector",)` replaces the hard-coded `_vector` stripping in the stream.
- Keep the `[TOOL_CALLS]` token-suppression workaround for Mistral-via-vLLM here (comment it
  as a provider quirk; it is harmless for other models).
- Agents that are not LangGraph-shaped (a plain LCEL chain, an external API…) subclass
  `BaseAgent` directly and only implement `astream`.

## 6. Registry (`common/registry.py`)

- Each agent package exposes a module-level `create_agent() -> BaseAgent` in `agent.py`.
- `registry.available_agents() -> list[str]`: scans `agents/*/agent.py` (see Q2 for the
  alternative — Python entry points — if agents will live in separate repositories).
- `registry.get_agent(name) -> BaseAgent`: imports lazily, instantiates once, caches.
- `AGENTS` env var (comma-separated, default: all discovered) restricts what an image serves.
- `DEFAULT_AGENT` env var (default: `generic_agent`) — used by the chat API `/chat`
  route for backward compatibility.
- Import failure of one agent must not take the whole registry down: log and skip, surface the
  error in `GET /agents` and in the pipeline `failed/` mechanism respectively.

## 7. Adapters

### OpenWebUI (`openwebui_pipelines/`)

- Shared module with `make_pipeline(agent_name) -> type[Pipeline]` (or a base class with
  `agent_name` class attribute). It holds everything that is in the current pipeline file:
  `to_langchain_messages` (from `common/messages.py`), `_tool_result_block`, truncation
  constants, spinner `status` events, `on_shutdown`.
- Per-agent stub file at the top level of `openwebui_pipelines/` (generated by the scaffolder):

  ```python
  from openwebui_pipelines._crisalid_pipeline import make_pipeline
  Pipeline = make_pipeline("dummy_agent")
  ```

  The stub is the per-agent OpenWebUI model. Alternative: one manifold pipeline listing all
  registered agents (zero files per agent) — see Q3.
- The shared module must not itself be loaded as a pipeline: check how the loader treats a
  leading underscore / a file with no `Pipeline` class; otherwise place it under
  `common/openwebui.py` instead.
- Import path: stubs import from the project root (`PYTHONPATH` already includes it in
  `start.sh` usage, the debug script and the Docker image).

### Chat API (`chat_api/`)

- `POST /chat` keeps its current contract and serves `DEFAULT_AGENT` (crisalid-apollo calls it).
- `POST /agents/{name}/chat` serves any registered agent; 404 on unknown name.
- `GET /agents` lists `{name, display_name, description}` (auth-protected like `/chat`).
- `GET /health` unchanged.
- Lifespan: instantiate agents lazily on first request or eagerly for `AGENTS`; `aclose()` all
  on shutdown.
- NDJSON translation moves to `chat_api/streaming.py`, typed against `AgentEvent`.

## 8. Dummy agent (`agents/dummy_agent/`)

Purpose: the smallest complete example and the offline test fixture.

- `StateGraph(MessagesState)` with `agent` and `tools` nodes, conditional edge, manual build
  (project convention).
- One trivial local tool (e.g. `get_current_date` or `count_words`) so tool_call /
  tool_result events are exercised end-to-end in OpenWebUI and the chat API.
- LLM from `common.llm.build_chat_model()`; `create_agent(llm=None)` accepts an injected model
  so tests pass a `GenericFakeChatModel` / `FakeMessagesListChatModel`.
- `system_prompt.md` next to the code, loaded with `Path(__file__).parent`.
- `README.md` explaining, in ~30 lines, what a team must write and what it gets for free.
- Comments in `agent.py` explain each LangGraph piece (state, nodes, edges, ToolNode).

## 9. Scaffolder (`scripts/create_new_agent.py`)

```
uv run python scripts/create_new_agent.py <name> [--display-name "..."] [--description "..."]
      [--template dummy|mcp-toolbox] [--no-openwebui] [--force]
```

- `<name>`: snake_case identifier, validated (`^[a-z][a-z0-9_]*$`), must not already exist.
- `--template dummy` (default): copy of the dummy agent with names substituted.
- `--template mcp-toolbox`: ReAct agent over an MCP Toolbox toolset using
  `MCPToolboxClient` (env vars `<NAME>_MCP_TOOLBOX_URL` / `_TOOLSET`, falling back to the
  CRISALID ones) — for teams whose agent is "generic_agent with another prompt/toolset".
- Generates: `agents/<name>/{__init__.py, agent.py, system_prompt.md, README.md}`,
  `openwebui_pipelines/<name>_pipeline.py` (unless `--no-openwebui`), a
  `tests/test_<name>.py` smoke test using the fake LLM.
- Prints the next steps (env vars to set, how to run both adapters, URL of the new route).
- Uses `string.Template` over files in `scripts/templates/`; standard library only.
- Idempotent and safe: refuses to overwrite without `--force`; never touches existing agents.

## 10. Docker and CI

- Two images, unchanged bases and ports. Each copies `common/`, `agents/` and its adapter;
  the pipelines image copies `openwebui_pipelines/*.py` into `./pipelines/`.
- Drop the per-agent `ENV` lists: declare only the adapter-level vars (`AGENTS`,
  `DEFAULT_AGENT`, `ENABLE_API_KEYS`, `API_KEYS`) with a comment that agent-specific vars are
  injected at deploy time. The `.env.sample` documents them per agent section.
- CI matrix unchanged (two images); pytest job now runs the new offline tests.
- `.dockerignore`: keep excluding `specs/`, `scripts/`, `tests/`, `*.md` except the agents'
  `system_prompt.md` files (they are runtime assets — adjust the ignore rule or rename them).

## 11. Tests (offline, `uv run pytest`)

- registry: discovery, `AGENTS` filtering, unknown name, import error isolation.
- `LangGraphAgent`: with the dummy agent + fake LLM scripted to call the tool once, assert the
  event sequence `ToolCall, ToolResult, str…`; assert `ainvoke()` returns the final text;
  assert `stream()` works from a thread without an event loop.
- OpenWebUI pipeline: `pipe()` yields the `<details>` block and the final `stop` chunk; the
  truncation logic is covered.
- Chat API: `fastapi.testclient`, `POST /chat` and `/agents/dummy_agent/chat` produce a valid
  NDJSON sequence `start, tool-input-available, tool-output-available, text-start,
  text-delta…, text-end, finish`; 401 without key; 404 unknown agent.
- Existing `test_schema_postprocessor.py` keeps passing after the move.

## 12. Live verification (after tests pass)

1. `uv sync --extra chat-api && uv pip install -r .openwebui-pipelines/requirements.txt`
2. `uv run uvicorn chat_api.main:app --port 9100` — curl `/health`, `/agents`, `/chat` with a
   CRISalid question (hits Neo4j through the MCP Toolbox), `/agents/dummy_agent/chat`.
3. `uv run python scripts/debug_openwebui_pipelines.py` — check the startup log lists both
   pipelines and nothing landed in `openwebui_pipelines/failed/`; `curl :9099/models`.
4. `docker build` both images from the repo root.
5. `uv run python scripts/create_new_agent.py sorbobot --description "…"`, then repeat 2–3 and
   confirm `sorbobot` appears in both adapters with zero manual edits. Delete it afterwards
   (or keep as a second example only if the user wants).

## 13. Constraints

- No behaviour change for `generic_agent` and for `POST /chat` (apollo contract).
- Core dependencies unchanged (`toolbox-langchain` stays core; nothing added for the scaffolder).
- Small commits, one concern each, short messages, no trailers (user's git style).
- Do not touch `neo4j_cypher_agent` beyond what Q6 decides.
- Do not commit `.env`; do not enable the `KEYCLOAK_*` lines.

## 14. Decisions (answered 2026-09-05, implemented on this branch)

- **Q1 Layout** — `generic_agent/` moved under `agents/`; `agents/` is the single discovery root.
- **Q2 Location of other teams' agents** — same repository; registry scans `agents/*/agent.py`.
- **Q3 OpenWebUI exposure** — one 3-line stub per agent (`Pipeline = make_pipeline("<name>")`).
- **Q4 Chat API routing** — `POST /chat` removed; `POST /agents/{name}/chat` + `GET /agents`.
  The caller is the sovisuplus backend (not crisalid-apollo); it must switch to the new route.
- **Q5 Deployment granularity** — two images, each shipping every agent; `AGENTS` restricts per deployment.
- **Q6 `neo4j_cypher_agent`** — deleted, with the disabled pipeline and the `langchain-neo4j` dependency.
- **Q7 Scaffolder templates** — `dummy` and `mcp-toolbox`.
- **Q8 Naming** — `common/` kept as the framework package; inheritance used where it removes boilerplate
  (`BaseAgent` → `LangGraphAgent` → `MCPToolboxAgent` → `GenericAgent`), nothing more.

Deviations from the design above, decided during implementation:

- The shared OpenWebUI code lives in `common/openwebui.py`, not in `openwebui_pipelines/`: the Pipelines server
  loads every top-level `*.py` of its directory as a pipeline, and the Docker image only copies the stubs.
- `MCPToolboxClient` moved to `common/` since `MCPToolboxAgent` and every `mcp-toolbox` agent use it.
- `LangGraphAgent` is subclassed (`build_graph()` method) rather than given a builder callable: one class per agent
  reads better for the teams writing them.
- `MCPToolboxAgent` always has a `postprocess_tools` node calling the `postprocess_tool_message()` hook (no-op by
  default), applied to the tool messages of the last tools step only.
- The dummy agent is the rendering of the `dummy` template; `tests/test_create_new_agent.py` fails if they drift.
- `pytest-asyncio` added to the dev group; `tests/fake_llm.py` provides a scripted chat model supporting tool calls
  and token streaming, so the whole suite runs offline.

- **Rename (2026-09-05)** — `crisalid_graph_agent` renamed `generic_agent` (`GenericAgent`, display name "Generic agent",
  stub `generic_agent_pipeline.py`); the OpenWebUI model id becomes `generic_agent_pipeline`.

- **Graph ownership (2026-09-05)** — `MCPToolboxAgent` removed: the ReAct loop, retry, semantic embedding and tool
  post-processing moved back into `agents/generic_agent/agent.py`, so the agent owns its LangGraph logic. `common/`
  keeps only agent-agnostic pieces (`LangGraphAgent` streaming, `MCPToolboxClient`, embedding provider,
  `common/tool_calls.py`). The `mcp-toolbox` template is a copy of that graph with a no-op post-processing hook.
