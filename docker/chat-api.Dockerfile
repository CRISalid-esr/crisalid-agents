# FastAPI chat API wrapper image (MUI X Chat NDJSON streaming endpoint).
# Build from the repository root: docker build -f docker/chat-api.Dockerfile -t crisalid-agents-chat-api .
FROM python:3.11-slim

WORKDIR /app

# Install uv for locked dependency resolution
RUN pip install uv --quiet

# Install project runtime deps + the chat-api extra (fastapi, uvicorn)
COPY pyproject.toml uv.lock ./
RUN uv export --no-dev --frozen --extra chat-api -q > /tmp/requirements.txt && \
    uv pip install --system -r /tmp/requirements.txt --no-cache-dir && \
    rm /tmp/requirements.txt

# Copy project source packages
COPY common/ ./common/
COPY crisalid_graph_agent/ ./crisalid_graph_agent/
COPY chat_api/ ./chat_api/

# Declare runtime env vars — values must be injected at deploy time (Docker Compose / K8s).
# No defaults are set here so that a missing variable causes a visible failure rather than
# silent misconfiguration.
# Inbound auth: API_KEYS / ENABLE_API_KEYS (same scheme as crisalid-apollo); the service
# must only be exposed on the internal Docker network.
# KEYCLOAK_* vars are outbound-only (service account calling the MCP Toolbox).
ENV MODEL="" \
    API_KEY="" \
    LLM_API_BASE="" \
    CRISALID_MCP_TOOLBOX_URL="" \
    CRISALID_MCP_TOOLBOX_TOOLSET="" \
    ENABLE_API_KEYS="true" \
    API_KEYS="" \
    KEYCLOAK_ISSUER="" \
    KEYCLOAK_CLIENT_ID="" \
    KEYCLOAK_CLIENT_SECRET="" \
    KEYCLOAK_SSL_VERIFY="true"

EXPOSE 9100

CMD ["uvicorn", "chat_api.main:app", "--host", "0.0.0.0", "--port", "9100"]
