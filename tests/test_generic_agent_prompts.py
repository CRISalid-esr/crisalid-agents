"""Generic agent system prompts: rules that the LLM cannot infer from the tool metadata alone."""

from pathlib import Path

import pytest

PROMPT_DIR = Path(__file__).resolve().parent.parent / "agents" / "generic_agent"


@pytest.mark.parametrize("prompt_file", ["mcp_toolbox_restricted_prompt.md", "mcp_toolbox_unrestricted_prompt.md"])
def test_prompts_carry_the_contributor_roles_rule(prompt_file):
    prompt = (PROMPT_DIR / prompt_file).read_text(encoding="utf-8")
    lines = prompt.splitlines()
    start = next(i for i, line in enumerate(lines) if "The people attached to a publication hold different roles" in line)
    vocabulary, usage, presentation = lines[start:start + 3]
    assert "`ctb` is not a role in itself" in vocabulary
    assert 'publications-by-theme with `roles` set to "aut,ctb"' in usage
    assert "name the role" in presentation
    # the rules follow the one that sends thematic questions to publications-by-theme
    assert prompt.index("always call publications-by-theme first") < prompt.index(vocabulary)
