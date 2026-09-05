from agents.$name.agent import create_agent


def test_${name}_is_configured():
    # The toolbox is only contacted on first use, so this runs offline.
    agent = create_agent()

    assert agent.name == "$name"
    assert agent.display_name == "$display_name"
    assert agent.system_prompt.strip()
