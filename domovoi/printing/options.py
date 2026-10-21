"""Print options: what a person picked, resolved against what the printer
can do, and turned into IPP job attributes.

Defaults (owner answers Q8-Q10, 2026-10-21): each printer's own
``sides-default`` and ``print-color-mode-default``, unless the household
set a per-printer switch; paper by the printer's ``media-default``, else
Letter in the US, Canada, Mexico and the Philippines and A4 elsewhere,
from the server's time zone (:func:`household_paper`).

The format sent (plan part 1): PDF when the printer lists it; else PWG
Raster drawn by us; else URF; never a format the printer didn't list.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from domovoi.printing import capabilities as caps_mod
from domovoi.printing import ipp
from domovoi.printing.ipp import Attr

LETTER = "na_letter_8.5x11in"
A4 = "iso_a4_210x297mm"
SIDES = ("one-sided", "two-sided-long-edge", "two-sided-short-edge")

_RANGE = re.compile(r"^\s*(\d{1,5})\s*(?:-\s*(\d{1,5})?)?\s*$")


class OptionError(ValueError):
    pass


def parse_page_ranges(spec: str) -> list[tuple[int, int]]:
    """``"1-3, 5, 7-"`` -> [(1, 3), (5, 5), (7, 99999)]."""
    out: list[tuple[int, int]] = []
    if spec is None or not str(spec).strip():
        return out
    for part in str(spec).replace(";", ",").split(","):
        if not part.strip():
            continue
        m = _RANGE.match(part)
        if not m:
            raise OptionError("Pages should look like 1-3, 5.")
        lo = int(m.group(1))
        hi = int(m.group(2)) if m.group(2) else (99999 if "-" in part else lo)
        if lo < 1 or hi < lo:
            raise OptionError("Pages should look like 1-3, 5.")
        out.append((lo, hi))
        if len(out) > 50:
            raise OptionError("That's too many page ranges.")
    return out


# ─── Paper by country ────────────────────────────────────────────────────

_LETTER_ZONES_PREFIX = ("US/", "Canada/", "America/Indiana/", "America/Kentucky/", "America/North_Dakota/")
_LETTER_ZONES = {
    "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles", "America/Phoenix",
    "America/Anchorage", "America/Adak", "America/Detroit", "America/Boise", "America/Juneau",
    "America/Sitka", "America/Nome", "America/Yakutat", "America/Metlakatla", "America/Menominee",
    "Pacific/Honolulu", "America/Puerto_Rico",
    "America/Toronto", "America/Vancouver", "America/Edmonton", "America/Winnipeg", "America/Halifax",
    "America/St_Johns", "America/Regina", "America/Montreal", "America/Moncton", "America/Whitehorse",
    "America/Yellowknife", "America/Iqaluit", "America/Goose_Bay", "America/Glace_Bay",
    "America/Swift_Current", "America/Dawson_Creek", "America/Fort_Nelson", "America/Dawson",
    "America/Rankin_Inlet", "America/Cambridge_Bay", "America/Inuvik", "America/Atikokan",
    "America/Creston", "America/Resolute", "America/Nipigon", "America/Thunder_Bay",
    "America/Mexico_City", "America/Cancun", "America/Monterrey", "America/Merida", "America/Matamoros",
    "America/Chihuahua", "America/Tijuana", "America/Hermosillo", "America/Mazatlan",
    "America/Bahia_Banderas", "America/Ojinaga", "America/Ciudad_Juarez",
    "Asia/Manila",
}
_LETTER_WINDOWS = ("eastern", "central", "mountain", "pacific", "alaska", "hawaii", "atlantic",
                   "newfoundland", "mexico", "canada")


def zone_name() -> str:
    tz = os.environ.get("TZ", "").lstrip(":")
    if tz:
        return tz
    for p in ("/etc/timezone",):
        try:
            v = Path(p).read_text().strip()
            if v:
                return v
        except OSError:
            pass
    try:
        link = os.readlink("/etc/localtime")
        if "zoneinfo/" in link:
            return link.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    return " ".join(time.tzname)


def household_paper(zone: str | None = None) -> str:
    z = zone if zone is not None else zone_name()
    if z in _LETTER_ZONES or z.startswith(_LETTER_ZONES_PREFIX):
        return LETTER
    low = z.lower()
    if "/" not in z and any(w in low for w in _LETTER_WINDOWS):
        return LETTER
    return A4


# ─── Resolution ──────────────────────────────────────────────────────────


@dataclass
class Resolved:
    sides: str
    color: str            # "color" | "monochrome"
    media: str
    copies: int
    pages_spec: str | None
    format: str           # application/pdf | image/pwg-raster | image/urf
    dpi: int = 0
    adjustments: list[str] = field(default_factory=list)

    @property
    def gray(self) -> bool:
        return self.color == "monochrome"

    def as_json(self) -> dict[str, Any]:
        return {"pages": self.pages_spec, "copies": self.copies, "sides": self.sides,
                "color": self.color, "media": self.media}


def choices(caps: dict[str, Any], copies_cap: int) -> dict[str, Any]:
    sides = [s for s in SIDES if s in (caps.get("sides") or ["one-sided"])] or ["one-sided"]
    color = ["color", "monochrome"] if caps.get("color") else ["monochrome"]
    media_ids = list(dict.fromkeys([m for m in (caps.get("media_ready") or []) if caps_mod.media_points(m)]))
    default = effective_media(caps)
    if default not in media_ids:
        media_ids.insert(0, default)
    for extra in (LETTER, A4):
        if extra in (caps.get("media_supported") or []) and extra not in media_ids and len(media_ids) < 2:
            media_ids.append(extra)
    return {
        "sides": sides,
        "color": color,
        "media": [{"id": m, "label": caps_mod.media_label(m)} for m in media_ids],
        "copies_max": max(1, min(copies_cap, 999)),
    }


def effective_media(caps: dict[str, Any]) -> str:
    m = caps.get("media_default") or ""
    if caps_mod.media_points(m):
        return m
    return household_paper()


def defaults(caps: dict[str, Any], *, two_sided: bool | None, color: bool | None) -> dict[str, str]:
    sides_supported = caps.get("sides") or ["one-sided"]
    sides = caps.get("sides_default") or "one-sided"
    if two_sided is True and "two-sided-long-edge" in sides_supported:
        sides = "two-sided-long-edge"
    elif two_sided is False:
        sides = "one-sided"
    if sides not in sides_supported:
        sides = "one-sided"
    if not caps.get("color"):
        mode = "monochrome"
    elif color is True:
        mode = "color"
    elif color is False:
        mode = "monochrome"
    else:
        d = caps.get("color_default") or "color"
        mode = "monochrome" if "monochrome" in d else "color"
    return {"sides": sides, "color": mode, "media": effective_media(caps)}


def pick_format(caps: dict[str, Any], *, force_raster: bool = False) -> tuple[str, int]:
    formats = caps.get("formats") or []
    if "application/pdf" in formats and not force_raster:
        return "application/pdf", 0
    if "image/pwg-raster" in formats and caps.get("pwg_resolutions"):
        res = caps["pwg_resolutions"]
        dpi = 300 if 300 in res else (min([r for r in res if r >= 300], default=max(res)))
        return "image/pwg-raster", dpi
    if "image/urf" in formats:
        rs = []
        for t in caps.get("urf") or []:
            if t.upper().startswith("RS"):
                for x in t[2:].split("-"):
                    if x.isdigit():
                        rs.append(int(x))
        if rs:
            dpi = 300 if 300 in rs else min([r for r in rs if r >= 300], default=max(rs))
            return "image/urf", dpi
    if "image/pwg-raster" in formats:
        return "image/pwg-raster", 300
    raise OptionError("This printer doesn't take any page format Domovoi can send.")


def resolve(
    caps: dict[str, Any], wanted: dict[str, Any], *, two_sided: bool | None, color: bool | None,
    max_copies: int,
) -> Resolved:
    d = defaults(caps, two_sided=two_sided, color=color)
    adjustments: list[str] = []
    sides = wanted.get("sides") or d["sides"]
    if sides not in SIDES:
        raise OptionError("Two-sided should be one-sided, long edge or short edge.")
    if sides not in (caps.get("sides") or ["one-sided"]):
        adjustments.append("This printer can't print two-sided, so it will print one-sided.")
        sides = "one-sided"
    mode = wanted.get("color") or d["color"]
    if mode not in ("color", "monochrome"):
        raise OptionError("Colour should be colour or black and white.")
    if mode == "color" and not caps.get("color"):
        adjustments.append("This printer prints in black and white only.")
        mode = "monochrome"
    media = wanted.get("media") or d["media"]
    if not caps_mod.media_points(media):
        raise OptionError("That paper size isn't known.")
    try:
        copies = int(wanted.get("copies") or 1)
    except (TypeError, ValueError) as e:
        raise OptionError("Copies should be a number.") from e
    if copies < 1:
        raise OptionError("Copies should be at least 1.")
    if copies > max_copies:
        raise OptionError(
            "That's %d copies, more than this house allows in one job (%d). An admin can change the limit."
            % (copies, max_copies))
    pages_spec = wanted.get("pages") or None
    if pages_spec:
        parse_page_ranges(pages_spec)
    fmt, dpi = pick_format(caps)
    return Resolved(sides=sides, color=mode, media=media, copies=copies, pages_spec=pages_spec,
                    format=fmt, dpi=dpi, adjustments=adjustments)


def sheets_for(pages: int, copies: int, sides: str) -> int:
    per_copy = (pages + 1) // 2 if sides != "one-sided" else pages
    return per_copy * copies


def summary(pages: int, sheets: int, sides: str, copies: int) -> str:
    parts = ["%d page%s" % (pages, "" if pages == 1 else "s")]
    if copies > 1:
        parts.append("%d copies" % copies)
    parts.append("%d sheet%s of paper" % (sheets, "" if sheets == 1 else "s"))
    parts.append("two-sided" if sides != "one-sided" else "one-sided")
    return " · ".join(parts)


def job_attributes(r: Resolved, caps: dict[str, Any], *, send_copies: bool, page_ranges: list[tuple[int, int]] | None) -> list[Attr]:
    attrs: list[Attr] = []
    if send_copies and r.copies > 1:
        attrs.append(Attr.of(ipp.INTEGER, "copies", r.copies))
    if len(caps.get("sides") or []) > 1:
        attrs.append(Attr.of(ipp.KEYWORD, "sides", r.sides))
    if caps.get("color_modes"):
        attrs.append(Attr.of(ipp.KEYWORD, "print-color-mode", r.color))
    size = caps_mod.media_hundredths_mm(r.media)
    if size:
        attrs.append(ipp.media_col(*size))
    if page_ranges:
        attrs.append(Attr(ipp.RANGE, "page-ranges", [ipp.Range(lo, hi) for lo, hi in page_ranges]))
    if "fit" in (caps.get("print_scaling") or []):
        attrs.append(Attr.of(ipp.KEYWORD, "print-scaling", "fit"))
    return attrs
