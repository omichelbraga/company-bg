import os
from pathlib import Path
from xml.sax.saxutils import escape

import cairosvg

TBG_TEMPLATE_DIR = Path("tbg")


class TeamsBackgroundError(Exception):
    """Base Teams background generation error."""


class TeamsBackgroundTemplateError(TeamsBackgroundError):
    """Template folder or template content is invalid."""


class TeamsBackgroundRenderError(TeamsBackgroundError):
    """SVG to PNG rendering failed."""


def list_templates() -> list[Path]:
    if not TBG_TEMPLATE_DIR.exists() or not TBG_TEMPLATE_DIR.is_dir():
        raise TeamsBackgroundTemplateError("Teams background template folder 'tbg/' was not found.")

    templates = sorted(
        [
            path
            for path in TBG_TEMPLATE_DIR.iterdir()
            if path.is_file() and path.suffix.lower() in {".svg", ".xml"}
        ]
    )

    if not templates:
        raise TeamsBackgroundTemplateError("No Teams background templates were found in 'tbg/'.")

    return templates


def _render_svg(svg_text: str, output_path: Path) -> None:
    try:
        cairosvg.svg2png(bytestring=svg_text.encode("utf-8"), write_to=str(output_path))
    except Exception as exc:
        raise TeamsBackgroundRenderError(
            f"Failed to render Teams background '{output_path.name}': {exc}"
        ) from exc


def _populate_template(svg_text: str, display_name: str, job_title: str) -> str:
    return (
        svg_text.replace("{{DisplayName}}", escape(display_name))
        .replace("{{JobTitle}}", escape(job_title or ""))
    )


def generate_teams_backgrounds(email_slug: str, display_name: str, job_title: str, output_root: str) -> list[str]:
    templates = list_templates()
    output_dir = Path(output_root) / email_slug / "teams-backgrounds"
    output_dir.mkdir(parents=True, exist_ok=True)

    image_urls: list[str] = []
    for template_path in templates:
        svg_text = template_path.read_text(encoding="utf-8")
        populated_svg = _populate_template(svg_text, display_name, job_title)
        output_name = f"{template_path.stem}.png"
        output_path = output_dir / output_name
        _render_svg(populated_svg, output_path)
        image_urls.append(f"/images/{email_slug}/teams-backgrounds/{output_name}")

    return image_urls
