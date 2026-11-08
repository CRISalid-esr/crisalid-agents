"""RFC 8693 token exchange: a user's access token for a token accepted by the MCP toolbox.

The chat API receives tokens issued for itself (audience ``crisalid-agents``). The toolbox expects tokens
issued for it (audience ``crisalid-graph-mcp``). Keycloak's standard token exchange lets the confidential
``crisalid-agents`` client, and only it, obtain the second from the first. The exchanged token expires with
the subject token; it is cached per subject token so that one user token costs one Keycloak round trip.
"""

import asyncio
import logging
import os
import time

import httpx
import jwt

logger = logging.getLogger(__name__)

TOKEN_EXCHANGE_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"
ACCESS_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"
DEFAULT_MCP_AUDIENCE = "crisalid-graph-mcp"


class TokenExchangeError(Exception):
    """Keycloak refused the exchange or could not be reached."""


def _unverified_claims(token: str) -> dict:
    try:
        return jwt.decode(token, options={"verify_signature": False})
    except jwt.PyJWTError:
        return {}


class TokenExchanger:
    def __init__(
        self,
        issuer: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        audience: str | None = None,
        ssl_verify: bool | None = None,
        http: httpx.AsyncClient | None = None,
    ):
        self.issuer = (issuer or os.getenv("KEYCLOAK_ISSUER", "")).rstrip("/")
        self.client_id = client_id or os.getenv("KEYCLOAK_CLIENT_ID", "")
        self.client_secret = client_secret or os.getenv("KEYCLOAK_CLIENT_SECRET", "")
        self.audience = audience or os.getenv("MCP_AUDIENCE", DEFAULT_MCP_AUDIENCE)
        ssl_env = os.getenv("KEYCLOAK_SSL_VERIFY", "true").lower() != "false"
        self.ssl_verify = ssl_env if ssl_verify is None else ssl_verify
        self._http = http
        self._cache: dict[str, tuple[str, float]] = {}
        self._lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        return bool(self.issuer and self.client_id and self.client_secret)

    @property
    def token_endpoint(self) -> str:
        return f"{self.issuer}/protocol/openid-connect/token"

    def _evict_expired(self, now: float) -> None:
        for key in [k for k, (_, exp) in self._cache.items() if exp <= now]:
            del self._cache[key]

    async def exchange(self, subject_token: str) -> str:
        claims = _unverified_claims(subject_token)
        key = claims.get("jti") or subject_token
        now = time.time()
        cached = self._cache.get(key)
        if cached and cached[1] > now:
            return cached[0]

        async with self._lock:
            cached = self._cache.get(key)
            if cached and cached[1] > now:
                return cached[0]
            token = await self._request(subject_token)
            exchanged_claims = _unverified_claims(token)
            expires_at = float(exchanged_claims.get("exp") or claims.get("exp") or now + 60)
            self._evict_expired(now)
            self._cache[key] = (token, expires_at)
            return token

    async def _request(self, subject_token: str) -> str:
        data = {
            "grant_type": TOKEN_EXCHANGE_GRANT,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "subject_token": subject_token,
            "subject_token_type": ACCESS_TOKEN_TYPE,
            "audience": self.audience,
        }
        try:
            if self._http is not None:
                response = await self._http.post(self.token_endpoint, data=data)
            else:
                async with httpx.AsyncClient(verify=self.ssl_verify, timeout=10.0) as http:
                    response = await http.post(self.token_endpoint, data=data)
        except httpx.HTTPError as exc:
            raise TokenExchangeError(f"token exchange request failed: {exc!r}") from exc
        if response.status_code != 200:
            logger.warning("token exchange refused (%s): %s", response.status_code, response.text[:200])
            raise TokenExchangeError(f"token exchange refused with status {response.status_code}")
        try:
            return response.json()["access_token"]
        except (ValueError, KeyError) as exc:
            raise TokenExchangeError("token exchange answered without an access_token") from exc
