import pytest

from agents.sorbobot.config import ConfigurationError, SorbobotSettings


def test_defaults_from_a_minimal_environment():
    settings = SorbobotSettings.from_env({"CRISALID_TAXI_URL": "http://taxi:8000/"})
    assert settings.taxi_url == "http://taxi:8000/"
    assert settings.toolset == "sorbobot" and settings.toolbox_url is None
    assert (settings.taxi_timeout_seconds, settings.toolbox_timeout_seconds, settings.judge_timeout_seconds) == (15, 30, 30)
    assert (settings.taxi_max_topics, settings.taxi_min_query_chars, settings.semantic_threshold) == (100, 25, 0.56)
    assert (settings.max_search_topics, settings.topic_link_threshold, settings.judge_threshold) == (20, 0.57, 0.6)
    assert (settings.min_authors, settings.max_judged_siblings, settings.ranking_method) == (2, 100, "weighted_relevance")
    assert (settings.show_max_authors, settings.show_max_domains) == (10, 5)
    assert (settings.show_max_person_publications, settings.profile_max_publications, settings.explore_max_domains) == (20, 20, 10)
    assert settings.max_message_chars == 1000
    assert settings.topic_sources == "crisalid"
    assert settings.supported_languages == ("en", "fr") and settings.default_language == "en"
    assert settings.judges_enabled


def test_values_are_read_and_converted():
    settings = SorbobotSettings.from_env({
        "CRISALID_TAXI_URL": "http://taxi", "CRISALID_MCP_TOOLBOX_URL": "http://toolbox:5000",
        "SORBOBOT_JUDGE_THRESHOLD": "0", "SORBOBOT_MIN_AUTHORS": "3", "SORBOBOT_RANKING_METHOD": "count",
        "SORBOBOT_SUPPORTED_LANGUAGES": "FR, en", "SORBOBOT_DEFAULT_LANGUAGE": "fr", "SORBOBOT_TAXI_TIMEOUT_SECONDS": "2.5",
        "SORBOBOT_TOPIC_SOURCES": " crisalid ,openalex, ",
    })
    assert settings.toolbox_url == "http://toolbox:5000"
    assert not settings.judges_enabled and settings.min_authors == 3 and settings.ranking_method == "count"
    assert settings.supported_languages == ("fr", "en") and settings.default_language == "fr"
    assert settings.taxi_timeout_seconds == 2.5
    assert settings.topic_sources == "crisalid,openalex"


def test_empty_topic_sources_keep_every_link_source():
    settings = SorbobotSettings.from_env({"CRISALID_TAXI_URL": "http://taxi", "SORBOBOT_TOPIC_SOURCES": ""})
    assert settings.topic_sources == ""


def test_the_agent_specific_toolbox_url_wins():
    settings = SorbobotSettings.from_env({
        "CRISALID_TAXI_URL": "http://taxi", "CRISALID_MCP_TOOLBOX_URL": "http://shared", "SORBOBOT_MCP_TOOLBOX_URL": "http://mine",
    })
    assert settings.toolbox_url == "http://mine"


@pytest.mark.parametrize("env, message", [
    ({}, "CRISALID_TAXI_URL"),
    ({"SORBOBOT_MIN_AUTHORS": "two"}, "SORBOBOT_MIN_AUTHORS must be an integer"),
    ({"SORBOBOT_JUDGE_THRESHOLD": "high"}, "SORBOBOT_JUDGE_THRESHOLD must be a number"),
    ({"SORBOBOT_JUDGE_THRESHOLD": "1.5"}, "between 0 and 1"),
    ({"SORBOBOT_SHOW_MAX_AUTHORS": "0"}, "must be > 0"),
    ({"SORBOBOT_RANKING_METHOD": "random"}, "SORBOBOT_RANKING_METHOD"),
    ({"SORBOBOT_DEFAULT_LANGUAGE": "de"}, "SORBOBOT_DEFAULT_LANGUAGE"),
    ({"SORBOBOT_SUPPORTED_LANGUAGES": " , "}, "SORBOBOT_SUPPORTED_LANGUAGES"),
])
def test_invalid_values_are_deployment_errors(env, message):
    base = {"CRISALID_TAXI_URL": "http://taxi"} if env else {}
    with pytest.raises(ConfigurationError, match=message):
        SorbobotSettings.from_env({**base, **env})
