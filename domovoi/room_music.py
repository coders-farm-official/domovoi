"""Getting a room's music to that room's satellite when the turn that asked
for it was not that room's.

A voice turn's own music rides home on its own socket
(``StreamSession._send_response``). Two kinds of request have no such
socket for the room they are about:

* the dashboard's and the app's room controls (``main._admin_dispatch_music``,
  which calls :func:`dispatch_music`), and
* a voice turn that names ANOTHER room — "play jazz in the garage" said in
  the dining room (``MusicHandler``'s room paths): the music is the
  garage's, the reply is the dining room's. The handler prepares the
  garage's player and hands the frames to :func:`deliver_later`, so the
  dining room hears "Playing jazz in the garage." while the garage's
  satellite gets its ``music_start``.

Everything here works from ``app.state`` (the live sessions and the music
registries the streaming layer keeps) and the room's MPD; it opens no
database session of its own.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from domovoi.models import Response
from domovoi.now_playing import NOW_PLAYING

log = logging.getLogger(__name__)

# Deliveries still running (a background task must be referenced until it
# ends, or the event loop may drop it). Tests wait on them with drain().
_DELIVERIES: set[asyncio.Task] = set()


async def dispatch_music(
    app: Any, response: Response, room_id: str, *, start_paused: bool = False,
) -> None:
    """Deliver the music_start / music_stop frame to the room's Pi.

    ``start_paused`` (a cast from a phone or browser that was paused): the
    satellite's player still gets its music_start and joins the stream, but
    MPD stays paused where the handler left it — recorded as a person's
    pause (domovoi/music_pause.py), so neither the music_ready handshake,
    its fallback, nor a later turn's auto-resume starts it. The person's
    play (``resume``) does, and is heard at once.

    Voice turns get this for free in ``StreamSession._send_response`` —
    the frame rides the same WebSocket the utterance came in on. The
    admin path has no inbound WS, so without this helper MPD starts
    decoding on Domovoi but the Pi never spawns mpg123 and the speaker
    stays silent. Symptom: the dashboard's now-playing card shows the
    stream as ``play`` with the elapsed time advancing while no audio
    reaches the room.

    Updates ``resumable_music`` regardless of whether the Pi is currently
    connected — a later reconnect (or the next response turn) will pull
    from there and auto-resume.
    """
    from domovoi.music_pause import note_paused_by_person
    from domovoi.streaming import _resume_mpd_for_room, send_music_start

    sessions: dict[str, Any] = app.state.active_sessions
    resumable: dict[str, str] = app.state.resumable_music
    current_pl: dict[str, Any] = app.state.current_playlist
    sess = sessions.get(room_id)
    if response.music_action == "start" and response.music_stream_url:
        resumable[room_id] = response.music_stream_url
        # Something new was asked for: a pause from before no longer holds
        # — unless what was asked for is to wait paused (`start_paused`),
        # recorded BEFORE the frame goes so its music_ready finds it.
        note_paused_by_person(app, room_id, start_paused)
        # No now-playing stamp clear on start: matched_handler can't be
        # trusted here — "play creep" routes through MusicHandler,
        # which may delegate to a streaming provider that overwrites
        # matched_handler back to "music" (music.py cascade).
        # The web backend's stream_url-vs-file freshness check makes
        # the dashboard pill follow the real source instantly anyway;
        # the playback-state sweeper handles stamp hygiene within 5 s.
        # Same logic for current_playlist — the sweeper invalidates
        # entries whose last_file_path doesn't match MPD currentsong
        # once the new track is actually playing.
        if sess is not None:
            # The same helper the voice path uses: the stream is made to
            # serve first (a cast is very often the first play since the
            # room's MPD daemon started, which is exactly when it doesn't),
            # then the music_start, then the prepare/resume handshake so
            # admin "Play in {room}" clicks don't stutter either. Through
            # the session's `start_music`: a room that is listening (a
            # capture, a question's follow-up window) or answering gets the
            # start once it is free instead of now — a player started under
            # an open capture is heard as one long sentence.
            start = getattr(sess, "start_music", None)
            if start is not None:
                await start(response.music_stream_url)
            else:
                await send_music_start(
                    app, sess, room_id, response.music_stream_url,
                )
        elif not start_paused:
            # No Pi connected to consume the stream. The handler queued
            # MPD paused; without a satellite to send music_ready, the
            # song would sit paused indefinitely. Resume now so the
            # next reconnect's auto-resume joins a live stream. (Asked to
            # wait paused, it waits.)
            await _resume_mpd_for_room(room_id)
    elif response.music_action == "stop":
        resumable.pop(room_id, None)
        note_paused_by_person(app, room_id, False)
        # Generic stamp pop (design §4.7) — no provider-specific
        # state dicts to clear.
        NOW_PLAYING.clear(room_id)
        current_pl.pop(room_id, None)
        pending: dict[str, dict[str, Any]] = app.state.pending_music_start
        stale = pending.pop(room_id, None)
        if stale is not None:
            stale_task = stale.get("task")
            if stale_task is not None and not stale_task.done():
                stale_task.cancel()
        if sess is not None:
            await sess._safe_send_text({"type": "music_stop"})


async def _deliver(
    app: Any, room_id: str, music_action: str | None, stream_url: str | None,
    volume_percent: int | None,
) -> None:
    if volume_percent is not None:
        sess = app.state.active_sessions.get(room_id)
        if sess is not None:
            try:
                await sess.set_output_volume(int(volume_percent))
            except Exception as e:  # noqa: BLE001 — the room's socket went
                log.warning("room music: volume for room=%s failed: %s", room_id, e)
    if music_action in ("start", "stop"):
        await dispatch_music(
            app,
            Response(text="", music_action=music_action, music_stream_url=stream_url),
            room_id,
        )


def deliver_later(
    app: Any,
    room_id: str,
    *,
    music_action: str | None = None,
    stream_url: str | None = None,
    volume_percent: int | None = None,
) -> asyncio.Task | None:
    """Send ``room_id``'s satellite what a turn from ANOTHER room decided
    for it: the ``music_start`` / ``music_stop`` (through
    :func:`dispatch_music`, so a busy room gets its start once it is free)
    and a new output volume (``set_volume``). Runs beside the asking room's
    reply instead of before it: readying a stream can take up to
    ``music_stream_ready_timeout_sec``, which the person who asked should
    not wait through in silence. None when there is nothing to send."""
    if app is None or (music_action is None and volume_percent is None):
        return None

    async def _run() -> None:
        try:
            await _deliver(app, room_id, music_action, stream_url, volume_percent)
        except Exception as e:  # noqa: BLE001 — logged; the turn has answered
            log.warning("room music: delivery to room=%s failed: %s", room_id, e)

    task = asyncio.create_task(_run(), name=f"room-music-{room_id}")
    _DELIVERIES.add(task)
    task.add_done_callback(_DELIVERIES.discard)
    return task


async def drain() -> None:
    """Wait for every delivery started so far (tests)."""
    while _DELIVERIES:
        await asyncio.gather(*list(_DELIVERIES), return_exceptions=True)
