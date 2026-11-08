"""Signed test tokens for the chat API tests: a key pair generated per session, no network."""

import time

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from chat_api.auth import TokenVerifier

ISSUER = "https://keycloak.test/realms/crisalid-inst"
AUDIENCE = "crisalid-agents"
KID = "test-key"

_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _pem(key) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


def public_key(token: str):
    header = jwt.get_unverified_header(token)
    if header.get("kid") != KID:
        raise jwt.PyJWKClientError(f"unknown kid {header.get('kid')!r}")
    return _private_key.public_key()


def make_verifier(min_ttl_seconds: int = 120, audience: str = AUDIENCE) -> TokenVerifier:
    return TokenVerifier(issuer=ISSUER, audience=audience, min_ttl_seconds=min_ttl_seconds, key_resolver=public_key)


def make_token(
    sub: str = "user-1",
    username: str = "jdupont@univ.fr",
    azp: str = "sovisuplus",
    audience: str | list[str] = AUDIENCE,
    issuer: str = ISSUER,
    ttl: int = 900,
    jti: str | None = "jti-1",
    kid: str = KID,
    key=None,
    **extra,
) -> str:
    now = int(time.time())
    claims = {
        "sub": sub,
        "preferred_username": username,
        "email": f"{username.split('@')[0]}@example.org",
        "azp": azp,
        "aud": audience,
        "iss": issuer,
        "iat": now,
        "exp": now + ttl,
        **extra,
    }
    if jti is not None:
        claims["jti"] = jti
    return jwt.encode(claims, _pem(key or _private_key), algorithm="RS256", headers={"kid": kid})


def foreign_key():
    return _other_key
