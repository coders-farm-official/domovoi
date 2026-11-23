"""Which path each room sound takes (1.5.0, rooms slice 5b).

A room's sound plays either on the room's synced playout
(``domovoi/room_playout``, every speaker in the room at the same moment)
or down one device's own ``/v1/stream`` socket (today's path). The rules
(design notes §4.7, owner answers 2026-11-22):

* **Music** (the room's MPD stream: music, radio, podcasts, audiobooks,
  Sleep sounds, a shared queue's host): on the playout whenever a live
  device in the room declared ``room_audio`` and has its room-audio socket
  open, in a room with one speaker too (one player for music everywhere).
  Otherwise today's ``music_start`` frame, which a device plays with its
  own stream player (``mpg123``, or ``win_player`` on Windows).
* **Spoken answers, timers, alarms, announcements, intercom, Friend House
  messages, plugin speech**: in a room with more than one live device, on
  the playout (``StreamSession._reply_via_room``; every one of them goes
  through ``StreamSession.announce`` or the reply path). A room with one
  device keeps the direct path: no playout delay before the first word.
* **Wake chime or greeting**: the device that heard the wake word, played
  by the device itself.
* **Drop-in calls**: direct to the one device the call was placed with
  (two-way audio with echo cancellation is that device's); the room's
  music on the playout is stopped for the call and comes back after it,
  by the same ``music_stop`` / music restore the call has always sent.
* A device that cannot play in sync (an older app, a bridged device)
  plays a room's sound only when it is the room's only device. The seam
  for a bridged device's per-device music opt-in is
  :func:`unsynced_music_devices` (nothing sets it yet).

Around a voice turn the room's music is held silent with a timed ``gain``
from the wake word on (the timeline runs on, as MPD plays on, so the music
comes back where the song is by then), and brought back at the first word
of a synced answer (mixed under the speech) or at the turn's music
decision. A person's pause, a skip and a stop are timed ``flush`` es on
every speaker; volume is a ``set_volume`` to every device of the room with
the server time it applies at.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

#: The room's music gain while a turn listens (the wake word to the
#: answer): silent, as the plain stream player was stopped for a turn.
LISTEN_GAIN = 0.0
#: How fast the music fades out at a wake word and back in after.
DUCK_RAMP_MS = 30
#: How far ahead a room volume change is stamped (the playout's control lead).
VOLUME_LEAD_US = 80_000


def playout(app: Any) -> Any | None:
    return getattr(getattr(app, "state", None), "room_playout", None)


def _registry(app: Any) -> Any:
    from domovoi import room_devices

    return room_devices.registry(app)


def plays_room_audio(session: Any) -> bool:
    abilities = getattr(session, "abilities", None)
    return abilities is not None and abilities.allows("room_audio")


def synced_speakers(app: Any, room_id: str) -> list[str]:
    """Devices of the room whose room-audio socket is open now."""
    pl = playout(app)
    return list(pl.speakers(room_id)) if pl is not None else []


def music_on_playout(app: Any, room_id: str) -> bool:
    """The room's music goes to the playout: one of its devices plays
    room audio and is listening on its room-audio socket."""
    return bool(synced_speakers(app, room_id))


def playing_music(app: Any, room_id: str) -> bool:
    pl = playout(app)
    return pl is not None and pl.playing_music(room_id)


def unsynced_music_devices(app: Any, room_id: str) -> list[Any]:
    """Live devices of the room that cannot play in sync but asked for the
    room's music anyway (owner answer: a bridged device's per-device opt-in,
    music only). They get today's ``music_start`` beside the playout. No
    device sets ``unsynced_music_opt_in`` yet: the seam for the bridge."""
    return [s for s in _registry(app).live_in(room_id)
            if not plays_room_audio(s) and getattr(s, "unsynced_music_opt_in", False)]


# ── music ──────────────────────────────────────────────────────────────────


async def start_music(app: Any, session: Any, room_id: str, url: str) -> bool:
    """``send_music_start`` for a room whose music goes to the playout: the
    room's stream on every speaker, MPD resumed (no ``music_ready`` to wait
    for: the server's own decoder is the player). False when the room
    can't (no synced speaker, no stream): the caller sends ``music_start``.

    A start for music already on the playout (a turn's auto-resume) only
    brings it back from the turn's hold; a start while it is paused by a
    person leaves it paused."""
    pl = playout(app)
    if pl is None or not music_on_playout(app, room_id):
        return False
    from domovoi import music_groups
    from domovoi.music_pause import paused_by_person

    host = music_groups.host_of(room_id) or room_id
    if not pl.playing_music(room_id) or pl.music_host.get(room_id) != host:
        if not await pl.play_stream(room_id, host=host):
            return False
    elif not paused_by_person(app, room_id):
        pl.resume_music(room_id)
    # A device that played the plain stream before (its room-audio socket
    # was not open yet) stops it: the playout has the music now.
    for sess in _registry(app).live_in(room_id):
        if getattr(sess, "plain_music_url", None) and plays_room_audio(sess):
            sess.plain_music_url = None
            await _send_raw(sess, {"type": "music_stop"})
    unduck(app, room_id)
    await _resume_player(app, room_id)
    if paused_by_person(app, room_id):
        pl.pause_music(room_id)
    log.info("room sound: room=%s music on the playout (%d speaker(s))",
             room_id, len(pl.speakers(room_id)))
    return True


async def _resume_player(app: Any, room_id: str) -> None:
    """What a device's ``music_ready`` does for the plain stream: cancel a
    pending handshake and resume MPD, unless a person paused it."""
    from domovoi import streaming
    from domovoi.music_pause import paused_by_person

    pending = getattr(app.state, "pending_music_start", None)
    if isinstance(pending, dict):
        entry = pending.pop(room_id, None)
        task = entry.get("task") if entry else None
        if task is not None and not task.done():
            task.cancel()
    streaming.note_satellite_music_silent(app, room_id, False)
    if not paused_by_person(app, room_id):
        await streaming._resume_mpd_for_room(room_id)


async def _send_raw(session: Any, frame: dict[str, Any]) -> None:
    """A frame that must not run ``_safe_send_text``'s music hooks."""
    import json

    try:
        await session.ws.send_text(json.dumps(frame))
    except Exception as e:  # noqa: BLE001 — the socket may be gone
        log.debug("room sound: %s to %s failed: %s", frame.get("type"),
                  getattr(session, "satellite_id", "?"), e)


def stop_music(app: Any, room_id: str) -> bool:
    """A ``music_stop`` for the room: its music on the playout ends on
    every speaker at one time (an answer playing over it plays on)."""
    pl = playout(app)
    if pl is None or not pl.playing_music(room_id):
        return False
    at = pl.stop_music(room_id)
    unduck(app, room_id, at_us=at)
    return True


def music_transport(host: str | None, action: str) -> None:
    """An MPD player did ``action``: rooms playing its stream on the
    playout follow, timed (called by ``domovoi/clients/mpd.py``)."""
    if not host:
        return
    from domovoi.room_playout.service import current

    pl = current()
    if pl is None:
        return
    try:
        rooms = pl.music_transport(host, action)
    except Exception as e:  # noqa: BLE001 — the player's command already ran
        log.warning("room sound: %s for %s's rooms failed: %s", action, host, e)
        return
    if rooms:
        log.info("room sound: %s on %s's player → timed on %s", action, host, ",".join(rooms))


# ── voice turns ────────────────────────────────────────────────────────────


def _ducked(app: Any) -> set[str]:
    d = getattr(app.state, "room_music_ducked", None)
    if not isinstance(d, set):
        d = set()
        app.state.room_music_ducked = d
    return d


def turn_started(app: Any, room_id: str) -> bool:
    """A wake word in the room: its music on the playout falls silent on
    every speaker (timed ``gain``) while the turn listens and answers."""
    pl = playout(app)
    if pl is None or not pl.playing_music(room_id):
        return False
    ducked = _ducked(app)
    if room_id in ducked:
        return False
    ducked.add(room_id)
    pl.duck(room_id, LISTEN_GAIN, ramp_ms=DUCK_RAMP_MS)
    return True


def unduck(app: Any, room_id: str, at_us: float | None = None) -> bool:
    """The turn is over, or its answer starts at ``at_us`` (then the music
    comes back under the answer's first word, mixed down by the streamer)."""
    ducked = _ducked(app)
    if room_id not in ducked:
        return False
    ducked.discard(room_id)
    pl = playout(app)
    if pl is not None and room_id in pl.streamers:
        pl.duck(room_id, 1.0, at_us=at_us, ramp_ms=DUCK_RAMP_MS)
    return True


def speech_starts(app: Any, room_id: str, start_us: float) -> None:
    """A synced answer (or timer, or announcement) is mixed in and plays
    from ``start_us``: ducked music comes back under it from that moment."""
    unduck(app, room_id, at_us=start_us)


# ── volume ─────────────────────────────────────────────────────────────────


async def set_room_volume(app: Any, session: Any, level: int) -> None:
    """A spoken volume change is the room's: every live device of the room
    gets ``set_volume``. Devices that play room audio get ``at_us`` (server
    playout clock) too, so their speakers change together. A lone device
    gets exactly today's frame."""
    from domovoi.room_playout.clock import now_us

    devices = _registry(app).live_in(session.room_id) or [session]
    if session not in devices:
        devices.append(session)
    synced = len(devices) > 1 or music_on_playout(app, session.room_id)
    at = int(now_us() + VOLUME_LEAD_US)
    for dev in devices:
        frame: dict[str, Any] = {"type": "set_volume", "level": level}
        if synced and plays_room_audio(dev):
            frame["at_us"] = at
        if dev is session:
            await session._safe_send_text(frame)
        else:
            await _send_raw(dev, frame)
