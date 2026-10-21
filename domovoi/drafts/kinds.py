"""The kinds of document a draft can be, with what each starts as.

| Kind | Template | Saved as by default (plan Q7) |
|---|---|---|
| resume | Resume, Plain | .docx (the ATS-safest choice, and editable) |
| cover letter | Letter, Block | .docx |
| letter | Letter, Block (Letter, Personal for a landlord, school or neighbour) | PDF (a letter to print or mail) |

Both formats are always a tap away; the default is only what "save" picks.
"""

from __future__ import annotations

from dataclasses import dataclass

FORMATS = ("docx", "pdf")


@dataclass(frozen=True)
class Kind:
    id: str
    label: str
    template: str
    fmt: str


KINDS: dict[str, Kind] = {
    "resume": Kind("resume", "Resume", "resume-plain", "docx"),
    "cover_letter": Kind("cover_letter", "Cover letter", "letter-block", "docx"),
    "letter": Kind("letter", "Letter", "letter-block", "pdf"),
}


def get(kind: str) -> Kind:
    return KINDS.get(kind) or KINDS["letter"]


def default_format(kind: str) -> str:
    return get(kind).fmt


def card_line(kind: str, version: int, words: int) -> str:
    """The history's one-line marker for a draft (plan part 10)."""
    return f"draft v{version} · {get(kind).label.lower()} · {words} word{'' if words == 1 else 's'}"
