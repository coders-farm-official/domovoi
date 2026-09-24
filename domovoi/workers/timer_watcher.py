from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from domovoi.db.repositories import TimerRepository
from domovoi.db.session import session_scope
from domovoi.handlers.timer import _format_duration
from domovoi.workers.base import Worker

log = logging.getLogger(__name__)

# "timer for 10 minutes for the pasta" stores the label "the pasta"; the
# fired line reads "Your pasta timer", not "Your the pasta timer".
_LEADING_ARTICLE_RE = re.compile(r"^(?:the|my|a|an)\s+", re.IGNORECASE)
# "10 minutes" → "10 minute": the duration is an adjective in the fired line.
_PLURAL_UNIT_RE = re.compile(r"\b(second|minute|hour)s\b")


def _timer_done_text(label: str | None, duration_sec: int) -> str:
    """What a plain timer says when it goes off: the label when the user
    gave one, else the duration in the same wording the "Timer set for ..."
    acknowledgement used, so two unlabelled timers can be told apart."""
    if label:
        return f"Your {_LEADING_ARTICLE_RE.sub('', label.strip())} timer is done."
    if duration_sec > 0:
        spoken = _PLURAL_UNIT_RE.sub(r"\1", _format_duration(duration_sec))
        return f"Your {spoken} timer is done."
    return "Your timer is done."


class TimerWatcher(Worker):
    """Polls `timers` for expired rows and fires them.

    Both kinds are spoken via the originating room's
    StreamSession.announce(): fan-out to the same Pi the user set them
    from. Plain timers (message NULL) say "Your pasta timer is done." /
    "Your 10 minute timer is done."; reminders (message != NULL) say
    "Reminder: <message>". There's no chime — the satellite has no frame
    for one outside a drop-in call — so the spoken line is the alert.

    If the Pi is offline when a timer fires, or the announce fails (the Pi
    is mid-response, the WebSocket died), the line is logged and dropped —
    `pop_expired` already deleted the row and there's no replay queue.

    The ``app`` reference is used to look up active satellite sessions;
    pass None in tests / standalone runs to keep the log-only path.
    """

    # Declarative registration (design §4.5). Runs even under stubs —
    # pure DB poll that has always started unconditionally.
    name = "timer_watcher"
    enabled_setting = None
    interval_setting = "timer_watcher_interval_sec"
    stub_suppressed = False

    def __init__(self, *, app: Any | None = None) -> None:
        self.app = app
        # Strong refs to in-flight announce tasks — the loop only holds
        # weak ones, so an unreferenced task can be collected mid-send.
        self._inflight: set[asyncio.Task[None]] = set()

    async def tick(self) -> int:
        """Fire all currently-expired timers. Returns how many fired."""
        async with session_scope() as s:
            expired = await TimerRepository(s).pop_expired()
        for tid, label, message, room_id, created_at, expires_at in expired:
            descriptor = label or f"id={tid}"
            if message:
                log.info(
                    "timer fired (reminder): %s room=%s message=%r",
                    descriptor, room_id, message,
                )
                self._dispatch_reminder(room_id, message)
            else:
                log.info("timer fired: %s room=%s", descriptor, room_id)
                duration_sec = round((expires_at - created_at).total_seconds())
                text = _timer_done_text(label, duration_sec)
                self._announce(room_id, text, kind="timer", dropping=repr(text))
        return len(expired)

    def _dispatch_reminder(self, room_id: str | None, message: str) -> None:
        self._announce(
            room_id, f"Reminder: {message}",
            kind="reminder", dropping=f"message={message!r}",
        )

    def _announce(
        self, room_id: str | None, text: str, *, kind: str, dropping: str,
    ) -> None:
        """Speak ``text`` through the originating room's Pi if reachable.

        Failures here (Pi offline, mid-response skip, broadcast race, TTS
        hiccup) just log and drop — the row is already deleted by
        `pop_expired`, so there's no retry path. Could add a `delivered_at`
        column + retry queue later if "I missed my timer" complaints
        accumulate.
        """
        if self.app is None or room_id is None:
            return
        sessions = getattr(self.app.state, "active_sessions", None)
        if sessions is None:
            return
        target = sessions.get(room_id)
        if target is None:
            # An empty registry is the common case here — a one-satellite
            # house whose only Pi is down — so it logs like any offline room.
            log.warning(
                "%s fired for offline room=%s; dropping %s",
                kind, room_id, dropping,
            )
            return
        # Schedule on the running loop. The watcher itself runs as an
        # asyncio task, so create_task is safe here.
        task = asyncio.create_task(
            target.announce(text), name=f"{kind}-broadcast-{room_id}",
        )
        self._inflight.add(task)
        task.add_done_callback(self._announce_done)

    def _announce_done(self, task: asyncio.Task[None]) -> None:
        # announce() raises when it can't deliver (Pi mid-response, dead
        # WebSocket). Retrieve it here so the drop is one readable line,
        # not an unretrieved-exception traceback at garbage collection.
        self._inflight.discard(task)
        exc = None if task.cancelled() else task.exception()
        if exc is not None:
            log.warning("%s failed; dropping: %s", task.get_name(), exc)

