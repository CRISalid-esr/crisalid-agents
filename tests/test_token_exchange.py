import httpx
import pytest

from common.token_exchange import ACCESS_TOKEN_TYPE, TOKEN_EXCHANGE_GRANT, TokenExchangeError, TokenExchanger
from tests.jwt_fixtures import make_token


def exchanger(handler) -> TokenExchanger:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://keycloak.test")
    return TokenExchanger(issuer="https://keycloak.test/realms/crisalid-inst", client_id="crisalid-agents",
                          client_secret="secret", audience="crisalid-graph-mcp", http=http)


async def test_exchange_posts_rfc8693_form_and_caches_per_subject_token():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(dict(httpx.QueryParams(request.content.decode())))
        return httpx.Response(200, json={"access_token": make_token(audience="crisalid-graph-mcp", ttl=600)})

    ex = exchanger(handler)
    subject = make_token(jti="abc")
    first = await ex.exchange(subject)
    second = await ex.exchange(subject)
    assert first == second
    assert len(calls) == 1
    assert calls[0]["grant_type"] == TOKEN_EXCHANGE_GRANT
    assert calls[0]["subject_token_type"] == ACCESS_TOKEN_TYPE
    assert calls[0]["subject_token"] == subject
    assert calls[0]["audience"] == "crisalid-graph-mcp"
    assert calls[0]["client_id"] == "crisalid-agents"
    assert calls[0]["client_secret"] == "secret"
    assert ex.token_endpoint == "https://keycloak.test/realms/crisalid-inst/protocol/openid-connect/token"

    await ex.exchange(make_token(jti="other"))
    assert len(calls) == 2


async def test_expired_cache_entry_is_renewed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": make_token(audience="crisalid-graph-mcp", ttl=-5)})

    ex = exchanger(handler)
    subject = make_token(jti="abc")
    await ex.exchange(subject)
    ex._cache["abc"] = ("stale", 0.0)
    assert await ex.exchange(subject) != "stale"


async def test_refused_exchange_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_token"})

    with pytest.raises(TokenExchangeError):
        await exchanger(handler).exchange(make_token())


def test_configured_flag(monkeypatch):
    for name in ("KEYCLOAK_ISSUER", "KEYCLOAK_CLIENT_ID", "KEYCLOAK_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    assert not TokenExchanger().configured
    assert TokenExchanger(issuer="https://k/realms/r", client_id="c", client_secret="s").configured
