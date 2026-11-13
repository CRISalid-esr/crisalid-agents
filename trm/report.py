"""JSON, Markdown and PDF rendering of a TRM run."""

import json
import logging
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from trm.models import RunResult

logger = logging.getLogger(__name__)

_TEMPLATES = Path(__file__).resolve().parent / "templates"


def _env(autoescape: bool) -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES)),
        autoescape=autoescape,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_markdown(run: RunResult) -> str:
    return _env(autoescape=False).get_template("report.md.j2").render(run=run.to_dict())


def render_html(run: RunResult) -> str:
    return _env(autoescape=True).get_template("report.html.j2").render(run=run.to_dict())


def write_pdf(html: str, path: Path) -> bool:
    """Render the HTML report to PDF with WeasyPrint; returns False (and logs) when it is not installed."""
    try:
        from weasyprint import HTML
    except Exception as exc:  # noqa: BLE001 — missing library or system dependency
        logger.warning("PDF report skipped, WeasyPrint unavailable: %s", exc)
        return False
    HTML(string=html).write_pdf(str(path))
    return True


def write_reports(run: RunResult, output_dir: Path, pdf: bool = True) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"trm-{run.cluster}-{run.started_at[:10]}"
    paths = {"json": output_dir / f"{stem}.json", "md": output_dir / f"{stem}.md"}
    paths["json"].write_text(json.dumps(run.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    paths["md"].write_text(render_markdown(run), encoding="utf-8")
    if pdf:
        pdf_path = output_dir / f"{stem}.pdf"
        if write_pdf(render_html(run), pdf_path):
            paths["pdf"] = pdf_path
    return paths
