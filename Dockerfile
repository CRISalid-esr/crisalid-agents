FROM ghcr.io/open-webui/pipelines:main

WORKDIR /app

# Install uv for locked dependency resolution
RUN pip install uv --quiet

# Install project runtime deps from the lock file (layer-cached until pyproject.toml/uv.lock change)
COPY pyproject.toml uv.lock ./
RUN uv export --no-dev --frozen -q > /tmp/requirements.txt && \
    uv pip install --system -r /tmp/requirements.txt --no-cache-dir && \
    rm /tmp/requirements.txt

# Copy project source packages (importable from /app, which is on PYTHONPATH)
COPY common/ ./common/
COPY crisalid_graph_agent/ ./crisalid_graph_agent/

# Install the pipeline into the pipelines directory served by the OpenWebUI runner
COPY openwebui_pipelines/crisalid_graph_agent_pipeline.py ./pipelines/crisalid_graph_agent_pipeline.py

# Declare runtime env vars — values must be injected at deploy time (Docker Compose / K8s).
# No defaults are set here so that a missing variable causes a visible failure rather than
# silent misconfiguration.
ENV MODEL="" \
    API_KEY="" \
    LLM_API_BASE="" \
    CRISALID_MCP_TOOLBOX_URL="" \
    CRISALID_MCP_TOOLBOX_TOOLSET="" \
    KEYCLOAK_ISSUER="" \
    KEYCLOAK_CLIENT_ID="" \
    KEYCLOAK_CLIENT_SECRET="" \
    KEYCLOAK_SSL_VERIFY="true"

# Port and entrypoint are inherited from the base image (ghcr.io/open-webui/pipelines)
