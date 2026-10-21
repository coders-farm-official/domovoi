"""What a printer can do and how it is, in plain words.

Two halves:

* :func:`summarize` turns a Get-Printer-Attributes answer into the
  JSON-safe capabilities we store with the printer (formats, sides, colour
  modes, paper, raster types and resolutions, copies, operations).
* :func:`status_of` turns the live attributes into the status a person
  reads: one lowercase chrome word (``ready``, ``out of paper``…), a tone
  for the dot, the sentences toasts and voice use, and the supplies.

Supplies: ``printer-supply`` (PWG 5100.13) when the printer has it,
otherwise the CUPS ``marker-*`` family — which is load-bearing, not a
fallback: Kamron's Brother HL-L3220CDW reports only ``marker-*`` (P0,
2026-10-21). A level of -1/-2/-3 means "unknown / some left" and is shown
as "level not reported", never as a number.

Everything a printer says is data: strings are already capped and stripped
by the codec; ``marker-colors`` is used as a swatch only when it is exactly
``#rrggbb``; ``printer-state-message`` is shown as text and never spoken.
"""

from __future__ import annotations

import re
from typing import Any

from domovoi.printing import ipp

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")

# ─── Plain words (plan part 8, "Errors in plain words") ───────────────────

# reason keyword (suffix -report/-warning/-error stripped) -> (state, tone, rank)
_REASONS: dict[str, tuple[str, str, int]] = {
    "media-empty": ("out of paper", "err", 90),
    "media-needed": ("out of paper", "err", 90),
    "media-jam": ("jammed", "err", 95),
    "cover-open": ("cover open", "err", 85),
    "door-open": ("cover open", "err", 85),
    "interlock-open": ("cover open", "err", 85),
    "toner-empty": ("out of toner", "err", 80),
    "marker-supply-empty": ("out of ink", "err", 80),
    "marker-waste-full": ("needs service", "err", 75),
    "output-area-full": ("tray full", "err", 70),
    "media-low": ("low on paper", "warn", 40),
    "toner-low": ("low on toner", "warn", 30),
    "marker-supply-low": ("low on ink", "warn", 30),
    "marker-waste-almost-full": ("needs service", "warn", 25),
    "connecting-to-device": ("checking", "warn", 20),
    "offline": ("offline", "off", 99),
    "shutdown": ("offline", "off", 99),
}


def _sentence(state: str, name: str, supply: str | None = None) -> str:
    who = name or "The printer"
    the = who if who.lower().startswith("the ") else "The " + who
    return {
        "out of paper": f"{the} is out of paper.",
        "low on paper": f"{the} is low on paper.",
        "jammed": f"Paper is stuck in {the[0].lower() + the[1:]}.",
        "out of toner": f"{the} is out of {supply or 'toner'}.",
        "out of ink": f"{the} is out of {supply or 'ink'}.",
        "low on toner": f"{the} is low on {supply or 'toner'}.",
        "low on ink": f"{the} is low on {supply or 'ink'}.",
        "cover open": f"A door or cover on {the[0].lower() + the[1:]} is open.",
        "tray full": f"{the}'s output tray is full.",
        "needs service": f"{the}'s waste ink pad is full. It needs servicing.",
        "offline": f"{the} isn't answering. Is it switched on?",
        "stopped": f"{the} has stopped.",
    }.get(state, "")


def reason_key(reason: str) -> str:
    r = (reason or "").lower()
    for suffix in ("-report", "-warning", "-error"):
        if r.endswith(suffix):
            return r[: -len(suffix)]
    return r


def is_error_reason(reason: str) -> bool:
    entry = _REASONS.get(reason_key(reason))
    return bool(entry and entry[1] == "err") and not reason.lower().endswith(("-report", "-warning"))


# ─── Supplies ─────────────────────────────────────────────────────────────

_SHORT_COLOURS = {"bk": "black", "k": "black", "pbk": "photo black", "mbk": "matte black",
                  "c": "cyan", "m": "magenta", "y": "yellow", "lc": "light cyan", "lm": "light magenta"}


