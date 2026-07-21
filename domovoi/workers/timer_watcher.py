from __future__ import annotations

import asyncio
import logging
from typing import Any

from domovoi.db.repositories import TimerRepository
from domovoi.db.session import session_scope
from domovoi.workers.base import Worker

log = logging.getLogger(__name__)


class TimerWatcher(Worker):
    """Polls `timers` for expired rows and fires them.

    Plain timers (no message) just get logged for now — a follow-up could add
    a beep frame to the originating room. Reminders (message != NULL) are
    spoken via the originating room's StreamSession.announce(): fan-out
    to the same Pi the user set the reminder from. If the Pi is offline
    when the reminder fires, the message is logged and dropped — there's
    no replay queue.

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

    async def tick(self) -> int:
        """Fire all currently-expired timers. Returns how many fired."""
        async with session_scope() as s:
            expired = await TimerRepository(s).pop_expired()
        for tid, label, message, room_id in expired:
            descriptor = label or f"id={tid}"
            if message:
                log.info(
                    "timer fired (reminder): %s room=%s message=%r",
                    descriptor, room_id, message,
                )
                self._dispatch_reminder(room_id, message)
            else:
                log.info("timer fired: %s room=%s", descriptor, room_id)
        return len(expired)

    def _dispatch_reminder(self, room_id: str | None, message: str) -> None:
        """Speak the reminder through the originating room's Pi if reachable.

        Failures here (Pi offline, broadcast race, TTS hiccup) just log
        and drop — the row is already deleted by `pop_expired`, so there's
        no retry path. Could add a `delivered_at` column + retry queue
        later if "I missed my reminder" complaints accumulate.
        """
        if self.app is None or room_id is None:
            return
        sessions = getattr(self.app.state, "active_sessions", None)
        if not sessions:
            return
        target = sessions.get(room_id)
        if target is None:
            log.warning(
                "reminder fired for offline room=%s; dropping message=%r",
                room_id, message,
            )
            return
        # Schedule on the running loop. The watcher itself runs as an
        # asyncio task, so create_task is safe here.
        asyncio.create_task(
            target.announce(f"Reminder: {message}"),
            name=f"reminder-broadcast-{room_id}",
        )

