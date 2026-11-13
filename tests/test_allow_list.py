import pytest

from chat_api.allow_list import AgentAllowList, parse_allow_list
from chat_api.auth import ApiError
from common.user_context import UserPrincipal


def principal(azp: str | None) -> UserPrincipal:
    return UserPrincipal(sub="s", username="u", email=None, client_id=azp, token="t", exp=0)


def test_parse():
    assert parse_allow_list("sovisuplus:generic_agent,project_topic_matching; open-webui : generic_agent , sorbobot ;") == {
        "sovisuplus": {"generic_agent", "project_topic_matching"},
        "open-webui": {"generic_agent", "sorbobot"},
    }
    assert parse_allow_list("") == {}
    assert parse_allow_list("viewer:") == {"viewer": set()}
    with pytest.raises(ValueError):
        parse_allow_list(":generic_agent")


def test_disabled_list_allows_everything():
    allow = AgentAllowList("")
    assert not allow.enabled
    assert allow.allowed(principal("anyone")) is None
    allow.check(principal("anyone"), "sorbobot")


def test_enabled_list():
    allow = AgentAllowList("sovisuplus:generic_agent;open-webui:generic_agent,sorbobot")
    assert allow.allowed(principal("sovisuplus")) == {"generic_agent"}
    assert allow.allowed(principal("unknown")) == set()
    assert allow.allowed(None) is None
    allow.check(principal("open-webui"), "sorbobot")
    with pytest.raises(ApiError) as excinfo:
        allow.check(principal("sovisuplus"), "sorbobot")
    assert excinfo.value.status_code == 403
    assert excinfo.value.code == "agent_not_allowed"
    agents = [{"name": "generic_agent"}, {"name": "sorbobot"}]
    assert allow.filter(principal("sovisuplus"), agents) == [{"name": "generic_agent"}]
    assert allow.filter(None, agents) == agents
