# Chat API image: every agent under agents/, served to SoVisu+ (MUI X Chat NDJSON) and OpenWebUI (OpenAI-compatible).
# Build from the repository root: docker build -f docker/chat-api.Dockerfile -t crisalid-agents-chat-api .
FROM python:3.11-slim

WORKDIR /app

# WeasyPrint (PDF reports of the topic-to-researcher batch) needs Pango/Cairo and a font
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libcairo2 fonts-dejavu-core ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv for locked dependency resolution
RUN pip install uv --quiet

# Install project runtime deps + the chat-api extra (fastapi, uvicorn) + the topic-matching extra
# (opensearch-py, pypdf: Horizon topic index ingestion and search) + the trm extra (jinja2, weasyprint:
# topic-to-researcher batch reports)
COPY pyproject.toml uv.lock ./
RUN uv export --no-dev --frozen --extra chat-api --extra topic-matching --extra trm -q > /tmp/requirements.txt && \
    uv pip install --system -r /tmp/requirements.txt --no-cache-dir && \
    rm /tmp/requirements.txt

# Project source packages
COPY common/ ./common/
COPY agents/ ./agents/
COPY chat_api/ ./chat_api/
COPY trm/ ./trm/
# Batch entry points (Horizon topic index ingestion, topic-to-researcher matching) run with
# `docker compose exec … python scripts/<name>.py`
COPY scripts/ ./scripts/

# Runtime configuration is injected at deploy time (Docker Compose / K8s); no defaults are set
# here so that a missing variable causes a visible failure rather than silent misconfiguration.
#   AGENTS                  comma-separated agents to serve (default: all under agents/)
#   AGENT_ALLOW_LIST        agents each client application (azp claim) may call
#   KEYCLOAK_ISSUER / CHAT_API_AUDIENCE / MIN_TOKEN_TTL_SECONDS   inbound bearer-token verification
#   KEYCLOAK_CLIENT_ID / KEYCLOAK_CLIENT_SECRET / MCP_AUDIENCE    token exchange for the MCP toolbox
#   MODEL / API_KEY / LLM_API_BASE                     LLM endpoint
#   CRISALID_MCP_TOOLBOX_URL / CRISALID_MCP_TOOLBOX_TOOLSET, EMBEDDING_*   see README
#   HORIZON_WP_DIR / HORIZON_OS_URL / HORIZON_OS_INDEX / HORIZON_SEARCH_PIPELINE   Horizon topic index
#   TRM_MODEL / TRM_* / TRM_OUTPUT_DIR                  topic-to-researcher batch (see README)
# A CA bundle mounted at /usr/local/share/ca-certificates/ is trusted at start (local Keycloak with a
# locally issued certificate, see the deployment documentation).
ENV AGENTS=""

EXPOSE 9100

COPY docker/chat-api-entrypoint.sh /usr/local/bin/chat-api-entrypoint.sh
RUN chmod +x /usr/local/bin/chat-api-entrypoint.sh

ENTRYPOINT ["chat-api-entrypoint.sh"]
CMD ["uvicorn", "chat_api.main:app", "--host", "0.0.0.0", "--port", "9100"]
