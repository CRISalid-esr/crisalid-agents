# OpenWebUI Pipelines wrapper image: serves every agent under agents/ as an OpenWebUI model.
# Build from the repository root: docker build -f docker/pipelines.Dockerfile -t crisalid-agents-owui .
FROM ghcr.io/open-webui/pipelines:main

WORKDIR /app

# Install uv for locked dependency resolution
RUN pip install uv --quiet

# Install project runtime deps from the lock file (layer-cached until pyproject.toml/uv.lock change).
# No extras: the chat-api extra (fastapi/uvicorn) must not override the base image's own versions.
COPY pyproject.toml uv.lock ./
RUN uv export --no-dev --frozen -q > /tmp/requirements.txt && \
    uv pip install --system -r /tmp/requirements.txt --no-cache-dir && \
    rm /tmp/requirements.txt

# Project source packages (importable from /app, which is on PYTHONPATH)
COPY common/ ./common/
COPY agents/ ./agents/

# One two-line stub per agent, served from the pipelines directory of the OpenWebUI runner
COPY openwebui_pipelines/*.py ./pipelines/

# Runtime configuration is injected at deploy time (Docker Compose / K8s); no defaults are set
# here so that a missing variable causes a visible failure rather than silent misconfiguration.
#   AGENTS                  comma-separated agents to serve (default: all under agents/)
#   MODEL / API_KEY / LLM_API_BASE                     LLM endpoint
#   CRISALID_MCP_TOOLBOX_URL / CRISALID_MCP_TOOLBOX_TOOLSET, KEYCLOAK_*, EMBEDDING_*   see README
ENV AGENTS=""

# Port and entrypoint are inherited from the base image (ghcr.io/open-webui/pipelines)
