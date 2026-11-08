# End-user identity from the chat clients to the MCP tools — specification

Branch `crisalid-ai-auth-refacto` in four repositories: `crisalid-agents` (this one), `sovisuplus`,
`crisalid-ai-skills` (MCP toolbox) and `crisalid-deployment`.

## 1. Purpose

The CRISalid agents answer questions over the institutional knowledge graph on behalf of a signed-in person, from
SoVisu+ or from OpenWebUI. Several questions depend on who is asking ("my publications", "the collaborators of my
laboratory"). This specification defines how the identity established at login is carried, verified at each hop and
made available to the MCP tools, so that identity-dependent queries are scoped server-side rather than from
information supplied in the conversation.

It covers:

- authentication of the chat clients (SoVisu+, OpenWebUI) to the `crisalid-agents` chat API with OAuth 2.0 access
  tokens issued by Keycloak;
- delegation from the chat API to the MCP toolbox with RFC 8693 token exchange;
- identity injection in the toolbox queries with *authenticated parameters*;
- the replacement of the OpenWebUI Pipelines server by an OpenAI-compatible endpoint of the chat API, so that one
  `crisalid-agents` service serves both clients;
- the corresponding Keycloak, deployment and documentation changes.

## 2. Current state

| Hop | Mechanism |
|---|---|
| SoVisu+ → chat API | shared `x-api-key`; the signed-in person's name and UID are prepended to the conversation as a `system` message by the SoVisu+ proxy route |
| OpenWebUI → pipelines server | shared `PIPELINES_API_KEY`; the pipeline receives the OpenWebUI user record |
| agents → MCP toolbox | one Keycloak service account per agent service (`client_credentials` grant), audience `crisalid-graph-mcp` |
| MCP toolbox → Neo4j | tools marked `authRequired`; no authenticated parameters |

Two `crisalid-agents` services run in deployment (`crisalid-agents-owui` on the Pipelines base image,
`crisalid-agents-chat-api` on FastAPI), each with its own `AGENTS` list. The OpenWebUI Pipelines project was
deprecated and archived in October 2026 (no further releases).

Keycloak (realm `crisalid-inst`) runs with `KC_HOSTNAME_BACKCHANNEL_DYNAMIC=true`: the `iss` claim of a token is
the URL the token was requested through, so tokens obtained through the internal Docker hostname and tokens obtained
through the public hostname carry different issuers. Access tokens are valid 5 min; the SSO session idles out after
30 min and ends after 10 h.

## 3. Target architecture

```
browser ──(session cookie)──► SoVisu+ (Next.js)      ──► POST /agents/{name}/chat      ┐
                              NextAuth JWT holds        Authorization: Bearer AT(sovisuplus)
                              AT + RT, refreshes                                         │
                                                                                          ▼
browser ──(session cookie)──► OpenWebUI 0.13       ──► POST /v1/chat/completions   crisalid-agents chat API
                              connection auth_type      Authorization: Bearer AT(open-webui)   │ validate JWT
                              = system_oauth                                             │ (iss, aud=crisalid-agents,
                                                                                          │  exp, JWKS), allow list (azp)
                                                                                          │ exchange AT → AT'(aud=crisalid-graph-mcp)
                                                                                          │ ContextVar(AT')
                                                                                          ▼
                                                                                   MCP toolbox (tools-auth.yaml)
                                                                                   header crisalid-keycloak_token: AT'
                                                                                   authRequired + authenticated params
                                                                                          │
                                                                                          ▼
                                                                                        Neo4j
```

Principles:

1. Each client application authenticates its users with Keycloak (authorization code flow) as today. The ID token
   establishes the client's session; the **access token** obtained in the same flow is what the client forwards.
2. Each service verifies a token issued for *it*: the chat API checks `aud = crisalid-agents`, the toolbox checks
   `aud = crisalid-graph-mcp`. A token is never passed through to a service it was not issued for.
3. Only the chat API can obtain toolbox tokens for a user (token exchange by a confidential client). The toolbox is
   not reachable with a client's own token.
4. The identity used in queries comes from token claims injected by the toolbox, never from LLM-visible parameters or
   from the conversation.
5. Non-interactive contexts (batch) keep the service-account grant.
6. One issuer: every service uses the public Keycloak URL; the dynamic backchannel is not used.

### 3.1 Tokens

| Token | Issued to | `aud` | Lifetime | Carried by |
|---|---|---|---|---|
| AT (user) | `sovisuplus` or `open-webui` | `crisalid-agents` (+ `account`) | 15 min (client override) | `Authorization: Bearer` to the chat API |
| AT' (exchanged) | `crisalid-agents` | `crisalid-graph-mcp` | remaining lifetime of AT | `crisalid-keycloak_token` header to the toolbox |
| AT (service account) | `crisalid-agents` | `crisalid-graph-mcp` | realm default | same header, batch only |

Claims used: `sub`, `preferred_username`, `email`, `azp` (client the token was issued to), `exp`, `jti`.

### 3.2 Token lifetime

- The chat API refuses a token with less than `MIN_TOKEN_TTL_SECONDS` (default 120) remaining
  (`401`, `error: token_expiring`), because the exchanged token expires with it and an agent turn (several tool calls,
  LLM judges) may last a couple of minutes. Clients refresh before sending: OpenWebUI refreshes when less than 5 min
  remain; SoVisu+ refreshes when less than 150 s remain, and retries once on `token_expiring`.
- The refresh token is bound to the Keycloak SSO session. When the refresh fails (session idle-out, end of session,
  revocation), SoVisu+ keeps its own session and only the chat degrades: the proxy route answers
  `401 reauthentication_required` and the widget invites the person to sign in again. OpenWebUI handles the
  equivalent case itself.
- The access token lifespan of the `sovisuplus` and `open-webui` clients is set to 15 min (client-level override), so
  that OpenWebUI's refresh-ahead window (5 min) does not trigger a refresh on every request.

### 3.3 Agent visibility per client

The `azp` claim names the Keycloak client the token was issued to. `AGENT_ALLOW_LIST`
(`<client-id>:<agent>,<agent>;<client-id>:<agent>…`) maps each client to the agents it may call:

- chat endpoints answer `403 {"error": "agent_not_allowed"}` outside the client's set;
- a client absent from the list may call no agent;
- `GET /agents` and `GET /v1/models` apply the set when a valid token is present and return every served agent
  otherwise (the listing is not sensitive, and OpenWebUI fetches the model list without a user context in some
  paths);
- an unset or empty variable disables the check (local development only; logged at startup).

`AGENTS` keeps restricting what the process loads; the allow list restricts what each client sees.

## 4. Keycloak configuration (realm `crisalid-inst`)

1. Client `crisalid-agents`: confidential (*Client authentication* on), *Service accounts roles* on (batch),
   *Standard token exchange* on, standard and direct flows off, no redirect URIs.
2. Client `crisalid-graph-mcp`: confidential, every flow off (resource server only; it never authenticates).
3. Client `open-webui`: confidential, standard flow, redirect URI `https://<openwebui-host>/oauth/oidc/callback`
   (local: `http://localhost:8080/oauth/oidc/callback`).
4. Client scope `crisalid-agents-audience` (default type) with an *Audience* mapper → included client audience
   `crisalid-agents`, added to the access token. Default scope of `sovisuplus` and `open-webui`.
5. Client scope `crisalid-graph-mcp-audience` (default type) with an *Audience* mapper → `crisalid-graph-mcp`. Default scope of
   `crisalid-agents` only: it serves both the token exchange (the `audience` request parameter filters the audiences
   granted by the requester's scopes, it cannot add one) and the service account.
6. Access token lifespan override 15 min on `sovisuplus` and `open-webui` (advanced settings of each client).
7. Hostname: the `KC_HOSTNAME_BACKCHANNEL_DYNAMIC` line is removed from both Keycloak compose services (default
   `false`), `KEYCLOAK_INTERNAL_HOST` and the `http://…:8080/realms/…` URLs are removed, and every compose fragment
   uses `KEYCLOAK_ISSUER=${KEYCLOAK_SCHEME}://${KEYCLOAK_HOST}:${KEYCLOAK_PORT}/realms/${KEYCLOAK_REALM}`.
   Resolution of the public hostname from the containers depends on where TLS ends:
   - Keycloak terminates TLS with a certificate for that hostname (local `keycloak-tls` + mkcert): a Docker network
     alias `${KEYCLOAK_HOST}` on the Keycloak service; the certificate matches and traffic stays in the network. The
     mkcert CA must be trusted by the chat API image, the toolbox image and the local OpenWebUI
     (`~/.open-webui/ca-bundle.pem`).
   - TLS ends at a reverse proxy in front of Keycloak (staging/prod): the containers reach the proxy through public
     DNS or `extra_hosts`; an alias on the Keycloak container is not applicable since the issuer is `https://` and
     Keycloak speaks HTTP behind the proxy.

Standard token exchange rules relied upon (Keycloak 26.5): the requester client is confidential with the feature
enabled; the subject token's `aud` contains the requester (`crisalid-agents`); `subject_token_type` is
`urn:ietf:params:oauth:token-type:access_token`; the exchanged token expires with the subject token ("Allow exchange
token expiration after subject token expiration" stays off); no refresh token is requested.

These settings land in `docker/keycloak/config/crisalid-inst.json.template` and `configure_keycloak.sh` (new
variables `OPENWEBUI_KEYCLOAK_CLIENT_SECRET`, `CRISALID_AGENTS_KEYCLOAK_CLIENT_SECRET`). Manual steps are documented
only for realms that already exist (`dev/export-kc-realm.qmd` workflow).

## 5. `crisalid-agents`

### 5.1 Inbound authentication (`chat_api/auth.py`)

`require_user(request) -> UserPrincipal(sub, username, email, client_id, token, exp)`:

- `Authorization: Bearer <JWT>`; signature checked with the realm JWKS
  (`${KEYCLOAK_ISSUER}/protocol/openid-connect/certs`, cached, refetched once on an unknown `kid`);
- `iss == KEYCLOAK_ISSUER`, `aud ∋ CHAT_API_AUDIENCE` (default `crisalid-agents`), `exp − now ≥ MIN_TOKEN_TTL_SECONDS`,
  `alg` in RS256/ES256;
- errors: `401`, `WWW-Authenticate: Bearer error="invalid_token"`, body
  `{"error": "invalid_token" | "token_expiring" | "wrong_audience"}`;
- dependency `PyJWT[crypto]` in the core dependencies; `ENABLE_API_KEYS` / `API_KEYS` are removed.

`GET /health`, `GET /agents` and `GET /v1/models` accept requests without a token (see §3.3).

### 5.2 Allow list (`chat_api/allow_list.py`)

`allowed_agents(principal) -> set[str]`: agents of `principal.client_id` in `AGENT_ALLOW_LIST`, intersected with the
served `AGENTS`. Behaviour as in §3.3.

### 5.3 Token exchange (`common/token_exchange.py`)

`TokenExchanger.exchange(subject_token) -> str`: `POST ${KEYCLOAK_ISSUER}/protocol/openid-connect/token` with
`grant_type=urn:ietf:params:oauth:grant-type:token-exchange`, `subject_token`,
`subject_token_type=urn:ietf:params:oauth:token-type:access_token`, `audience=${MCP_AUDIENCE}`, client
authentication with `KEYCLOAK_CLIENT_ID` / `KEYCLOAK_CLIENT_SECRET`. Async (`httpx.AsyncClient`). Exchanged tokens are
cached per subject token (`jti` → token, until `exp`) so that one user token costs one Keycloak round trip, not one
per tool call.

### 5.4 Request context (`common/user_context.py`)

`current_user: ContextVar[UserPrincipal | None]` and `current_toolbox_token: ContextVar[str | None]`, with a
`bind_user(principal, toolbox_token)` context manager. Agents and their toolbox tools are loaded once per process
(`common/registry.py`); `toolbox_core` resolves the token getter on every tool call and sends it as the
`crisalid-keycloak_token` header, so the getter reads the context variable of the current request. A test verifies
that the value set around `astream` reaches the toolbox header through the LangGraph `ToolNode`.

### 5.5 Toolbox client (`common/mcp_toolbox_client.py`)

Two token providers, chosen at construction:

- `delegated`: reads `current_toolbox_token`; raises a clear error when no request context is bound. Used by every
  agent created through the registry.
- `service_account`: today's `client_credentials` getter. Used by the TRM batch (`trm/sources.py`).

### 5.6 Chat API routes (`chat_api/main.py`)

`POST /agents/{name}/chat` depends on `require_user`, checks the allow list, exchanges the token, wraps
`ndjson_chunks` in `bind_user` and logs `sub`, `azp` and the agent name per turn (never the token). The request and
response formats (MUI X Chat body, NDJSON chunks) are unchanged.

### 5.7 OpenAI-compatible routes (`chat_api/openai_compat.py`)

A second router of the same application, consumed by OpenWebUI:

- `GET /v1/models`: the served agents as models (`id` = agent name), filtered by the allow list when a token is
  present;
- `POST /v1/chat/completions`: `model` = agent name (allow list), `messages` converted with
  `common.messages.message_from_role`; `stream: true` → SSE `data: {chat.completion.chunk}` … `data: [DONE]`;
  `stream: false` → one `chat.completion`;
- tool calls and results rendered inside the assistant text as `<details type="tool_calls">` blocks, as the
  pipeline did. The formatting is extracted from `common/openwebui.py` into `common/tool_rendering.py`.

Both routers share the registry, the auth dependency, the allow list and the token exchange; only the serialisation
differs.

### 5.8 Removals

`openwebui_pipelines/`, `common/openwebui.py` (after extraction), `docker/pipelines.Dockerfile`,
`.openwebui-pipelines/` and the pipelines lines of the install commands, `scripts/debug_openwebui_pipelines.py`,
`BaseAgent.stream` (sync bridge) if nothing else uses it, the OpenWebUI stub of `scripts/create_new_agent.py` and
its template test, `tests/test_openwebui_pipeline.py`. `CLAUDE.md`, `README.md` and `.env.sample` are updated
(`KEYCLOAK_ISSUER`, `CHAT_API_AUDIENCE`, `MCP_AUDIENCE`, `MIN_TOKEN_TTL_SECONDS`, `AGENT_ALLOW_LIST`).

### 5.9 Tests

Offline, as the rest of the suite: JWTs signed with a key generated in the test session and a fake JWKS; a fake
exchange endpoint; the toolbox replaced by a fake that records the headers it receives. Cases: valid token; missing
token; wrong audience; wrong issuer; token expiring; client not in the allow list; agent not allowed; listing with
and without token; context variable reaching the tool call; service-account provider unchanged; OpenAI streaming and
non-streaming responses; `<details>` rendering.

## 6. MCP toolbox (`crisalid-ai-skills`)

- `mcp-toolbox/Dockerfile`: `TOOLBOX_VERSION` moved from `v1.1.0` to the current release (`v1.16.0` at the time of
  writing); the samples are re-run against it (the `number` → `float` alias patched in
  `common/mcp_toolbox_client.py` may become unnecessary with the matching `toolbox-core`).
- `tools-auth.yaml`: `audience: ${MCP_AUDIENCE}` (value `crisalid-graph-mcp`), `authorizationServer: ${KEYCLOAK_ISSUER}`
  (public issuer). Every tool of the served toolsets (`crisalid-restricted`, `sorbobot`, …) carries
  `authRequired: [crisalid-keycloak]`.
- New tool `get-current-person` (toolsets `crisalid-restricted` and `sorbobot`): no LLM-visible parameter; one
  authenticated parameter `username` bound to the `preferred_username` claim
  (`authServices: [{name: crisalid-keycloak, field: preferred_username}]`). The toolbox hides authenticated parameters
  from the manifest, so the LLM cannot supply it. Cypher:
  `MATCH (p:Person)-[:HAS_IDENTIFIER]->(:AgentIdentifier {type: 'local', value: split($username, '@')[0]})`,
  returning uid, names, identifiers and memberships; empty result (not an error) when no Person matches. The
  `split` normalises an eppn (`login@domain`) to the local identifier, the same rule as `stripDomainFromEppn` in
  SoVisu+; the README documents the two as twins.
- Tools about a person keep their explicit `person_uid` parameter; the agents call `get-current-person` first for
  "me/my" questions. No per-tool "mine" variants.
- README and `samples/`: a delegated-token sample (`load_restricted_toolset_delegated.py`) next to the
  service-account one; the Dockerfile exposes `MCP_AUDIENCE`.

## 7. SoVisu+

- `src/app/auth/auth_options.ts`, `jwt` callback: on sign-in, store `account.access_token`,
  `account.refresh_token` and `account.expires_at` in the NextAuth JWT (encrypted cookie). On later calls, when
  `expires_at − now < 150 s`, refresh at `${KEYCLOAK_ISSUER}/protocol/openid-connect/token`
  (`grant_type=refresh_token`) and store the rotated pair; on failure set `token.error = 'RefreshTokenExpired'` and
  drop the tokens without ending the session. The `session` callback exposes `error` only, never the tokens. Cookie
  size is checked against the NextAuth chunk limit with the `authz` context present.
- `src/app/api/chat/route.ts`: reads the access token server-side (`getToken` from `next-auth/jwt`); answers
  `401 {error: 'reauthentication_required'}` when absent; forwards `Authorization: Bearer`; maps an upstream
  `401 token_expiring` to one forced refresh and retry. The identity `system` message (`buildUserContext`) and the
  `body.user` field are removed: "me/my" is resolved by the agents through `get-current-person`. The configured
  system prompt is still prepended.
- `CrisalidAgentsChatClient.streamChat(body, accessToken, signal)`; `CRISALID_AGENTS_API_KEY` removed from
  `.env.sample`, `docs/installation.md` and the deployment environment.
- `aiChatAdapter.ts`: `getUser` / `AiChatUser` removed; `reauthentication_required` shown as "sign in again to use
  the assistant" with a link to the sign-in page; the rest of the application is unaffected.
- Tests: `route.test.ts` (token forwarding, 401 paths, retry), refresh logic of `auth_options`, client test.

## 8. Deployment (`crisalid-deployment`)

- `docker/crisalid-agents/crisalid-agents.yaml`: `crisalid-agents-owui` removed; `crisalid-agents-chat-api` is the
  single running agents service (OpenWebUI's `depends_on` points to it); its environment gains `KEYCLOAK_ISSUER`
  (public), `CHAT_API_AUDIENCE`, `MCP_AUDIENCE`, `MIN_TOKEN_TTL_SECONDS`, `AGENT_ALLOW_LIST` (replacing
  `CRISALID_AGENTS_AGENTS` / `CRISALID_AGENTS_CHAT_API_AGENTS`; `AGENTS` is the union) and loses `ENABLE_API_KEYS`,
  `API_KEYS`, `SOVISUPLUS_CHAT_API_KEY`, `CRISALID_AGENTS_PIPELINES_API_KEY`. `crisalid-agents-batch` (same image,
  run on demand) is unchanged apart from the issuer URL.
- `docker/neo4j-mcp/neo4j-mcp.yaml`: `KEYCLOAK_ISSUER` public, `MCP_AUDIENCE`; image built from the new toolbox
  version.
- `docker/openwebui/openwebui.yaml`: image `ghcr.io/open-webui/open-webui:0.13.x-slim` (exact patch release
  pinned); `ENABLE_OAUTH_ID_TOKEN_COOKIE=false`; `WEBUI_URL`; the OAuth session encryption key variable (exact name
  checked against the 0.13 environment reference); `OPENID_PROVIDER_URL` on the public issuer.
- OpenWebUI admin configuration (manual): connection URL `http://crisalid-agents-chat-api:9100/v1`, auth type
  **System OAuth**, no key, "forward cookies" off; models (agents) configured as today (capabilities off, no system
  prompt); background tasks (title, follow-ups) on a direct LLM model.
- Keycloak: §4.
- Documentation: `ai/configuring-openwebui.qmd` (System OAuth connection, Keycloak clients and scopes, upgrade
  notes; the "internal vs public provider URL" section is removed), `ai/security.qmd` (the new trust chain; the
  pipelines section is removed), `ai/docker-compose.qmd`, `ai/local-workstation.qmd` (Keycloak is required by the
  chat path), `dev/local-tls.qmd` (public issuer inside the containers).

## 9. Delivery order

1. **Keycloak**: realm template and script, local stack without the dynamic backchannel, public hostname resolvable
   from the containers, 15 min lifespan on the two web clients. Done when a `curl` token exchange with a `sovisuplus`
   user token returns a token with `aud: crisalid-graph-mcp`, and the service-account path works through the public issuer.
2. **Toolbox**: binary upgrade, audience variable, `get-current-person`, auth on every served tool. Done when the
   delegated sample resolves the signed-in person and the service-account sample still works.
3. **`crisalid-agents`**: §5. Done when `uv run pytest` passes and a local `curl` with a real token streams an answer
   from both routers.
4. **SoVisu+**: §7.
5. **OpenWebUI** on the local workstation (`DATA_DIR=~/.open-webui uvx --python 3.11 open-webui@latest serve`):
   Keycloak client `open-webui`, System OAuth connection, agents visible and answering with "me" resolved.
6. **Deployment and documentation**, then staging.

## 10. Test plan (local workstation)

1. Stack: `keycloak-tls`, `neo4j`, `neo4j-mcp` (new image); chat API with
   `uv run uvicorn chat_api.main:app --port 9100`.
2. Obtain a `sovisuplus` user token (browser login, read from the NextAuth JWT in a debug route); decode and check
   `aud`, `azp`, `exp`.
3. `curl -H "Authorization: Bearer $AT" localhost:9100/agents/generic_agent/chat` with "list my publications": the
   trace shows `get-current-person` then `list-person-publications` with the resolved uid.
4. Negative cases: no token → 401; wrong audience → 401 `wrong_audience`; 30 s from expiry → 401 `token_expiring`;
   `sovisuplus` token on `sorbobot` → 403 `agent_not_allowed`; `GET /v1/models` lists two agents with that token
   and three without.
5. OpenWebUI: Keycloak login, System OAuth connection, chat; wait past the token lifespan and chat again (refresh
   path); end the Keycloak session and chat again (clean error).
6. SoVisu+: chat widget; wait past the token lifespan (refresh path); revoke the session in the Keycloak admin
   console → "sign in again" in the widget, the rest of SoVisu+ still usable.
7. TRM batch runs with the service account.

## 11. Out of scope

- Authorization inside the graph (hidden structures, SoVisu+ visibility rules): the toolbox knows who asks, not what
  they may see.
- OAuth scopes and consent; a `mcp:tools` scope (`scopesRequired` in the toolbox) may be added later.
- Use of the agents from OpenWebUI API tokens (non-browser sessions).
- The MCP Authorization mode of the toolbox (`mcpEnabled: true`, `Authorization: Bearer`), which would open the
  toolbox to any MCP client holding a Keycloak token; the present design keeps the toolbox reachable only through the
  chat API.

## 12. References

- OpenWebUI Pipelines deprecation: https://github.com/open-webui/pipelines/pull/614
- OpenWebUI connection authentication types (`system_oauth`) and OAuth session refresh: changelog entries 0.9.0,
  0.11.0, 0.12.0 of https://github.com/open-webui/open-webui
- Keycloak standard token exchange: https://www.keycloak.org/securing-apps/token-exchange
- MCP toolbox generic OIDC auth service and authenticated parameters:
  https://github.com/googleapis/genai-toolbox (`docs/en/documentation/configuration/authentication/generic.md`,
  `docs/en/resources/tools/_index.md`)
- MCP authorization: https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization
- RFC 8693, OAuth 2.0 Token Exchange: https://www.rfc-editor.org/rfc/rfc8693
