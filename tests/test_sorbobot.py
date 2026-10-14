from agents.sorbobot.agent import create_agent


def test_sorbobot_is_configured():
    # The toolbox is only contacted on first use, so this runs offline.
    agent = create_agent()

    assert agent.name == "sorbobot"
    assert agent.display_name == "Sorbobot"
    assert agent.system_prompt.strip()
