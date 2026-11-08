"""Bearer-token authentication of the chat API.

Clients (SoVisu+, OpenWebUI) send the signed-in person's Keycloak access token as
``Authorization: Bearer <JWT>``. The token is verified with the realm's JSON Web Key Set
(``<KEYCLOAK_ISSUER>/protocol/openid-connect/certs``, cached by PyJWT and refetched on an unknown key id):
signature (RS256/ES256), ``iss`` = ``KEYCLOAK_ISSUER``, ``aud`` ∋ ``CHAT_API_AUDIENCE``, ``exp``. A token
with less than ``MIN_TOKEN_TTL_SECONDS`` of life left is refused (``token_expiring``) because the toolbox
token obtained from it would expire during the agent's turn; clients refresh and retry.

Errors are ``401`` with ``WWW-Authenticate: Bearer error="invalid_token"`` and a body
``{"error": "invalid_token" | "wrong_audience" | "token_expiring"}``.
"""

import asyncio
import os
import ssl
import time
from collections.abc import Callable

import jwt
from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from common.user_context import UserPrincipal

ALGORITHMS = ["RS256", "ES256"]
DEFAULT_AUDIENCE = "crisalid-agents"
DEFAULT_MIN_TTL_SECONDS = 120


class ApiError(HTTPException):
    """An error answered as ``{"error": <code>}`` (see the handler in ``chat_api.main``)."""

    def __init__(self, status_code: int, code: str, headers: dict | None = None):
        super().__init__(status_code=status_code, detail={"error": code}, headers=headers)
        self.code = code


class AuthError(ApiError):
    def __init__(self, code: str):
        super().__init__(401, code, headers={"WWW-Authenticate": 'Bearer error="invalid_token"'})


class TokenVerifier:
    def __init__(
        self,
        issuer: str,
        audience: str = DEFAULT_AUDIENCE,
        min_ttl_seconds: int = DEFAULT_MIN_TTL_SECONDS,
        key_resolver: Callable[[str], object] | None = None,
        ssl_verify: bool = True,
    ):
        self.issuer = issuer.rstrip("/")
        self.audience = audience
        self.min_ttl_seconds = min_ttl_seconds
        self._key_resolver = key_resolver
        self._ssl_verify = ssl_verify
        self._jwk_client: jwt.PyJWKClient | None = None

    @property
    def jwks_url(self) -> str:
        return f"{self.issuer}/protocol/openid-connect/certs"

    def _resolve_key(self, token: str):
        if self._key_resolver is not None:
            return self._key_resolver(token)
        if self._jwk_client is None:
            context = None
            if not self._ssl_verify:
                context = ssl.create_default_context()
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
            self._jwk_client = jwt.PyJWKClient(self.jwks_url, cache_keys=True, ssl_context=context)
        return self._jwk_client.get_signing_key_from_jwt(token).key

    async def verify(self, token: str) -> UserPrincipal:
        try:
            key = await asyncio.to_thread(self._resolve_key, token)
        except Exception as exc:  # noqa: BLE001 — unknown kid, malformed header, JWKS unreachable
            raise AuthError("invalid_token") from exc
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=ALGORITHMS,
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["exp", "sub"]},
            )
        except jwt.InvalidAudienceError as exc:
            raise AuthError("wrong_audience") from exc
        except jwt.PyJWTError as exc:
            raise AuthError("invalid_token") from exc
        if claims["exp"] - time.time() < self.min_ttl_seconds:
            raise AuthError("token_expiring")
        return UserPrincipal(
            sub=str(claims["sub"]),
            username=claims.get("preferred_username"),
            email=claims.get("email"),
            client_id=claims.get("azp"),
            token=token,
            exp=int(claims["exp"]),
            jti=claims.get("jti"),
        )


_verifier: TokenVerifier | None = None


def verifier_from_env() -> TokenVerifier:
    issuer = os.getenv("KEYCLOAK_ISSUER", "").strip()
    if not issuer:
        raise RuntimeError("KEYCLOAK_ISSUER is not set: the chat API cannot verify access tokens")
    return TokenVerifier(
        issuer=issuer,
        audience=os.getenv("CHAT_API_AUDIENCE", DEFAULT_AUDIENCE),
        min_ttl_seconds=int(os.getenv("MIN_TOKEN_TTL_SECONDS", str(DEFAULT_MIN_TTL_SECONDS))),
        ssl_verify=os.getenv("KEYCLOAK_SSL_VERIFY", "true").lower() != "false",
    )


def get_verifier() -> TokenVerifier:
    global _verifier
    if _verifier is None:
        _verifier = verifier_from_env()
    return _verifier


def set_verifier(verifier: TokenVerifier | None) -> None:
    """Install a verifier (tests) or reset to the environment-based one (``None``)."""
    global _verifier
    _verifier = verifier


_bearer = HTTPBearer(auto_error=False)


async def _verify_credentials(credentials: HTTPAuthorizationCredentials | None) -> UserPrincipal | None:
    if credentials is None or credentials.scheme.lower() != "bearer":
        return None
    try:
        verifier = get_verifier()
    except RuntimeError as exc:
        raise ApiError(503, "authentication_not_configured") from exc
    return await verifier.verify(credentials.credentials)


async def require_user(credentials: HTTPAuthorizationCredentials | None = Security(_bearer)) -> UserPrincipal:
    principal = await _verify_credentials(credentials)
    if principal is None:
        raise AuthError("invalid_token")
    return principal


async def optional_user(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> UserPrincipal | None:
    """No token → anonymous; a token, when present, must be valid."""
    return await _verify_credentials(credentials)
