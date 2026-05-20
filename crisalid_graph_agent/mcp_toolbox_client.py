import os
import time
from typing import Callable

import httpx
from toolbox_core.protocol import Protocol
from toolbox_langchain import ToolboxClient


class MCPToolboxClient:
    def __init__(
        self,
        toolbox_url: str | None = None,
        toolset_name: str | None = None,
        keycloak_issuer: str | None = None,
        keycloak_client_id: str | None = None,
        keycloak_client_secret: str | None = None,
        keycloak_ssl_verify: bool | None = None,
    ):
        self.toolbox_url = toolbox_url or os.getenv("CRISALID_MCP_TOOLBOX_URL", "http://127.0.0.1:5000")
        self.toolset_name = toolset_name or os.getenv("CRISALID_MCP_TOOLBOX_TOOLSET", "crisalid-restricted")

        self._keycloak_issuer = keycloak_issuer or os.getenv("KEYCLOAK_ISSUER", "")
        self._keycloak_client_id = keycloak_client_id or os.getenv("KEYCLOAK_CLIENT_ID", "")
        self._keycloak_client_secret = keycloak_client_secret or os.getenv("KEYCLOAK_CLIENT_SECRET", "")
        ssl_env = os.getenv("KEYCLOAK_SSL_VERIFY", "true").lower()
        self._keycloak_ssl_verify = keycloak_ssl_verify if keycloak_ssl_verify is not None else ssl_env != "false"

        self._client: ToolboxClient | None = None
        self._tools = None

    def _auth_configured(self) -> bool:
        return bool(self._keycloak_issuer and self._keycloak_client_id and self._keycloak_client_secret)

    def _make_token_getter(self) -> Callable[[], str]:
        issuer = self._keycloak_issuer
        client_id = self._keycloak_client_id
        client_secret = self._keycloak_client_secret
        ssl_verify = self._keycloak_ssl_verify
        cache: dict = {"token": None, "expires_at": 0.0}

        def get_token() -> str:
            now = time.time()
            if cache["token"] and now < cache["expires_at"] - 30:
                return cache["token"]
            response = httpx.post(
                f"{issuer}/protocol/openid-connect/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
                verify=ssl_verify,
            )
            response.raise_for_status()
            data = response.json()
            cache["token"] = data["access_token"]
            cache["expires_at"] = now + data.get("expires_in", 300)
            return cache["token"]

        return get_token

    async def aload_tools(self):
        if self._tools is not None:
            return self._tools

        self._client = ToolboxClient(
            self.toolbox_url,
            protocol=Protocol.MCP_LATEST,
        )

        await self._client.__aenter__()

        kwargs = {}
        if self._auth_configured():
            kwargs["auth_token_getters"] = {"crisalid-keycloak": self._make_token_getter()}

        self._tools = await self._client.aload_toolset(self.toolset_name, **kwargs)

        print(
            f"Loaded {len(self._tools)} Toolbox tool(s) "
            f"from toolset {self.toolset_name!r}: "
            f"{[tool.name for tool in self._tools]}"
        )

        return self._tools

    async def aclose(self):
        if self._client is not None:
            await self._client.__aexit__(None, None, None)
            self._client = None
            self._tools = None
