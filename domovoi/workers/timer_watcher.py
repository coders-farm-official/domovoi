from __future__ import annotations

import logging
import re
from typing import Any

from domovoi.handlers.timer import _format_duration, spoken_label
from domovoi.workers.base import Worker

log = logging.getLogger(__name__)

# "10 minutes" → "10 minute": the duration is an adjective in the fired line.
_PLURAL_UNIT_RE = re.compile(r"\b(second|minute|hour)s\b")


def _duration_adjective(duration_sec: int) -> str | None:
    """The span a timer or reminder was set for, as the adjective the
    fired line puts before "timer" / "reminder": 600 → "10 minute",
    5400 → "1 hour and 30 minute". None for a span of zero or less."""
    if duration_sec > 0:
        return _PLURAL_UNIT_RE.sub(r"\1", _format_duration(duration_sec))
    return None


def _timer_subject(label: str | None, duration_sec: int) -> str:
    """"Your pasta timer" / "Your 10 minute timer" / "Your timer": the
    subject of every line a plain timer says when it goes off."""
    if label:
        # "the pasta" → "Your pasta timer", not "Your the pasta timer".
        return f"Your {spoken_label(label)} timer"
    spoken = _duration_adjective(duration_sec)
    if spoken:
        return f"Your {spoken} timer"
    return "Your timer"


def _timer_done_text(label: str | None, duration_sec: int) -> str:
    """What a plain timer says when it goes off: the label when the user
    gave one, else the duration in the same wording the "Timer set for ..."
    acknowledgement used, so two unlabelled timers can be told apart."""
    return f"{_timer_subject(label, duration_sec)} is done."


class TimerWatcher(Worker):
    """Polls `timers` for expired rows and fires them.

    The work lives in :class:`domovoi.timer_delivery.TimerDelivery`, which
    the core builds at startup (``app.state.timer_delivery``): each tick
    moves every due row from ``timers`` into the V017 fire ledger in one
    transaction, then announces it in the room it was set in and in every
    other connected room that has not turned on "Only reminders for this
    device" (``timer_own_only_rooms``). A busy room is waited for, an
    offline one gets a grace window to reconnect, and every room's outcome
    is recorded. The rules are in domovoi/timer_delivery.py.

    Plain timers (message NULL) say "Your pasta timer is done." /
    "Your 10 minute timer is done."; reminders (message not NULL) say
    "Reminder: <message>". Other rooms hear the room it came from first:
    "From the garage: Your 10 minute timer is done.". There's no chime —
    the satellite has no frame for one outside a drop-in call — so the
    spoken line is the alert.

    ``app=None`` (tests, standalone runs) still pops and records every due
    timer; it just has no satellite to announce it on.
    """

    # Declarative registration (design §4.5). Runs even under stubs —
    # pure DB poll that has always started unconditionally.
    name = "timer_watcher"
    enabled_setting = None
    interval_setting = "timer_watcher_interval_sec"
    stub_suppressed = False

    def __init__(self, *, app: Any | None = None) -> None:
        self.app = app
        # Used only when the app carries no coordinator of its own (a test
        # handing in a bare app, or app=None).
        self._own_delivery: Any | None = None

    @property
    def delivery(self) -> Any:
        state = getattr(self.app, "state", None) if self.app is not None else None
        shared = getattr(state, "timer_delivery", None)
        if shared is not None:
            return shared
        if self._own_delivery is None:
            # Imported here: timer_delivery imports this module for the
            # fired-line wording.
            from domovoi.timer_delivery import TimerDelivery

            self._own_delivery = TimerDelivery(self.app)
        return self._own_delivery

    async def tick(self) -> int:
        """Fire all currently-expired timers. Returns how many fired."""
        return await self.delivery.tick()
