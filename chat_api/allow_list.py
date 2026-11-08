"""Which agents each client application may call.

``AGENT_ALLOW_LIST`` maps Keycloak client ids (the ``azp`` claim of the access token) to agent names::

    sovisuplus:generic_agent,project_topic_matching;open-webui:generic_agent,project_topic_matching,sorbobot

A client absent from the list may call no agent. An empty or unset variable disables the check (local
development). Agent listings apply the list when the request carries a token and return every served agent
otherwise; chat endpoints always enforce it.
"""

import logging
import os

from chat_api.auth import ApiError
from common.user_context import UserPrincipal

logger = logging.getLogger(__name__)


def parse_allow_list(raw: str) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for entry in raw.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        client, _, agents = entry.partition(":")
        client = client.strip()
        if not client:
            raise ValueError(f"AGENT_ALLOW_LIST entry without a client id: {entry!r}")
        result.setdefault(client, set()).update(a.strip() for a in agents.split(",") if a.strip())
    return result


class AgentAllowList:
    def __init__(self, raw: str | None = None):
        if raw is None:
            raw = os.getenv("AGENT_ALLOW_LIST", "")
        self._by_client = parse_allow_list(raw)

    @property
    def enabled(self) -> bool:
        return bool(self._by_client)

    def allowed(self, principal: UserPrincipal | None) -> set[str] | None:
        """Agents the caller may use; ``None`` means no restriction applies."""
        if not self.enabled or principal is None:
            return None
        return set(self._by_client.get(principal.client_id or "", ()))

    def check(self, principal: UserPrincipal, agent_name: str) -> None:
        allowed = self.allowed(principal)
        if allowed is not None and agent_name not in allowed:
            raise ApiError(403, "agent_not_allowed")

    def filter(self, principal: UserPrincipal | None, agents: list[dict]) -> list[dict]:
        allowed = self.allowed(principal)
        if allowed is None:
            return agents
        return [agent for agent in agents if agent["name"] in allowed]


_allow_list: AgentAllowList | None = None


def get_allow_list() -> AgentAllowList:
    global _allow_list
    if _allow_list is None:
        _allow_list = AgentAllowList()
        if not _allow_list.enabled:
            logger.warning("AGENT_ALLOW_LIST is empty: every client may call every served agent")
    return _allow_list


def set_allow_list(allow_list: AgentAllowList | None) -> None:
    global _allow_list
    _allow_list = allow_list
