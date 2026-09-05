import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from create_new_agent import PROJECT_ROOT, class_name_for, scaffold  # noqa: E402

DUMMY_DESCRIPTION = "Reference agent: answers questions and counts words with a local tool."


def test_dummy_agent_matches_its_template(tmp_path):
    # agents/dummy_agent is the checked-in rendering of the dummy template: keep them in sync.
    created = scaffold(
        "dummy_agent", template="dummy", root=tmp_path,
        display_name="Dummy agent", description=DUMMY_DESCRIPTION,
    )
    assert created
    for path in created:
        relative = path.relative_to(tmp_path)
        assert path.read_text() == (PROJECT_ROOT / relative).read_text(), f"{relative} drifted from its template"


@pytest.mark.parametrize("template", ["dummy", "mcp-toolbox"])
def test_generated_agent_is_valid_python(tmp_path, template):
    created = scaffold("sorbobot", template=template, root=tmp_path, description="Sorbonne assistant.")

    names = {p.relative_to(tmp_path).as_posix() for p in created}
    assert names == {
        "agents/sorbobot/__init__.py",
        "agents/sorbobot/agent.py",
        "agents/sorbobot/system_prompt.md",
        "agents/sorbobot/README.md",
        "tests/test_sorbobot.py",
        "openwebui_pipelines/sorbobot_pipeline.py",
    }
    agent_src = (tmp_path / "agents/sorbobot/agent.py").read_text()
    assert "class SorbobotAgent(" in agent_src
    assert 'display_name="Sorbobot"' in agent_src
    for path in created:
        if path.suffix == ".py":
            compile(path.read_text(), str(path), "exec")


def test_no_openwebui_option(tmp_path):
    created = scaffold("ptr", root=tmp_path, openwebui=False)
    assert not any(p.name == "ptr_pipeline.py" for p in created)


def test_refuses_to_overwrite_without_force(tmp_path):
    scaffold("ptr", root=tmp_path)
    with pytest.raises(FileExistsError):
        scaffold("ptr", root=tmp_path)
    scaffold("ptr", root=tmp_path, force=True)


@pytest.mark.parametrize("name", ["Sorbobot", "1ptr", "my-agent", ""])
def test_invalid_names(tmp_path, name):
    with pytest.raises(ValueError):
        scaffold(name, root=tmp_path)


def test_class_names():
    assert class_name_for("sorbobot") == "SorbobotAgent"
    assert class_name_for("dummy_agent") == "DummyAgent"
    assert class_name_for("ptr_v2") == "PtrV2Agent"
