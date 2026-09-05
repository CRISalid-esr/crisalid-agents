"""Scaffold a new agent package, its OpenWebUI pipeline stub and a smoke test.

    uv run python scripts/create_new_agent.py sorbobot --display-name "Sorbobot" \
        --description "Answers questions about ..." --template mcp-toolbox

Templates live in scripts/templates/<template>/ and use string.Template placeholders:
$name, $class_name, $display_name, $description, $NAME (upper-case env var prefix).
"""

import argparse
import re
import string
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
TEMPLATES = {"dummy": "dummy", "mcp-toolbox": "mcp_toolbox"}

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def class_name_for(name: str) -> str:
    camel = "".join(part.capitalize() for part in name.split("_"))
    return camel if name.endswith("_agent") else f"{camel}Agent"


def display_name_for(name: str) -> str:
    return name.replace("_", " ").capitalize()


def _render(template_file: Path, context: dict[str, str]) -> str:
    return string.Template(template_file.read_text(encoding="utf-8")).substitute(context)


def scaffold(
    name: str,
    template: str = "dummy",
    root: Path = PROJECT_ROOT,
    display_name: str | None = None,
    description: str | None = None,
    openwebui: bool = True,
    force: bool = False,
) -> list[Path]:
    if not _NAME_RE.match(name):
        raise ValueError(f"Invalid agent name {name!r}: use snake_case (letters, digits, underscores)")
    if template not in TEMPLATES:
        raise ValueError(f"Unknown template {template!r}; choose from {sorted(TEMPLATES)}")

    template_dir = TEMPLATES_DIR / TEMPLATES[template]
    context = {
        "name": name,
        "NAME": name.upper(),
        "class_name": class_name_for(name),
        "display_name": display_name or display_name_for(name),
        "description": description or f"{display_name or display_name_for(name)} agent.",
    }

    package_dir = root / "agents" / name
    files: dict[Path, str] = {
        package_dir / "__init__.py": "",
        package_dir / "agent.py": _render(template_dir / "agent.py.tpl", context),
        package_dir / "system_prompt.md": _render(template_dir / "system_prompt.md.tpl", context),
        package_dir / "README.md": _render(template_dir / "README.md.tpl", context),
        root / "tests" / f"test_{name}.py": _render(template_dir / "test.py.tpl", context),
    }
    if openwebui:
        files[root / "openwebui_pipelines" / f"{name}_pipeline.py"] = _render(TEMPLATES_DIR / "pipeline.py.tpl", context)

    existing = [path for path in files if path.exists()]
    if existing and not force:
        listing = "\n".join(f"  {path.relative_to(root)}" for path in existing)
        raise FileExistsError(f"Refusing to overwrite existing files (use --force):\n{listing}")

    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return list(files)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a new agent package with its adapters wired up.")
    parser.add_argument("name", help="snake_case identifier, e.g. sorbobot (becomes agents/<name>/)")
    parser.add_argument("--display-name", help="name shown to users (default: derived from <name>)")
    parser.add_argument("--description", help="one-line description shown by GET /agents")
    parser.add_argument("--template", choices=sorted(TEMPLATES), default="dummy",
                        help="dummy: LangGraph loop with one local tool; mcp-toolbox: ReAct over an MCP Toolbox toolset")
    parser.add_argument("--no-openwebui", action="store_true", help="do not create the OpenWebUI pipeline stub")
    parser.add_argument("--force", action="store_true", help="overwrite existing files")
    args = parser.parse_args(argv)

    try:
        created = scaffold(
            args.name,
            template=args.template,
            display_name=args.display_name,
            description=args.description,
            openwebui=not args.no_openwebui,
            force=args.force,
        )
    except (ValueError, FileExistsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print("Created:")
    for path in created:
        print(f"  {path.relative_to(PROJECT_ROOT)}")
    print()
    print("Next steps:")
    print(f"  1. Edit agents/{args.name}/agent.py and agents/{args.name}/system_prompt.md")
    if args.template == "mcp-toolbox":
        print(f"  2. Set {args.name.upper()}_MCP_TOOLBOX_URL / {args.name.upper()}_MCP_TOOLBOX_TOOLSET in .env"
              " (or rely on CRISALID_MCP_TOOLBOX_*)")
    else:
        print("  2. Set MODEL / API_KEY / LLM_API_BASE in .env")
    print("  3. uv run pytest")
    print("  4. uv run uvicorn chat_api.main:app --port 9100"
          f"   →  POST /agents/{args.name}/chat")
    if not args.no_openwebui:
        print("     uv run python scripts/debug_openwebui_pipelines.py"
              f"   →  model \"{args.display_name or display_name_for(args.name)}\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
