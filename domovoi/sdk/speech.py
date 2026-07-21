"""SpeechAPI — out-of-turn announcements to satellite rooms (SDK 1.1).

Plugins sometimes finish work AFTER their turn's Response has been
spoken — a background summarization, a long download, a print
completing. This API lets them push a spoken announcement to a room
through the exact fan-out the core timers and ``/v1/admin/announce``
use: ``StreamSession.announce``, which skips a Pi mid-response (its
in-flight TTS would clip the announcement) and auto-restores resumable
music afterwards.

The active-session map lives on the FastAPI app state; the core binds a
provider at startup (``bind_active_sessions`` in main.py's lifespan).
Before binding — or in the web process, where this API must never be
used — ``announce`` reports zero rooms reached rather than raising.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

log = logging.getLogger(__name__)

_active_sessions_provider: Callable[[], dict[str, Any]] | None = None


def bind_active_sessions(provider: Callable[[], dict[str, Any]]) -> None:
    """Called once by core startup with a live view of
    ``app.state.active_sessions`` (room_id → StreamSession)."""
    global _active_sessions_provider
    _active_sessions_provider = provider


class SpeechAPI:
    """Injected as ``sdk.speech`` — never constructed by plugins."""

    def __init__(self, slug: str = "core") -> None:
        self.slug = slug

    async def announce(self, room_id: str | None, text: str) -> list[str]:
        """Speak ``text`` in ``room_id`` (or every connected room when
        None). Returns the room_ids actually reached — empty when the
        target isn't connected. Never raises for delivery problems:
        an announcement is best-effort by nature (the Pi may drop WiFi
        between the check and the play), so callers branch on the
        returned list, not on exceptions."""
        if not (text or "").strip():
            return []
        if _active_sessions_provider is None:
            log.debug("speech.announce before binding — no satellites reachable")
            return []
        sessions = _active_sessions_provider() or {}
        if room_id is None:
            targets = list(sessions.values())
        else:
            target = sessions.get(room_id)
            targets = [target] if target is not None else []

        reached: list[str] = []
        for sess in targets:
            try:
                await sess.announce(text)
                reached.append(sess.room_id)
            except Exception as e:  # noqa: BLE001 — per-room isolation
                log.warning(
                    "speech.announce (plugin %s) failed for room=%s: %s",
                    self.slug, getattr(sess, "room_id", "?"), e,
                )
        return reached
