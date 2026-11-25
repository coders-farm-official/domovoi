"""Look for a new signed Domovoi release by itself (update notices, 1.5.0).

Every ``update_check_tick_sec`` (1 h) the worker asks whether a check is
due and, when it is, runs the same check as the About card's **Check for
updates** (:func:`domovoi.update_notice.run_check`): the git fetch, then
the release's signed manifest (two small files from GitHub), then the
notice. Nothing is ever installed from here.

The gate, in this order, every tick (any "no" and nothing goes out):

1. ``update_auto_check`` is on (Settings → System → About & updates);
2. the internet answer is exactly Yes (``always``). Sometimes, No and not
   answered all skip: not ``internet_allowed()``, which Sometimes passes;
3. the connectivity probe isn't reading offline;
4. no restart or update is under way, and the container guard holds no
   freeze;
5. the checkout is a git checkout.

When: once per process start, 2 minutes after it (never during boot),
skipped if the last successful automatic check is under 6 hours old (or
the last attempt failed under an hour ago), so a crash loop doesn't
hammer GitHub. Then once the last success is older than 23 hours plus
this house's jitter (0 to 120 minutes, picked at start). A failure is
tried again at the next tick.

The offline kit never checks: the kit builder and ``kit_update`` never
import this module, and an offline house stops at step 2 or 3.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, Callable

from domovoi import connectivity, egress, git_version, self_restart, update_notice
from domovoi.config import settings
from domovoi.workers.base import Worker

log = logging.getLogger(__name__)

START_DELAY_SEC = 120.0
START_FLOOR_SEC = 6 * 3600.0
FAILED_FLOOR_SEC = 3600.0
DAILY_SEC = 23 * 3600.0
JITTER_MAX_SEC = 120 * 60.0


class UpdateNoticeChecker(Worker):
    name = "update_notice_check"
    # Resolved on this object (it is its own settings source, see
    # main.py): the tick waits START_DELAY_SEC for the start check, then
    # settings.update_check_tick_sec.
    interval_setting = "update_check_tick_sec"
    stub_suppressed = True
    requires_online = False   # gated in tick(), step by step

    def __init__(self, *, clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time, jitter_sec: float | None = None) -> None:
        self._clock = clock
        self._wall = wall
        self._started = clock()
        # The start delay is over (the tick then waits the setting); the
        # first look at whether a check is due has been made (the start rules).
        self._delay_over = False
        self._start_done = False
        self.jitter_sec = random.uniform(0, JITTER_MAX_SEC) if jitter_sec is None else jitter_sec

    @property
    def update_check_tick_sec(self) -> float:
        tick = float(settings.update_check_tick_sec or 3600.0)
        if self._delay_over:
            return tick
        left = START_DELAY_SEC - (self._clock() - self._started)
        return max(1.0, min(tick, left)) if left > 0 else 1.0

    async def gate(self) -> str | None:
        """Why this tick sends nothing, or None to go on."""
        if not settings.update_auto_check:
            return "setting_off"
        if egress.policy() != "always":
            return "internet_not_yes"
        probe = connectivity.current_probe()
        if probe is not None and not getattr(probe, "online", True):
            return "offline"
        try:
            last = await asyncio.to_thread(git_version.read_last_update)
            if await asyncio.to_thread(self_restart.underway, last):
                return "updating"
        except Exception:  # noqa: BLE001 — can't tell: don't add a fetch to an update
            return "updating"
        try:
            from domovoi.container_guard import freeze

            if await freeze.is_frozen():
                return "frozen"
        except Exception:  # noqa: BLE001 — the guard's store is away: carry on
            pass
        if await git_version.current_sha() == "unknown":
            return "no_git"
        return None

    def due(self) -> bool:
        """Whether a check is due now (call after the state is loaded)."""
        now = self._wall()
        last_at, ok_at, last_ok = update_notice.last_auto_times()
        if not self._start_done:
            if ok_at is not None and now - ok_at < START_FLOOR_SEC:
                return False
            if last_ok is False and last_at is not None and now - last_at < FAILED_FLOOR_SEC:
                return False
            return True
        if last_ok is False:
            return True
        return ok_at is None or now - ok_at >= DAILY_SEC + self.jitter_sec

    async def tick(self) -> dict[str, Any]:
        if not self._delay_over:
            if self._clock() - self._started < START_DELAY_SEC:
                return {"checked": 0, "skipped": "starting"}
            self._delay_over = True
        why = await self.gate()
        if why is not None:
            return {"checked": 0, "skipped": why}
        await update_notice.load_state()
        due = self.due()
        self._start_done = True
        if not due:
            return {"checked": 0, "skipped": "not_due"}
        if update_notice.busy():
            return {"checked": 0, "skipped": "busy"}
        await update_notice.run_check("auto")
        return {"checked": 1, "skipped": None}
