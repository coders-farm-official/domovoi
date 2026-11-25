"""The household's media-understanding switches (household_settings,
key ``media_understanding``, seeded by V078).

::

    {"enabled": bool,        # "Understand photos and videos"
     "faces": bool,          # "Recognize faces" (off by default, admin)
     "places": bool,         # "Use photo locations" (off by default, admin)
     "paused": bool,         # Pause: the runner takes no library work
     "run_now": bool,        # Run now: ignore the night window until the
                             # backlog is done (still voice-first, capped)
     "frame_cap_mode": "auto" | "manual",
     "frame_cap": int | null,   # the manual frames-per-video cap
     "set_by": "install" | "upgrade" | "admin"}

The seed (owner answers, 2026-11-22): ``enabled`` is on for a new install
and off for a house that upgraded from 1.4.x until an admin turns it on;
faces and places are off for everyone. A missing row reads as the
upgrade defaults (everything off): nothing runs that nobody chose.

Read by both processes (the core's runner, the web's settings routes).
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

KEY = "media_understanding"

#: Bounds of the manual frames-per-video cap (Settings → Photos and videos).
FRAME_CAP_MIN = 3
FRAME_CAP_MAX = 1000

DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "faces": False,
    "places": False,
    "paused": False,
    "run_now": False,
    "frame_cap_mode": "auto",
    "frame_cap": None,
    "set_by": "upgrade",
}

BOOLS = ("enabled", "faces", "places", "paused", "run_now")


class InvalidSwitches(ValueError):
    pass


def normalize(raw: Any) -> dict[str, Any]:
    """A stored value made whole (unknown keys dropped, missing ones the
    defaults)."""
    out = dict(DEFAULTS)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = {}
    if isinstance(raw, dict):
        for k in BOOLS:
            if isinstance(raw.get(k), bool):
                out[k] = raw[k]
        if raw.get("frame_cap_mode") in ("auto", "manual"):
            out["frame_cap_mode"] = raw["frame_cap_mode"]
        cap = raw.get("frame_cap")
        if isinstance(cap, int) and not isinstance(cap, bool) and FRAME_CAP_MIN <= cap <= FRAME_CAP_MAX:
            out["frame_cap"] = cap
        if isinstance(raw.get("set_by"), str):
            out["set_by"] = raw["set_by"]
    if out["frame_cap_mode"] == "manual" and out["frame_cap"] is None:
        out["frame_cap_mode"] = "auto"
    return out


def apply_changes(current: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    """``current`` with an admin's ``changes``; :class:`InvalidSwitches`
    for a malformed one. Pure."""
    if not isinstance(changes, dict):
        raise InvalidSwitches("changes must be an object")
    allowed = set(BOOLS) | {"frame_cap_mode", "frame_cap"}
    unknown = set(changes) - allowed
    if unknown:
        raise InvalidSwitches(f"unknown setting {sorted(unknown)[0]!r}")
    out = dict(current)
    for k in BOOLS:
        if k in changes:
            if not isinstance(changes[k], bool):
                raise InvalidSwitches(f"{k} must be true or false")
            out[k] = changes[k]
    if "frame_cap_mode" in changes:
        if changes["frame_cap_mode"] not in ("auto", "manual"):
            raise InvalidSwitches("frame_cap_mode is auto or manual")
        out["frame_cap_mode"] = changes["frame_cap_mode"]
    if "frame_cap" in changes:
        cap = changes["frame_cap"]
        if cap is not None and (not isinstance(cap, int) or isinstance(cap, bool)
                                or not FRAME_CAP_MIN <= cap <= FRAME_CAP_MAX):
            raise InvalidSwitches(f"frame_cap is a whole number from {FRAME_CAP_MIN} to {FRAME_CAP_MAX}")
        out["frame_cap"] = cap
    if out["frame_cap_mode"] == "manual" and out["frame_cap"] is None:
        raise InvalidSwitches("a manual cap needs frame_cap")
    out["set_by"] = "admin"
    return out


async def read(s: Any) -> dict[str, Any]:
    row = (await s.execute(text("SELECT value FROM household_settings WHERE key = :k"),
                           {"k": KEY})).first()
    return normalize(row[0] if row else None)


async def write(s: Any, value: dict[str, Any]) -> dict[str, Any]:
    value = normalize(value)
    await s.execute(text(
        "INSERT INTO household_settings (key, value) VALUES (:k, CAST(:v AS JSONB)) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"),
        {"k": KEY, "v": json.dumps(value)})
    await s.execute(text("SELECT pg_notify('media_understanding_changed', 'settings')"))
    return value


def admin_cap(value: dict[str, Any]) -> int | None:
    """The admin's manual frames-per-video cap, or None for Automatic."""
    if value.get("frame_cap_mode") == "manual" and isinstance(value.get("frame_cap"), int):
        return int(value["frame_cap"])
    return None
