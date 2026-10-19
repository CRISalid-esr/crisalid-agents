import pytest

from agents.sorbobot.config import SettingsError, SorbobotSettings


def test_settings_come_from_the_environment_except_fixed_values(monkeypatch):
    monkeypatch.setenv("CRISALID_TAXI_URL", "http://taxi.test")
    monkeypatch.setenv("SORBOBOT_MIN_AUTHORS", "5")
    monkeypatch.setenv("SORBOBOT_TOPIC_LINK_THRESHOLD", "0.7")
    monkeypatch.setenv("SORBOBOT_JUDGE_RETRY_THRESHOLD", "0.9")

    settings = SorbobotSettings.from_env()

    assert (settings.taxi_url, settings.min_authors, settings.topic_link_threshold) == ("http://taxi.test", 5, 0.7)
    assert settings.semantic_threshold == 0.56
    assert settings.JUDGE_RETRY_THRESHOLD == 0.5


@pytest.mark.parametrize(
    ("variable", "value", "message"),
    [
        ("CRISALID_TAXI_URL", "", "CRISALID_TAXI_URL is required"),
        ("SORBOBOT_MIN_AUTHORS", "ten", "not a valid int"),
        ("SORBOBOT_RANKING_METHOD", "popularity", "SORBOBOT_RANKING_METHOD"),
        ("SORBOBOT_JUDGE_THRESHOLD", "1.5", "between 0 and 1"),
        ("SORBOBOT_SHOW_MAX_AUTHORS", "0", "SORBOBOT_SHOW_MAX_AUTHORS must be positive"),
        ("SORBOBOT_MAX_SEARCH_TOPICS", "0", "SORBOBOT_MAX_SEARCH_TOPICS must be positive"),
    ],
)
def test_invalid_settings_are_rejected(monkeypatch, variable, value, message):
    monkeypatch.setenv("CRISALID_TAXI_URL", "http://taxi.test")
    monkeypatch.setenv(variable, value)

    with pytest.raises(SettingsError, match=message):
        SorbobotSettings.from_env()
