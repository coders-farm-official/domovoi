"""The satellite upgrades this core has asked for, and how each one ended.

``POST /v1/admin/satellite/upgrade`` sends the ``upgrade`` frame and answers
at once. The satellite then syncs its code, restarts, and says ``hello``
again: on the new code, or on the old one when its rollback watchdog put
that back (``satellite/client.py`` ``_apply_upgrade``). The core is the one
party that sees both ends, so it keeps one record per room in
``app.state.satellite_upgrades``. From the request until the satellite is
back the room is UPGRADING: the snapshot carries the record
(``satellite_upgrades``), the dashboard and the app lock the room's Upgrade
button on it — across a page reload or in another tab, the way Settings →
Version locks on ``restart_in_progress`` — and a second request meanwhile
is refused (409): a satellite handed a second ``upgrade`` frame mid-sync
starts a second sync and backup over the first.

States:

``running``
    asked for; the satellite is not back yet.
``done``
    back with the ``expected_sha`` the request carried.
``rolled_back``
    back on another code version: the new code did not stay up and the
    satellite's watchdog restored the old tree.
``no_news``
    no hello within :func:`window_sec`. A sync that fails BEFORE the restart
    (a manifest it could not fetch, an import self-check that failed)
    leaves the satellite online on its old code and tells the core nothing;
    its log says why.

A finished record is kept for :data:`KEEP_FINISHED_SEC` so a page that
missed the return can still say how it went, then dropped. In memory only,
like the synced_sha it is checked against: a core restart forgets it (and
ends every connection, so nothing is mid-upgrade from its point of view).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

RUNNING = "running"
DONE = "done"
ROLLED_BACK = "rolled_back"
NO_NEWS = "no_news"

# How long a finished upgrade stays in the snapshot.
KEEP_FINISHED_SEC = 600.0


def window_sec(reconnect_timeout_sec: float) -> float:
    """How long the core waits for the satellite to come back before it
    calls the upgrade ``no_news``. The satellite syncs (an import self-check
    of up to 120 s included), restarts, and its watchdog gives the new code
    ``reconnect_timeout_sec`` to connect before it rolls back and restarts
    again: two of those plus two minutes covers the slow path."""
    try:
        reconnect = float(reconnect_timeout_sec)
    except (TypeError, ValueError):
        reconnect = 90.0
    return max(180.0, 2 * max(reconnect, 0.0) + 120.0)


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def _settle(rec: dict[str, Any], now: float) -> dict[str, Any]:
    """A running record past its window is ``no_news``."""
    if rec.get("state") == RUNNING and now >= rec.get("deadline", now):
        rec["state"] = NO_NEWS
        rec["finished_at"] = rec.get("deadline", now)
    return rec


def running(table: dict[str, dict[str, Any]], room: str, now: float) -> dict[str, Any] | None:
    """The room's upgrade when one is under way, else None."""
    rec = table.get(room)
    if rec is None:
        return None
    _settle(rec, now)
    return rec if rec["state"] == RUNNING else None


def start(
    table: dict[str, dict[str, Any]],
    room: str,
    *,
    expected_sha: str | None,
    from_sha: str | None,
    now: float,
    window: float,
) -> dict[str, Any]:
    """Record an upgrade the core is about to ask for (before the frame
    goes out, so a second request in the meantime is refused)."""
    rec = {
        "state": RUNNING,
        "expected_sha": expected_sha,
        "from_sha": from_sha,
        "started_at": now,
        "deadline": now + window,
        "finished_at": None,
    }
    table[room] = rec
    return rec


def forget(table: dict[str, dict[str, Any]], room: str, rec: dict[str, Any]) -> None:
    """Drop `rec` (the frame never went out), unless a newer one replaced it."""
    if table.get(room) is rec:
        del table[room]


def on_hello(
    table: dict[str, dict[str, Any]], room: str, synced_sha: str | None, now: float
) -> None:
    """The room said hello: an upgrade asked for it is over, and the code
    version it came back on says how it went. A later hello within the keep
    window can still change the answer (the new code came up, then the
    watchdog rolled it back)."""
    rec = table.get(room)
    if rec is None:
        return
    _settle(rec, now)
    finished = rec.get("finished_at")
    if rec["state"] != RUNNING and finished is not None and now - finished > KEEP_FINISHED_SEC:
        del table[room]
        return
    state = DONE if synced_sha and synced_sha == rec.get("expected_sha") else ROLLED_BACK
    if state == rec["state"]:
        return          # one more reconnect on the same code: nothing new
    rec["state"] = state
    rec["back_sha"] = synced_sha
    rec["finished_at"] = now


def view(table: dict[str, dict[str, Any]], now: float) -> dict[str, dict[str, Any]]:
    """The snapshot's ``satellite_upgrades``: every room with an upgrade
    under way, or one that finished within the keep window. Expired
    records are dropped here."""
    out: dict[str, dict[str, Any]] = {}
    for room, rec in list(table.items()):
        _settle(rec, now)
        finished = rec.get("finished_at")
        if rec["state"] != RUNNING and finished is not None and now - finished > KEEP_FINISHED_SEC:
            del table[room]
            continue
        out[room] = {
            "state": rec["state"],
            "expected_sha": rec.get("expected_sha"),
            "from_sha": rec.get("from_sha"),
            "back_sha": rec.get("back_sha"),
            "started_at": _iso(rec.get("started_at")),
            "deadline_at": _iso(rec.get("deadline")),
            "finished_at": _iso(finished),
        }
    return out
