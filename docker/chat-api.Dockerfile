# FastAPI chat API wrapper image (MUI X Chat NDJSON streaming, one route per agent under agents/).
# Build from the repository root: docker build -f docker/chat-api.Dockerfile -t crisalid-agents-chat-api .
FROM python:3.11-slim

WORKDIR /app

# WeasyPrint (PDF reports of the topic-to-researcher batch) needs Pango/Cairo and a font
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libcairo2 fonts-dejavu-core \
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
#   ENABLE_API_KEYS / API_KEYS   inbound auth (x-api-key header); the service must only be
#                           exposed on the internal Docker network
#   MODEL / API_KEY / LLM_API_BASE                     LLM endpoint
#   CRISALID_MCP_TOOLBOX_URL / CRISALID_MCP_TOOLBOX_TOOLSET, KEYCLOAK_*, EMBEDDING_*   see README
#   HORIZON_WP_DIR / HORIZON_OS_URL / HORIZON_OS_INDEX / HORIZON_SEARCH_PIPELINE   Horizon topic index
#   TRM_MODEL / TRM_* / TRM_OUTPUT_DIR                  topic-to-researcher batch (see README)
ENV AGENTS="" \
    ENABLE_API_KEYS="true"

EXPOSE 9100

CMD ["uvicorn", "chat_api.main:app", "--host", "0.0.0.0", "--port", "9100"]
