from agents.sorbobot.agent import SYSTEM_PROMPT_FILE, create_agent


def test_sorbobot_is_configured():
    # The toolbox is only contacted on first use, so this runs offline.
    agent = create_agent()

    assert agent.name == "sorbobot"
    assert agent.display_name == "Sorbobot"
    assert SYSTEM_PROMPT_FILE.read_text(encoding="utf-8").strip()
