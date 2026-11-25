"""Microphone DSP presets for an XVF3800 satellite (1.4.1, admin only).

An admin picks a preset for one satellite in its Settings tab; the core
sends only the preset's NAME (``set_mic_dsp``), and the satellite turns it
into ``xvf_host`` writes from its own fixed table (satellite/mic_dsp.py).
No parameter, value or command ever crosses the socket, so the most a
stolen admin session can do is pick one of these presets.

The satellite keeps the chosen preset (``~/.domovoi/mic_dsp.json``, beside
its config.toml), re-applies it after a reconnect or a USB replug, and
reports what the chip holds afterwards (``mic_dsp_state``), which the core
caches per room for the dashboard.

A test marker (``mic_dsp_mark``) stamps a short note ("block 2, 1m") into
the satellite's log and this server's log, so a noisy-room test can be
split into blocks afterwards.

The names here must match the satellite's table (a test holds them
together). A future "winner" preset is one more name in both.
"""

from __future__ import annotations

import math
import re
from typing import Any

#: The preset names, in the order the dashboard offers them. ``factory``
#: is the array's own settings (restored exactly).
PRESETS: tuple[str, ...] = ("factory", "B", "C", "D")

#: What each preset does, for the dashboard.
PRESET_LABELS: dict[str, str] = {
    "factory": "Factory (the array's own settings)",
    "B": "B: AGC max gain 31.6",
    "C": "C: B + noise floors 0.05",
    "D": "D: C + AGC off, fixed gain 10",
}

#: A marker's length cap (characters, after escaping). The satellite caps
#: at the same length.
MARK_MAX = 120

_PARAM_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,39}\Z")
_MAX_PARAMS = 16
_MAX_VALUES = 8


# Line and paragraph separators, zero-width and bidi controls: escaped like
# control characters, so a marker cannot hide or reorder text in a log.
_INVISIBLE = frozenset(chr(c) for c in (
    0x2028, 0x2029, 0x200B, 0x200C, 0x200D, 0x200E, 0x200F, 0x202A, 0x202B,
    0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069, 0x061C, 0xFEFF,
))

#: The largest number kept from a satellite's readback.
_MAX_MAGNITUDE = 1e9


def clean_mark(text: object) -> str:
    """A marker as it may be logged and sent: control characters escaped
    (``\\x1b``), surrounding space dropped, at most :data:`MARK_MAX`
    characters. Empty for anything that isn't text."""
    if not isinstance(text, str):
        return ""
    out = []
    # Cut before escaping: the work stays bounded whatever arrives.
    for ch in text.strip()[:MARK_MAX]:
        code = ord(ch)
        if code < 0x20 or code == 0x7F or 0x80 <= code < 0xA0 or ch in _INVISIBLE:
            out.append(f"\\x{code:02x}" if code < 0x100 else f"\\u{code:04x}")
        else:
            out.append(ch)
    return "".join(out)[:MARK_MAX]


def _values(raw: Any) -> list[Any] | None:
    if not isinstance(raw, list):
        return None
    out: list[Any] = []
    for v in raw[:_MAX_VALUES]:
        if isinstance(v, bool):
            return None
        if isinstance(v, (int, float)) and abs(v) <= _MAX_MAGNITUDE and math.isfinite(v):
            out.append(v)
        elif (isinstance(v, str) and len(v) <= 24
              and re.fullmatch(r"-?\d+(\.\d+)?([eE][-+]?\d+)?", v)
              and abs(float(v)) <= _MAX_MAGNITUDE):
            out.append(v)
        else:
            return None
    return out


def _param_map(raw: Any) -> dict[str, list[Any] | None]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[Any] | None] = {}
    for key, val in list(raw.items())[:_MAX_PARAMS]:
        if isinstance(key, str) and _PARAM_RE.match(key):
            out[key] = _values(val)
    return out


def clean_state(ctrl: dict[str, Any]) -> dict[str, Any]:
    """A satellite's ``mic_dsp_state`` as the core keeps and shows it:
    bounded, typed, with nothing from the device passed through raw."""
    preset = ctrl.get("preset")
    error = ctrl.get("error")
    at = ctrl.get("at")
    return {
        "preset": preset if isinstance(preset, str) and preset in PRESETS else None,
        "applied": _param_map(ctrl.get("applied")),
        "factory": _param_map(ctrl.get("factory")),
        "readback": _param_map(ctrl.get("readback")),
        "ok": ctrl.get("ok") is True,
        "error": clean_mark(error)[:200] if isinstance(error, str) and error else None,
        "at": at if isinstance(at, str) and len(at) <= 40 and re.fullmatch(r"[0-9T:+.\-Z]+", at) else None,
    }


def household_default() -> str | None:
    """The household's boot-default preset for XVF3800 satellites
    (``settings.mic_dsp_default_preset``; owner answer Q8: a preset becomes
    the default only if the hour-long noisy-room test shows it helps). None
    while unset, which it is: no value has been chosen."""
    from domovoi.config import settings

    name = (getattr(settings, "mic_dsp_default_preset", "") or "").strip()
    return name if name in PRESETS and name != "factory" else None


def default_to_apply(state: dict[str, Any]) -> str | None:
    """The preset to send a satellite whose ``mic_dsp_state`` is ``state``
    (from :func:`clean_state`): the household default, when one is set and
    the array holds its factory settings. None otherwise — a satellite an
    admin moved to another preset keeps it, and with no default set (today)
    nothing is ever sent."""
    preset = household_default()
    if preset is None or state.get("preset") not in (None, "factory"):
        return None
    if state.get("error"):
        # A write that failed is not retried on every report.
        return None
    return preset
