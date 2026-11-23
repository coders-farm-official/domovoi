"""Suggesting "rooms that can hear each other" (1.5.0, design §3.5).

Wake claims stay within a room unless an admin links two rooms
(``room_hear_links``, owner answer Q3: opt-in, with a suggestion). The
suggestion comes from what the house hears: when one "hey Domovoi" wakes
satellites in two unlinked rooms at nearly the same moment, both rooms
answered it. After :data:`THRESHOLD` such wakes within :data:`WINDOW_DAYS`
the Rooms page suggests linking them; the admin links them or dismisses the
suggestion, and a dismissal is remembered (that pair is never suggested
again; linking it by hand still works).

:class:`NearWakes` spots the pairs (in memory, fed by
``wake_claims.Arbiter``); the counts and dismissals are kept in
``household_settings`` under :data:`KEY` (V045) so they survive a restart.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Any, Callable

from sqlalchemy import text

log = logging.getLogger(__name__)

KEY = "room_hear_suggest"
#: Wakes in two rooms this close together are one wake heard in both.
NEAR_SEC = 1.0
#: This many near-simultaneous wakes ...
THRESHOLD = 3
#: ... within this many days make a suggestion.
WINDOW_DAYS = 14
#: Wake times kept per pair (enough for the threshold).
KEEP_TIMES = 10


def pair_key(a: str, b: str) -> str:
    lo, hi = sorted((a, b))
    return f"{lo}|{hi}"


class NearWakes:
    """Spots wakes in two rooms within :data:`NEAR_SEC` of each other.
    ``found(a, b)`` is called once per such wake for each pair of rooms
    not in one group."""

    def __init__(self, found: Callable[[str, str], None], *,
                 near_sec: float = NEAR_SEC) -> None:
        self.found = found
        self.near_sec = near_sec
        self._last: dict[str, float] = {}        # room -> its latest wake
        self._counted: dict[str, float] = {}     # pair -> when it was counted

    def heard(self, room_id: str, group: frozenset[str], now: float) -> list[tuple[str, str]]:
        hits: list[tuple[str, str]] = []
        for other, at in list(self._last.items()):
            if other == room_id or other in group or now - at > self.near_sec:
                continue
            key = pair_key(room_id, other)
            # Several devices of one room waking on the same word count once.
            if now - self._counted.get(key, -1e18) <= 3 * self.near_sec:
                continue
            self._counted[key] = now
            hits.append(tuple(sorted((room_id, other))))  # type: ignore[arg-type]
        self._last[room_id] = now
        for a, b in hits:
            try:
                self.found(a, b)
            except Exception as e:  # noqa: BLE001 — a wake never fails on this
                log.warning("hear links: noting %s/%s failed: %s", a, b, e)
        return hits


# ── the stored counts ─────────────────────────────────────────────────────


async def _load(s: Any) -> dict[str, Any]:
    row = (await s.execute(text("SELECT value FROM household_settings WHERE key = :k"),
                           {"k": KEY})).first()
    value = row[0] if row is not None else None
    if isinstance(value, str):
        value = json.loads(value)
    pairs = value.get("pairs") if isinstance(value, dict) else None
    return pairs if isinstance(pairs, dict) else {}


async def _save(s: Any, pairs: dict[str, Any]) -> None:
    await s.execute(text(
        "INSERT INTO household_settings (key, value, updated_at) "
        "VALUES (:k, CAST(:v AS jsonb), now()) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"
    ), {"k": KEY, "v": json.dumps({"pairs": pairs})})


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _recent(times: list[Any], now: dt.datetime) -> list[str]:
    cut = now - dt.timedelta(days=WINDOW_DAYS)
    out = []
    for t in times:
        try:
            if dt.datetime.fromisoformat(str(t)) >= cut:
                out.append(str(t))
        except ValueError:
            continue
    return out


async def record(s: Any, a: str, b: str, *, now: dt.datetime | None = None) -> int:
    """One near-simultaneous wake in rooms ``a`` and ``b``; returns the
    pair's count within the window."""
    now = now or _now()
    pairs = await _load(s)
    entry = pairs.get(pair_key(a, b))
    entry = dict(entry) if isinstance(entry, dict) else {}
    times = _recent(list(entry.get("times") or []), now)
    times.append(now.isoformat(timespec="seconds"))
    entry["times"] = times[-KEEP_TIMES:]
    pairs[pair_key(a, b)] = entry
    await _save(s, pairs)
    return len(entry["times"])


async def dismiss(s: Any, a: str, b: str) -> None:
    """The admin said no: the pair is never suggested again."""
    pairs = await _load(s)
    pairs[pair_key(a, b)] = {"dismissed": True, "times": []}
    await _save(s, pairs)


async def forget(s: Any, a: str, b: str) -> None:
    """The pair was linked: its count starts again (a later unlink then
    needs fresh evidence before it is suggested)."""
    pairs = await _load(s)
    entry = pairs.get(pair_key(a, b))
    if isinstance(entry, dict) and not entry.get("dismissed"):
        pairs.pop(pair_key(a, b), None)
        await _save(s, pairs)


async def suggestions(s: Any, *, now: dt.datetime | None = None) -> list[dict[str, Any]]:
    """Pairs heard together at least :data:`THRESHOLD` times within the
    window, not dismissed, not linked, both rooms still there."""
    now = now or _now()
    pairs = await _load(s)
    if not pairs:
        return []
    rooms = {r[0] for r in (await s.execute(text("SELECT room_id FROM rooms"))).all()}
    linked = {pair_key(r[0], r[1]) for r in
              (await s.execute(text("SELECT room_a, room_b FROM room_hear_links"))).all()}
    out = []
    for key, entry in sorted(pairs.items()):
        if not isinstance(entry, dict) or entry.get("dismissed") or key in linked:
            continue
        a, _, b = key.partition("|")
        if a not in rooms or b not in rooms:
            continue
        n = len(_recent(list(entry.get("times") or []), now))
        if n >= THRESHOLD:
            out.append({"rooms": [a, b], "count": n})
    return out


def persist_in_background(a: str, b: str) -> None:
    """``NearWakes.found`` for the app: write the count off the wake path."""
    import asyncio

    async def _write() -> None:
        from domovoi.db.session import session_scope

        try:
            async with session_scope() as s:
                n = await record(s, a, b)
            log.info("hear links: %s and %s woke together (%d in %d days)",
                     a, b, n, WINDOW_DAYS)
        except Exception as e:  # noqa: BLE001
            log.warning("hear links: keeping the count for %s/%s failed: %s", a, b, e)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(_write())
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


_TASKS: set[Any] = set()
