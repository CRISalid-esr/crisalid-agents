"""API key authentication for the chat API.

Same scheme as crisalid-apollo: the ``x-api-key`` header is checked against the
comma-separated ``API_KEYS`` env var, and the check is on unless
``ENABLE_API_KEYS`` is set to ``"false"``.

The chat API is only reachable from the internal Docker network (called by the
sovisuplus backend, never directly from the browser); proper end-user OIDC auth
will be added later. Note the ``KEYCLOAK_*`` env vars are unrelated: they are
outbound-only (service account calling the MCP Toolbox, see
``common.mcp_toolbox_client``).
"""

import os

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

_api_key_header = APIKeyHeader(name="x-api-key", auto_error=False)


async def require_api_key(api_key: str | None = Security(_api_key_header)) -> None:
    if os.getenv("ENABLE_API_KEYS", "true").strip().lower() == "false":
        return
    valid_keys = [k.strip() for k in os.getenv("API_KEYS", "").split(",") if k.strip()]
    if not api_key or api_key not in valid_keys:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: Invalid API Key",
        )
