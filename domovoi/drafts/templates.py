"""The draft templates (``templates/*.json``), read by the Python and the
Kotlin renderers alike.

Every template is one column, with standard headings and nothing important
in page headers or footers: no tables, text boxes, icons, photos, skill
bars or columns (what the ATS guides agree on). Sizes are points; a size
or a leading may have one or two decimals (10.5 pt).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from domovoi.drafts import TEMPLATES_DIR

#: The block styles every template defines.
STYLE_NAMES = ("title", "heading", "strong", "body", "bullet")
TEMPLATE_IDS = ("resume-plain", "resume-classic", "letter-block", "letter-personal")


@dataclass(frozen=True)
class Style:
    font: str          # "regular" | "bold"
    size: float
    leading: float
    before: float
    after: float
    indent: float = 0.0
    marker: str = ""


@dataclass(frozen=True)
class Template:
    id: str
    label: str
    kinds: tuple[str, ...]
    width: float
    height: float
    margin: float
    fonts: dict[str, str]
    docx_font: str
    styles: dict[str, Style]
    sections: tuple[str, ...]

    def font_file(self, which: str) -> str:
        """The font file stem for "regular" or "bold"."""
        return self.fonts[which]


def _style(raw: dict[str, Any]) -> Style:
    return Style(font=str(raw["font"]), size=float(raw["size"]), leading=float(raw["leading"]),
                 before=float(raw.get("before", 0)), after=float(raw.get("after", 0)),
                 indent=float(raw.get("indent", 0)), marker=str(raw.get("marker", "")))


def parse(raw: dict[str, Any]) -> Template:
    styles = {name: _style(raw["styles"][name]) for name in STYLE_NAMES}
    page = raw["page"]
    return Template(
        id=str(raw["id"]), label=str(raw["label"]), kinds=tuple(raw["kinds"]),
        width=float(page["width"]), height=float(page["height"]), margin=float(page["margin"]),
        fonts={"regular": str(raw["fonts"]["regular"]), "bold": str(raw["fonts"]["bold"])},
        docx_font=str(raw["docx_font"]), styles=styles, sections=tuple(raw.get("sections") or ()),
    )


@lru_cache(maxsize=None)
def get(template_id: str) -> Template:
    """The template ``template_id``; KeyError for an unknown one."""
    if template_id not in TEMPLATE_IDS:
        raise KeyError(template_id)
    return parse(json.loads((TEMPLATES_DIR / f"{template_id}.json").read_text(encoding="utf-8")))


def for_kind(kind: str) -> list[Template]:
    return [get(t) for t in TEMPLATE_IDS if kind in get(t).kinds]
