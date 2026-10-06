"""The lyrics workers' live state: the core snapshot's ``"lyrics"`` key
(contract §7.3), for the dashboard's Jobs card.

In memory only — never a database read, never raises, never a title or a
lyric. Counts that need the database (how many songs have lyrics, how many
LRCLIB found …) are the web's own query. ``last_error`` is a short code:
``rate_limited``, ``unavailable:<code>``, ``rejected:<status>``,
``save:<ExceptionType>`` or an exception type.

The fetch's ``state`` is reconciled with the live settings and the gate at
read time, so switching LRCLIB off (or the internet answer to "No") shows
at once, not after the next tick.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

SCAN_STATES = ("idle", "running", "done", "error")
FETCH_STATES = (
    "off", "internet_off", "offline", "rate_limited", "paused", "running", "idle", "done", "error",
)
# States a tick reports that the live gate overrides (or clears) at read time.
_GATE_STATES = frozenset({"off", "internet_off", "offline", "rate_limited", "paused"})


@dataclass
class ScanStatus:
    state: str = "idle"
    last_tick_at: float | None = None
    last_pass_at: float | None = None
    tracks: int | None = None
    unscanned: int | None = None
    read_last_pass: int | None = None
    last_error: str | None = None
    # rows read again so far in the pass under way (published as
    # read_last_pass when the pass completes)
    read_this_pass: int = 0


@dataclass
class FetchStatus:
    state: str = "idle"
    last_tick_at: float | None = None
    last_request_at: float | None = None
    due: int | None = None
    rate_limited_until: float | None = None
    paused_until: float | None = None
    last_error: str | None = None


SCAN = ScanStatus()
FETCH = FetchStatus()


def reset_for_tests() -> None:
    """Back to the boot state, in place (holders of SCAN / FETCH keep
    seeing the live objects)."""
    SCAN.__dict__.update(ScanStatus().__dict__)
    FETCH.__dict__.update(FetchStatus().__dict__)


def scan() -> ScanStatus:
    return SCAN


def fetch() -> FetchStatus:
    return FETCH


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def probe_online() -> bool:
    """The connectivity probe's verdict. No probe registered (a unit
    context, the core still booting) is NOT online: this gate decides
    whether a song's name leaves the house, so it does not assume."""
    from domovoi.connectivity import current_probe

    probe = current_probe()
    return probe is not None and bool(probe.online)


def gate_state() -> str | None:
    """Why LRCLIB may not be asked right now, or None when it may:
    ``off`` (the setting), ``internet_off`` (INTERNET_ACCESS=never),
    ``offline`` (the probe), ``shutting_down``. THE egress gate of the
    fetch ([R10]); checked at the start of every tick and before every
    request."""
    from domovoi import egress, lifecycle
    from domovoi.config import settings

    if not settings.lyrics_lrclib_enabled:
        return "off"
    if egress.internet_turned_off():
        return "internet_off"
    if lifecycle.shutting_down():
        return "shutting_down"
    if not probe_online():
        return "offline"
    return None


def _fetch_state(now: float) -> str:
    # Under "No" the setting itself follows the answer to off and is greyed
    # out ("needs internet"): what the household should hear is why — this
    # Domovoi stays off the internet — not "switched off in Settings", where
    # it could not switch it back on. (Only the report: the worker's gate
    # keeps its own order, the .lrc catch-up runs under internet_off.)
    from domovoi import egress

    if egress.internet_turned_off():
        return "internet_off"
    gate = gate_state()
    if gate == "shutting_down":
        return FETCH.state
    if gate is not None:
        return gate
    if FETCH.rate_limited_until is not None and now < FETCH.rate_limited_until:
        return "rate_limited"
    if FETCH.paused_until is not None and now < FETCH.paused_until:
        return "paused"
    if FETCH.state in _GATE_STATES:
        return "idle"  # switched back on / back online / pause over: the next tick decides
    return FETCH.state


def _future(ts: float | None, now: float) -> float | None:
    return ts if ts is not None and ts > now else None


def lyrics_status() -> dict[str, Any]:
    """``{"scan": {...}, "fetch": {...}}`` — contract §7.3. Never raises."""
    now = time.time()
    try:
        from domovoi.config import settings

        enabled = bool(settings.lyrics_lrclib_enabled)
        write_lrc = bool(settings.lyrics_write_lrc)
        fetch_state = _fetch_state(now)
    except Exception:  # noqa: BLE001 — a status read must never fail the snapshot
        enabled, write_lrc, fetch_state = False, False, "error"
    s, f = SCAN, FETCH
    return {
        "scan": {
            "state": s.state,
            "last_tick_at": _iso(s.last_tick_at),
            "last_pass_at": _iso(s.last_pass_at),
            "tracks": s.tracks,
            "unscanned": s.unscanned,
            "read_last_pass": s.read_last_pass,
            "last_error": s.last_error,
        },
        "fetch": {
            "enabled": enabled,
            "write_lrc": write_lrc,
            "state": fetch_state,
            "last_tick_at": _iso(f.last_tick_at),
            "last_request_at": _iso(f.last_request_at),
            "due": f.due,
            "rate_limited_until": _iso(_future(f.rate_limited_until, now)),
            "paused_until": _iso(_future(f.paused_until, now)),
            "last_error": f.last_error,
        },
    }


__all__ = [
    "FETCH_STATES",
    "FetchStatus",
    "SCAN_STATES",
    "ScanStatus",
    "fetch",
    "gate_state",
    "lyrics_status",
    "probe_online",
    "reset_for_tests",
    "scan",
]
