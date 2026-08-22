"""output/html_formatter.py — Renders MOM JSON dict into HTML email string."""

import logging
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape

logger = logging.getLogger(__name__)

_TEMPLATE_DIR  = Path(__file__).resolve().parent / "templates"
_TEMPLATE_FILE = "mom_email.html.j2"


class HTMLFormatter:
    """Renders MOM data dict → HTML email string."""

    def __init__(self):
        self._env = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            autoescape=select_autoescape(["html", "j2"]),
        )
        self._template = self._env.get_template(_TEMPLATE_FILE)
        logger.info(f"[HTMLFormatter] Template loaded from {_TEMPLATE_DIR / _TEMPLATE_FILE}")

    def render(self, mom_data: dict) -> str:
        html = self._template.render(data=mom_data)
        logger.info(f"[HTMLFormatter] Rendered {len(html):,} character HTML email.")
        return html