def _supply_name(raw: str, kind: str) -> str:
    raw = (raw or "").strip()
    colour = _SHORT_COLOURS.get(raw.lower())
    noun = "toner" if "toner" in kind else ("ink" if "ink" in kind else "")
    if colour:
        return (colour + (" " + noun if noun else "")).capitalize()
    return raw[:60] or "Supply"


def _level(v: Any) -> int | None:
    if not isinstance(v, int) or v < 0:
        return None
    return max(0, min(100, v))


def _octets_text(value: str) -> str:
    """``printer-supply`` is an octetString, which the codec hands over as
    hex; the bytes are ``key=value;`` text. Anything that doesn't decode
    to printable text is returned as it came."""
    if not value or len(value) % 2 or any(c not in "0123456789abcdef" for c in value):
        return value
    try:
        text = bytes.fromhex(value).decode("ascii")
    except (ValueError, UnicodeDecodeError):
        return value
    return text if all(" " <= ch <= "~" for ch in text) else value


def supplies_of(attrs: dict[str, list[Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    supply = attrs.get("printer-supply") or []
    descs = attrs.get("printer-supply-description") or []
    for i, entry in enumerate(supply[:16]):
        if not isinstance(entry, str):
            continue
        entry = _octets_text(entry)
        fields = {}
        for part in entry.split(";"):
            k, _, v = part.partition("=")
            if k:
                fields[k.strip().lower()] = v.strip()
        kind = fields.get("type", "").lower()
        if kind and not any(w in kind for w in ("toner", "ink", "marker")):
            continue
        # A waste tank fills up; it isn't something that runs out.
        if "waste" in kind or fields.get("class", "").lower() == "receptaclethatisfilled":
            continue
        try:
            level = int(fields.get("level", "-2"))
            maxc = int(fields.get("maxcapacity", "100") or 100)
        except ValueError:
            level, maxc = -2, 100
        pct = round(level * 100 / maxc) if level >= 0 and maxc > 0 else None
        name = descs[i] if i < len(descs) and isinstance(descs[i], str) and descs[i] else \
            fields.get("colorantname", "")
        colour = fields.get("colorantname", "").lower()
        out.append({
            "name": _supply_name(name, kind), "level": _level(pct),
            "color": _CSS_COLOURS.get(colour), "kind": "toner" if "toner" in kind else "ink",
            "low": pct is not None and pct <= 10,
        })
    if out:
        return out
    names = attrs.get("marker-names") or []
    levels = attrs.get("marker-levels") or []
    lows = attrs.get("marker-low-levels") or []
    colours = attrs.get("marker-colors") or []
    types = attrs.get("marker-types") or []
    for i, raw in enumerate(names[:16]):
        kind = str(types[i]).lower() if i < len(types) else ""
        if kind and ("waste" in kind or "opc" in kind or "fuser" in kind or "transfer" in kind):
            continue
        level = _level(levels[i] if i < len(levels) else None)
        low_at = lows[i] if i < len(lows) and isinstance(lows[i], int) else 10
        swatch = colours[i] if i < len(colours) and isinstance(colours[i], str) and _HEX.match(colours[i]) else None
        out.append({
            "name": _supply_name(str(raw), kind), "level": level, "color": swatch.lower() if swatch else None,
            "kind": "toner" if "toner" in kind else "ink",
            "low": level is not None and level <= max(0, low_at),
        })
    return out


_CSS_COLOURS = {"black": "#000000", "cyan": "#00ffff", "magenta": "#ff00ff", "yellow": "#ffff00"}


# ─── Capabilities ─────────────────────────────────────────────────────────


def _strs(attrs: dict[str, list[Any]], name: str, cap: int = 200) -> list[str]:
    return [v for v in (attrs.get(name) or [])[:cap] if isinstance(v, str)]


def _dpis(attrs: dict[str, list[Any]], name: str) -> list[int]:
    out = []
    for v in attrs.get(name) or []:
        if isinstance(v, ipp.Resolution) and v.dpi not in out:
            out.append(v.dpi)
    return sorted(out)


def summarize(attrs: dict[str, list[Any]]) -> dict[str, Any]:
    first = lambda n, d=None: ipp.first(attrs, n, d)  # noqa: E731
    copies = first("copies-supported")
    copies_max = copies.upper if isinstance(copies, ipp.Range) else (copies if isinstance(copies, int) else 1)
    color_modes = _strs(attrs, "print-color-mode-supported")
    sides = _strs(attrs, "sides-supported") or ["one-sided"]
    media_ready = _strs(attrs, "media-ready")
    media_supported = _strs(attrs, "media-supported", 400)
    uuid = str(first("printer-uuid", "") or "").lower().removeprefix("urn:uuid:")
    more_info = str(first("printer-more-info", "") or "")
    return {
        "uuid": uuid,
        "make_and_model": str(first("printer-make-and-model", "") or first("printer-info", "") or "Printer")[:120],
        "location": str(first("printer-location", "") or "")[:120],
        "info": str(first("printer-info", "") or "")[:120],
        "formats": _strs(attrs, "document-format-supported"),
        "color": "color" in color_modes or bool(first("color-supported", False)),
        "color_modes": color_modes,
        "color_default": str(first("print-color-mode-default", "") or ""),
        "sides": sides,
        "sides_default": str(first("sides-default", "one-sided") or "one-sided"),
        "duplex": any(s.startswith("two-sided") for s in sides),
        "media_default": str(first("media-default", "") or ""),
        "media_ready": media_ready,
        "media_supported": media_supported,
        "pwg_resolutions": _dpis(attrs, "pwg-raster-document-resolution-supported"),
        "pwg_types": _strs(attrs, "pwg-raster-document-type-supported"),
        "sheet_back": str(first("pwg-raster-document-sheet-back", "normal") or "normal"),
        "urf": _strs(attrs, "urf-supported"),
        "copies_max": max(1, int(copies_max or 1)),
        "page_ranges": bool(first("page-ranges-supported", False)),
        "operations": [v for v in attrs.get("operations-supported") or [] if isinstance(v, int)][:100],
        "identify_actions": _strs(attrs, "identify-actions-supported"),
        "print_scaling": _strs(attrs, "print-scaling-supported"),
        "admin_url": more_info if more_info.startswith(("http://", "https://")) else "",
        "pdf_max_kb": _range_upper(first("pdf-k-octets-supported")),
    }


def _range_upper(v: Any) -> int | None:
    return v.upper if isinstance(v, ipp.Range) else None


def media_label(keyword: str) -> str:
    k = (keyword or "").lower()
    known = {"na_letter_8.5x11in": "Letter", "iso_a4_210x297mm": "A4", "na_legal_8.5x14in": "Legal",
             "iso_a5_148x210mm": "A5", "na_executive_7.25x10.5in": "Executive", "na_index-4x6_4x6in": "4 × 6 photo",
             "iso_a6_105x148mm": "A6", "na_5x7_5x7in": "5 × 7 photo"}
    if k in known:
        return known[k]
    m = re.match(r"^[a-z]+_([a-z0-9.-]+)_", k)
    return (m.group(1).replace("-", " ").title() if m else keyword)[:40]


_MEDIA_SIZE = re.compile(r"_(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)(mm|in)$")


def media_points(keyword: str) -> tuple[int, int] | None:
    """(width, height) in points for a PWG media keyword."""
    m = _MEDIA_SIZE.search(keyword or "")
    if not m:
        return None
    w, h, unit = float(m.group(1)), float(m.group(2)), m.group(3)
    f = 72.0 if unit == "in" else 72.0 / 25.4
    return (round(w * f), round(h * f))


def media_hundredths_mm(keyword: str) -> tuple[int, int] | None:
    m = _MEDIA_SIZE.search(keyword or "")
    if not m:
        return None
    w, h, unit = float(m.group(1)), float(m.group(2)), m.group(3)
    f = 2540.0 if unit == "in" else 100.0
    return (round(w * f), round(h * f))


# ─── Status ───────────────────────────────────────────────────────────────


def status_of(attrs: dict[str, list[Any]] | None, name: str, *, offline: bool = False) -> dict[str, Any]:
    if offline or attrs is None:
        return {"state": "offline", "tone": "off", "words": [_sentence("offline", name)], "message": ""}
    pstate = ipp.first(attrs, "printer-state", 3)
    reasons = [r for r in (attrs.get("printer-state-reasons") or []) if isinstance(r, str) and r != "none"]
    supplies = supplies_of(attrs)
    best: tuple[str, str, int] | None = None
    words: list[str] = []
    for r in reasons:
        entry = _REASONS.get(reason_key(r))
        if entry is None:
            continue
        state, tone, rank = entry
        if r.lower().endswith("-report"):
            tone = "warn" if tone == "err" else tone
        supply = None
        if state.startswith(("low on", "out of")) and state not in ("out of paper", "low on paper"):
            hit = [s for s in supplies if (s["level"] is not None and s["level"] <= 10) or s["low"]]
            supply = supply_words(hit[0]) if hit else None
        sentence = _sentence(state, name, supply)
        if sentence and sentence not in words:
            words.append(sentence)
        if best is None or rank > best[2]:
            best = (state, tone, rank)
    if best is None:
        low = [s for s in supplies if s["low"]]
        if low:
            kind = "toner" if low[0]["kind"] == "toner" else "ink"
            best = ("low on " + kind, "warn", 1)
            words.append(_sentence(best[0], name, supply_words(low[0])))
    if best is not None:
        state, tone = best[0], best[1]
    elif pstate == 4:
        state, tone = "printing", "ok"
    elif pstate == 5:
        state, tone = "stopped", "err"
        words.append(_sentence("stopped", name))
    else:
        state, tone = "ready", "ok"
    if best is not None and pstate == 4 and tone != "err" and best[2] < 30:
        # Low ink while printing: the job is what matters right now; the
        # low-ink sentence stays in ``words``.
        state = "printing"
    return {
        "state": state, "tone": tone, "words": words,
        "message": str(ipp.first(attrs, "printer-state-message", "") or "")[:200],
    }


_SPOKEN_COLOURS = ("photo black", "matte black", "light cyan", "light magenta",
                   "black", "cyan", "magenta", "yellow")
_SWATCH_COLOURS = {"#000000": "black", "#00ffff": "cyan", "#ff00ff": "magenta", "#ffff00": "yellow"}


def spoken_colour(supply: dict[str, Any]) -> str | None:
    """Our own word for a supply's colour, or None. What a printer calls its
    supplies is never spoken as it is (owner rule): only these words."""
    name = str(supply.get("name") or "").lower()
    for word in _SPOKEN_COLOURS:
        if word in name:
            return word
    return _SWATCH_COLOURS.get(str(supply.get("color") or "").lower())


def supply_words(supply: dict[str, Any]) -> str:
    """ "yellow toner", "black ink", or just "toner" / "ink"."""
    noun = "toner" if supply.get("kind") == "toner" else "ink"
    colour = spoken_colour(supply)
    return "%s %s" % (colour, noun) if colour else noun


def spoken_supplies(name: str, supplies: list[dict[str, Any]]) -> str:
    """ "The office printer: black about 60 percent, and colour is low:
    yellow is at 10 percent." """
    the = name if name.lower().startswith("the ") else "the " + name
    if not supplies or all(s["level"] is None for s in supplies):
        return f"{the[0].upper() + the[1:]} doesn't report its ink levels."
    parts = []
    lows = []
    for s in supplies:
        label = spoken_colour(s) or ("toner" if s["kind"] == "toner" else "ink")
        if s["level"] is None:
            continue
        if s["low"]:
            lows.append(f"{label} is at {s['level']} percent")
        else:
            parts.append(f"{label} about {round(s['level'] / 5) * 5} percent")
    head = the[0].upper() + the[1:] + ": "
    if parts and lows:
        return head + ", ".join(parts) + ", and some are low: " + " and ".join(lows) + "."
    if lows:
        return head + " and ".join(lows) + ". That's low."
    return head + ", ".join(parts) + "."
