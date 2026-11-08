"""Pieces shared by the two routers of the chat API: agent lookup, allow list, toolbox token."""

import logging

from fastapi import HTTPException, status

from chat_api.allow_list import get_allow_list
from chat_api.auth import ApiError
from common.agent import BaseAgent
from common.registry import UnknownAgentError, registry
from common.token_exchange import TokenExchangeError, TokenExchanger
from common.user_context import UserPrincipal

logger = logging.getLogger(__name__)

_exchanger: TokenExchanger | None = None


def get_exchanger() -> TokenExchanger:
    global _exchanger
    if _exchanger is None:
        _exchanger = TokenExchanger()
        if not _exchanger.configured:
            logger.warning(
                "KEYCLOAK_CLIENT_ID / KEYCLOAK_CLIENT_SECRET are not set: no token exchange, "
                "tool calls reach the MCP toolbox without the user's identity"
            )
    return _exchanger


def set_exchanger(exchanger: TokenExchanger | None) -> None:
    global _exchanger
    _exchanger = exchanger


async def toolbox_token_for(principal: UserPrincipal) -> str | None:
    exchanger = get_exchanger()
    if not exchanger.configured:
        return None
    try:
        return await exchanger.exchange(principal.token)
    except TokenExchangeError as exc:
        logger.error("token exchange failed for sub=%s azp=%s: %s", principal.sub, principal.client_id, exc)
        raise ApiError(status.HTTP_502_BAD_GATEWAY, "token_exchange_failed") from exc


def resolve_agent(name: str, principal: UserPrincipal) -> BaseAgent:
    try:
        agent = registry.get_agent(name)
    except UnknownAgentError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    get_allow_list().check(principal, agent.name)
    return agent


def visible_agents(principal: UserPrincipal | None) -> list[dict]:
    return get_allow_list().filter(principal, registry.describe_agents())


def log_turn(principal: UserPrincipal, agent_name: str, route: str) -> None:
    logger.info("chat turn route=%s agent=%s sub=%s user=%s azp=%s",
                route, agent_name, principal.sub, principal.username, principal.client_id)
