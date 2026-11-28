"""The room display (1.4.0): a phone or tablet in room mode showing what
the room is playing, its controls, and its timers and alarms.

A room phone holds only its room key, which the core accepts on the hello
of ``/v1/stream/{room}`` and nowhere else, so everything goes over that
socket. Only a device whose hello listed the ability ``room_display``
(domovoi/satellite_abilities.py), on a socket that proved its room with
the pairing token, gets any of it; ``ready.features`` then lists
``room_display``. A Pi (no ``supports``) never does.

**Server → room**, ``room_display`` (:func:`build_frame`)::

    {"type": "room_display",
     "sent_at_ms": <this server's monotonic clock, ms: for ordering only>,
     "now_playing": null | {
         "state": "play" | "pause",
         "title", "artist", "album",          # strings or null
         "source": "library" | "radio" | "sleep" | "podcast" | "audiobook" | "other",
         "art": null | "/v1/music/cover/{track_id}",
         "duration_sec", "elapsed_sec",       # numbers or null
         "controls": [subset of "pause", "resume", "skip", "previous"]},
     "timers": [{"id", "kind": "timer" | "alarm", "label", "remaining_ms", "total_ms"}],
     "ringing": null | {"fire_id", "kind": "timer" | "alarm", "label"},
     "media": null | {                        # 1.5.0: photo and video results
         "id": <int, new for each set of results>,
         "said": <the spoken answer, a string>,
         "count": <int>, "more": <bool>,
         "expires_in_ms": <int: hide them by then even with no new frame>,
         "items": [{"kind": "image" | "video",
                    "thumb": "/v1/room-media/{token}",   # short-lived, no credential
                    "caption": <string or null: what it MIGHT show>,
                    "date": <"YYYY-MM-DD" or null>,
                    "at_s": <number or null: a video's best moment>}]}}

* Sent once after ``ready``, then whenever it CHANGES: this room's music
  (its group host's, in a group: every member shows the same), its timers
  or alarms, or one going off here. At most one a second
  (:data:`MIN_INTERVAL_SEC`). Time passing is not a change: the phone
  counts ``elapsed_sec`` up and ``remaining_ms`` down itself; a seek, or a
  clock that drifted more than :data:`DRIFT_SEC`, is.
* ``controls``: only what works in this room right now (:func:`controls_for`):
  pause while playing, resume while paused; skip when the queue has a next
  song; previous for a song (it goes back, or starts the first song again).
  A stream (the radio) can only pause and resume.
* ``timers``: this room's timers and alarms, never a reminder (anyone in
  the room can read the screen, and a reminder's words are household
  speech). An alarm is a reminder with the label the reminder handler gives
  an alarm ("7 AM alarm"). Remaining time, never a wall clock.
* ``ringing``: a timer or alarm going off here that nobody has acknowledged,
  within the window "stop the timer" takes (timer_delivery.ringing_fire).
* ``media`` (1.5.0, domovoi/room_media.py): the results of "show me photos
  of the beach" said in this room, at most 6, for 90 seconds; null
  otherwise. Gone with the next frame sent for any other reason (music,
  a timer), when newer results replace them, or after ``expires_in_ms``.
  A caption is a picture model's guess and untrusted text: draw it as
  text, never markup, as "Might show: …". ``thumb`` is a path on this
  server, good for 10 minutes, and needs no credential. A client from
  before 1.5.0 ignores the key.
* ``art``: a library song's cover on this server's open cover route
  (``GET /v1/music/cover/{track_id}``); null for anything else (a stream,
  a plugin's item): never guessed.

**Room → server**, ``room_control`` (:meth:`RoomDisplayFeed.on_control`),
authorised by the socket (no room in the frame):

* ``{"type": "room_control", "action": "pause"|"resume"|"skip"|"previous"}``
  goes the way the dashboard's ``POST /v1/admin/music/{action}/{room}`` does
  (the spoken equivalent, routed for this room, so in a group it acts on
  the group, as saying it there would). Only an action the last frame
  offered is taken.
* ``{"type": "room_control", "action": "stop_ringing", "fire_id": N}``
  acknowledges that fire here (timer_delivery.ack_recent_fire: a timer or
  an alarm) and ends its announcement in this room.

Nothing is answered: the next ``room_display`` shows the result. A frame
that is not exactly one of these is ignored (logged at debug), as is one
past the rate limit (:data:`CONTROL_BURST` at once, then one per
:data:`CONTROL_REFILL_SEC`).

Volume is not here: the room reports and takes it with ``volume_status`` /
``set_volume``, as before.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from pathlib import PurePosixPath
from typing import Any, Awaitable, Callable

from sqlalchemy import text

from domovoi import satellite_abilities
from domovoi.events import EVENTS, Event

log = logging.getLogger(__name__)

ABILITY = "room_display"

#: Never two frames closer than this.
MIN_INTERVAL_SEC = 1.0
#: How often the room's player and timers are read when nothing pokes the
#: feed (a song the player moved on to by itself, a timer set from the
#: dashboard).
POLL_SEC = 2.0
#: A song's position or a timer's end that moved by more than this from
#: where the last frame put it (a seek, a clock that drifted) is a change.
DRIFT_SEC = 2.5
#: Timers listed at most (soonest first).
MAX_TIMERS = 20
#: Longest text sent (a title, a label).
MAX_TEXT = 200

#: room_control: this many at once, then one per CONTROL_REFILL_SEC.
CONTROL_BURST = 5
CONTROL_REFILL_SEC = 0.5

#: The transport verbs and what each says, as the dashboard's route sends
#: them (domovoi/main.py ``_MUSIC_ACTIONS``).
MUSIC_TRANSCRIPTS: dict[str, str] = {
    "pause": "pause the music",
    "resume": "resume",
    "skip": "next",
    "previous": "previous",
}
STOP_RINGING = "stop_ringing"

#: What pokes every feed at once: the music or a timer may have changed.
_POKE_EVENTS = (
    "core.now_playing_stamped",
    "core.now_playing_cleared",
    "core.timer_fired",
    "core.timer_fire_settled",
    "core.turn_completed",
)


def wanted(session: Any) -> bool:
    """Whether this session gets the room display: it declared the ability
    and proved its room with the pairing token."""
    return (
        satellite_abilities.session_abilities(session).allows(ABILITY)
        and getattr(session, "token_authenticated", False) is True
    )


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:MAX_TEXT] if value else None


def _num(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) and f >= 0 else None


# ─── now playing ─────────────────────────────────────────────────────────


def _is_stream(uri: str | None) -> bool:
    return bool(uri) and "://" in uri  # type: ignore[operator]


def _source(uri: str | None, library_id: int | None, stamp: Any) -> str:
    """The generic source name the screen shows: never a provider's."""
    if library_id is not None:
        return "library"
    if _is_stream(uri):
        slug = str(getattr(stamp, "source", "") or "").lower()
        if slug == "radio":
            return "radio"
        if slug.startswith("sleep"):
            return "sleep"
        if slug.startswith("podcast"):
            return "podcast"
        if slug.startswith("audiobook"):
            return "audiobook"
        return "other"
    head = (uri or "").replace("\\", "/").lstrip("/").split("/", 1)[0]
    if head == "podcasts":
        return "podcast"
    if head == "audiobooks":
        return "audiobook"
    return "other"


def controls_for(state: str, stream: bool, pos: int | None, length: int | None) -> list[str]:
    """What works in a room right now, as the dashboard's room card and the
    guest gate allow it: the play/pause that matches the state; for a song
    in a queue, skip when there is a next one and previous (back one song,
    or the first one again from its start). A stream can only pause and
    resume: a skip would end it, and there is nothing before it."""
    out = ["pause" if state == "play" else "resume"]
    if stream or pos is None:
        return out
    if length is not None and pos + 1 < length:
        out.append("skip")
    out.append("previous")
    return out


async def _library_row(session: Any, uri: str | None) -> tuple[int, str | None, str | None, str | None, float | None] | None:
    """The library song the player's file is (id, title, artist, album,
    duration), by the listen logger's rule
    (domovoi.workers.listen_logger.track_for_file); None for anything else."""
    if not uri or "://" in uri:
        return None
    rel = uri.replace("\\", "/").lstrip("/")
    row = (
        await session.execute(
            text(
                """
                SELECT id, title, artist, album, duration_sec FROM library_tracks
                WHERE right(replace(file_path, chr(92), '/'), length(:rel) + 1) = '/' || :rel
                   OR replace(file_path, chr(92), '/') = :rel
                ORDER BY length(file_path)
                LIMIT 1
                """
            ),
            {"rel": rel},
        )
    ).first()
    if row is None:
        return None
    return int(row[0]), row[1], row[2], row[3], _num(row[4])


def _player_for(room_id: str) -> Any:
    """The player this room hears: its group host's, else its own; never
    another room's (``get_mpd_client_for`` falls back to the first room)."""
    from domovoi import music_groups
    from domovoi.clients.mpd import own_player

    return own_player(music_groups.host_of(room_id) or room_id)


async def now_playing(session: Any, room_id: str) -> dict[str, Any] | None:
    """This room's ``now_playing`` (see the module docstring), or None when
    nothing is playing or paused, or the room has no player."""
    from domovoi.now_playing import NOW_PLAYING

    player = _player_for(room_id)
    if player is None:
        return None
    snap = await player.playback_snapshot()
    state = snap.get("state")
    if state not in ("play", "pause"):
        return None
    song = await player.current_song() or {}
    uri = snap.get("file") or song.get("file")
    uri = uri if isinstance(uri, str) else None
    stream = _is_stream(uri)

    stamp = NOW_PLAYING.get(room_id)
    if stamp is not None and stream:
        stamped_url = (stamp.data or {}).get("stream_url")
        if stamped_url and stamped_url != uri:
            stamp = None  # an older play's stamp: not this stream's

    def tag(*keys: str) -> str | None:
        for key in keys:
            value = _text(song.get(key))
            if value:
                return value
        return None

    library = await _library_row(session, uri)
    track_id = library[0] if library is not None else None
    title = tag("Title", "title")
    artist = tag("Artist", "artist")
    album = tag("Album", "album")
    duration = _num(snap.get("duration"))
    if library is not None:
        title = _text(library[1]) or title
        artist = _text(library[2]) or artist
        album = _text(library[3]) or album
        duration = duration or library[4]
    if stream:
        # A stream's name is the station's (the plugin's stamp), else what
        # the stream calls itself. No album, no artist, no length.
        stamped_title = _text((stamp.data or {}).get("title")) if stamp is not None else None
        title = stamped_title or tag("Name", "name") or title
        artist = album = None
        duration = None
    elif title is None and uri:
        title = _text(PurePosixPath(uri.replace("\\", "/")).stem)

    elapsed = _num(snap.get("elapsed"))
    return {
        "state": state,
        "title": title,
        "artist": artist,
        "album": album,
        "source": _source(uri, track_id, stamp),
        "art": f"/v1/music/cover/{track_id}" if track_id is not None else None,
        "duration_sec": duration if duration and duration > 0 else None,
        "elapsed_sec": round(elapsed, 1) if elapsed is not None else None,
        "controls": controls_for(state, stream, snap.get("pos"), snap.get("playlistlength")),
    }


# ─── timers and alarms ───────────────────────────────────────────────────


async def timers(session: Any, room_id: str) -> list[dict[str, Any]]:
    """This room's timers and alarms, soonest first; never a reminder."""
    from domovoi.handlers.reminder import ALARM_LABEL_SQL_RE
    from domovoi.handlers.timer import spoken_label

    rows = (
        await session.execute(
            text(
                """
                SELECT id, label, message IS NULL AS is_timer,
                       GREATEST(0, EXTRACT(EPOCH FROM (expires_at - now())) * 1000),
                       EXTRACT(EPOCH FROM (expires_at - created_at)) * 1000
                  FROM timers
                 WHERE room_id = :room
                   AND (message IS NULL OR lower(label) ~ :alarm_label)
                 ORDER BY expires_at ASC, id ASC
                 LIMIT :limit
                """
            ),
            {"room": room_id, "alarm_label": ALARM_LABEL_SQL_RE, "limit": MAX_TIMERS},
        )
    ).all()
    out: list[dict[str, Any]] = []
    for r in rows:
        is_timer = bool(r[2])
        label = _text(r[1])
        if label is not None and is_timer:
            label = _text(spoken_label(label))
        total = _num(r[4])
        out.append({
            "id": int(r[0]),
            "kind": "timer" if is_timer else "alarm",
            "label": label,
            "remaining_ms": int(round(float(r[3] or 0))),
            "total_ms": int(round(total)) if total else None,
        })
    return out


async def ringing(session: Any, room_id: str) -> dict[str, Any] | None:
    """A timer or alarm going off here, unacknowledged; else None."""
    from domovoi.handlers.timer import spoken_label
    from domovoi.timer_delivery import ringing_fire

    fire = await ringing_fire(session, room_id)
    if fire is None:
        return None
    label = _text(fire.label)
    if label is not None and fire.kind == "timer":
        label = _text(spoken_label(label))
    return {"fire_id": fire.fire_id, "kind": fire.kind, "label": label}


def _mono_ms() -> int:
    return int(time.monotonic() * 1000)


async def build_frame(room_id: str, *, mono_ms: Callable[[], int] = _mono_ms) -> dict[str, Any]:
    """One ``room_display`` frame for ``room_id`` (module docstring). A
    part that cannot be read right now (the player, the database) raises:
    the feed sends nothing rather than a frame that says "nothing"."""
    from domovoi.db.session import session_scope

    from domovoi import room_media

    async with session_scope() as s:
        np = await now_playing(s, room_id)
        rows = await timers(s, room_id)
        ring = await ringing(s, room_id)
    return {
        "type": "room_display",
        "sent_at_ms": mono_ms(),
        "now_playing": np,
        "timers": rows,
        "ringing": ring,
        "media": room_media.frame_media(room_id),
    }


# ─── change detection ────────────────────────────────────────────────────


def _np_shape(np: dict[str, Any] | None) -> Any:
    if np is None:
        return None
    return {k: v for k, v in np.items() if k != "elapsed_sec"}


def _np_anchor(frame: dict[str, Any]) -> float | None:
    """Where the song is, as a clock reading: its start (sent_at - elapsed)
    while playing, its position while paused."""
    np = frame.get("now_playing")
    if not np or np.get("elapsed_sec") is None:
        return None
    elapsed = float(np["elapsed_sec"])
    if np.get("state") == "play":
        return frame["sent_at_ms"] / 1000.0 - elapsed
    return elapsed


def _media_id(frame: dict[str, Any] | None) -> int | None:
    m = (frame or {}).get("media")
    return m.get("id") if isinstance(m, dict) else None


def differs(old: dict[str, Any] | None, new: dict[str, Any]) -> bool:
    """Whether ``new`` says something ``old`` didn't, time passing aside."""
    if old is None:
        return True
    if _media_id(old) != _media_id(new):
        return True
    return differs_besides_media(old, new)


def differs_besides_media(old: dict[str, Any], new: dict[str, Any]) -> bool:
    """:func:`differs`, the photo results left out."""
    if old.get("ringing") != new.get("ringing"):
        return True
    if _np_shape(old.get("now_playing")) != _np_shape(new.get("now_playing")):
        return True
    a, b = _np_anchor(old), _np_anchor(new)
    if (a is None) != (b is None) or (a is not None and abs(a - b) > DRIFT_SEC):  # type: ignore[operator]
        return True
    old_t, new_t = old.get("timers") or [], new.get("timers") or []
    if len(old_t) != len(new_t):
        return True
    for t0, t1 in zip(old_t, new_t):
        if {k: v for k, v in t0.items() if k != "remaining_ms"} != {
            k: v for k, v in t1.items() if k != "remaining_ms"
        }:
            return True
        end0 = old["sent_at_ms"] + t0["remaining_ms"]
        end1 = new["sent_at_ms"] + t1["remaining_ms"]
        if abs(end0 - end1) > DRIFT_SEC * 1000:
            return True
    return False


# ─── the feed: one per session ───────────────────────────────────────────


class _Bucket:
    def __init__(self, burst: int, refill_sec: float, clock: Callable[[], float]) -> None:
        self._burst, self._refill, self._clock = burst, refill_sec, clock
        self._tokens = float(burst)
        self._at = clock()

    def take(self) -> bool:
        now = self._clock()
        self._tokens = min(self._burst, self._tokens + (now - self._at) / self._refill)
        self._at = now
        if self._tokens < 1:
            return False
        self._tokens -= 1
        return True


Builder = Callable[[str], Awaitable[dict[str, Any]]]


class RoomDisplayFeed:
    """Sends ``room_display`` to one session and takes its ``room_control``.
    Started by ``StreamSession`` right after ``ready`` (only when
    :func:`wanted`), stopped when the socket closes."""

    def __init__(
        self,
        session: Any,
        *,
        builder: Builder | None = None,
        clock: Callable[[], float] = time.monotonic,
        poll_sec: float = POLL_SEC,
        min_interval_sec: float = MIN_INTERVAL_SEC,
    ) -> None:
        self.session = session
        self.room_id: str = session.room_id
        self._build = builder or build_frame
        self._clock = clock
        self.poll_sec = poll_sec
        self.min_interval_sec = min_interval_sec
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._subs: list[Any] = []
        self.last_sent: dict[str, Any] | None = None
        self._last_sent_at: float | None = None
        self._bucket = _Bucket(CONTROL_BURST, CONTROL_REFILL_SEC, clock)
        self._limited_said = False
        self._actions: set[asyncio.Task[None]] = set()
        #: Results already taken off this screen by a later frame.
        self._media_done: int | None = None

    # ── lifecycle ───────────────────────────────────────────────────────

    def start(self) -> None:
        if self._task is not None:
            return
        for name in _POKE_EVENTS:
            self._subs.append(EVENTS.subscribe(name, self._on_event))
        self._task = asyncio.create_task(self._run(), name=f"room-display-{self.room_id}")

    async def stop(self) -> None:
        for sub in self._subs:
            EVENTS.unsubscribe(sub)
        self._subs.clear()
        # A control already on its way (a pause) is left to finish: the
        # room asked for it. Only the sending stops.
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    def poke(self) -> None:
        """Something may have changed: look now (still at most one frame a
        second)."""
        self._wake.set()

    async def _on_event(self, _event: Event) -> None:
        self.poke()

    # ── sending ─────────────────────────────────────────────────────────

    async def _run(self) -> None:
        while True:
            self._wake.clear()
            try:
                frame = await self._build(self.room_id)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — try again next look
                log.debug("room display for %s: could not read the room: %s", self.room_id, e)
                frame = None
            if frame is not None:
                frame = self.settle_media(frame)
            if frame is not None and differs(self.last_sent, frame):
                wait = 0.0
                if self._last_sent_at is not None:
                    wait = self.min_interval_sec - (self._clock() - self._last_sent_at)
                if wait > 0:
                    # Too soon after the last one: look again once it may
                    # go, so what is sent is what is true then.
                    await asyncio.sleep(wait)
                    continue
                await self.session._safe_send_text(frame)
                self.last_sent = frame
                self._last_sent_at = self._clock()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_sec)
            except asyncio.TimeoutError:
                pass

    def settle_media(self, frame: dict[str, Any]) -> dict[str, Any]:
        """Photo results last until the next frame sent for another reason
        (domovoi/room_media.py): once one is due with the same results,
        they are dropped from it and from every later frame."""
        mid = _media_id(frame)
        if mid is None:
            return frame
        if mid == self._media_done or (
            self.last_sent is not None and _media_id(self.last_sent) == mid
            and differs_besides_media(self.last_sent, frame)
        ):
            self._media_done = mid
            return {**frame, "media": None}
        return frame

    # ── room_control ────────────────────────────────────────────────────

    def _offered(self) -> list[str]:
        np = (self.last_sent or {}).get("now_playing") or {}
        return list(np.get("controls") or [])

    @staticmethod
    def parse_control(ctrl: Any) -> tuple[str, int | None] | None:
        """``(action, fire_id)`` from a ``room_control`` frame, or None when
        it is not exactly one of the frames the module docstring lists."""
        if not isinstance(ctrl, dict) or ctrl.get("type") != "room_control":
            return None
        action = ctrl.get("action")
        if not isinstance(action, str):
            return None
        if action in MUSIC_TRANSCRIPTS:
            return (action, None) if set(ctrl) == {"type", "action"} else None
        if action == STOP_RINGING:
            fire_id = ctrl.get("fire_id")
            if set(ctrl) != {"type", "action", "fire_id"}:
                return None
            if isinstance(fire_id, bool) or not isinstance(fire_id, int):
                return None
            if not 0 < fire_id < 2**63:
                return None
            return action, fire_id
        return None

    async def on_control(self, ctrl: dict[str, Any]) -> None:
        """Take one ``room_control`` (module docstring). Never answers."""
        parsed = self.parse_control(ctrl)
        if parsed is None:
            log.debug("room display %s: ignoring room_control %.120r", self.room_id, ctrl)
            return
        if not self._bucket.take():
            if not self._limited_said:
                self._limited_said = True
                log.info("room display %s: room_control faster than allowed; dropping", self.room_id)
            return
        action, fire_id = parsed
        if action == STOP_RINGING:
            await self.stop_ringing(fire_id)  # type: ignore[arg-type]
        else:
            if action not in self._offered():
                log.debug("room display %s: %s is not offered right now; ignored", self.room_id, action)
                return
            # On its own task: a route through the handlers takes a moment,
            # and the socket's audio must keep flowing meanwhile.
            task = asyncio.create_task(self.music(action), name=f"room-control-{self.room_id}")
            self._actions.add(task)
            task.add_done_callback(self._actions.discard)

    async def music(self, action: str) -> None:
        """The dashboard's room control, from this room: the spoken
        equivalent routed for this room (the same audit trail), then the
        music frame to the room's satellite(s)."""
        from domovoi.config import settings
        from domovoi.db.session import session_scope
        from domovoi.models import Context, Intent
        from domovoi.room_music import dispatch_music
        from domovoi.router import route

        app = self.session.ws.app
        probe = getattr(app.state, "probe", None)
        intent = Intent(transcript=MUSIC_TRANSCRIPTS[action], room_id=self.room_id)
        ctx = Context(
            room_id=self.room_id,
            online=bool(getattr(probe, "online", False)),
            bot_name=settings.bot_name,
            app=app,
            answerable=False,
        )
        try:
            async with session_scope() as s:
                response = await route(intent, ctx, s)
            await dispatch_music(app, response, self.room_id)
        except Exception as e:  # noqa: BLE001 — the next frame shows the truth
            log.warning("room display %s: %s failed: %s", self.room_id, action, e)
        poke_all(app)

    async def stop_ringing(self, fire_id: int) -> None:
        """Acknowledge ``fire_id`` here (a timer or an alarm) and end its
        announcement in this room."""
        from domovoi.db.session import session_scope
        from domovoi.timer_delivery import ACK_WITHIN_SEC, ack_recent_fire

        try:
            async with session_scope() as s:
                acked = await ack_recent_fire(
                    s, self.room_id, within_sec=ACK_WITHIN_SEC,
                    timer_or_alarm=True, fire_id=fire_id,
                )
        except Exception as e:  # noqa: BLE001
            log.warning("room display %s: stopping fire %d failed: %s", self.room_id, fire_id, e)
            return
        if acked is None:
            log.debug("room display %s: fire %d is not ringing here", self.room_id, fire_id)
        else:
            log.info("room display %s: fire %d stopped from the screen", self.room_id, fire_id)
            delivery = getattr(self.session.ws.app.state, "timer_delivery", None)
            if delivery is not None:
                delivery.stop_announcing(fire_id, self.room_id)
        self.poke()


def poke_all(app: Any) -> None:
    """Tell every room display on ``app`` to look now."""
    sessions = getattr(getattr(app, "state", None), "active_sessions", None) or {}
    for sess in list(sessions.values()):
        feed = getattr(sess, "room_display", None)
        if isinstance(feed, RoomDisplayFeed):
            feed.poke()


async def stop_ringing_by_voice(
    session: Any, room_id: str, app: Any = None, *, heard_within_sec: float | None = None,
) -> int | None:
    """A bare "stop" said in ``room_id`` while a timer or alarm is ringing
    there: acknowledge it (and end its announcement) and return its id; None
    when nothing is ringing, so "stop" stops the music as before. Called
    from the music handler's "stop" fast path, and from the router for a
    "stop" said during a drop-in with ``heard_within_sec``: then only a fire
    actually announced in the room that recently counts, never one held
    for the call to end (ringing_fire's ``heard_only``)."""
    from domovoi.timer_delivery import ACK_WITHIN_SEC, ack_recent_fire, ringing_fire

    if heard_within_sec is not None:
        fire = await ringing_fire(session, room_id, heard_within_sec, heard_only=True)
    else:
        fire = await ringing_fire(session, room_id)
    if fire is None:
        return None
    acked = await ack_recent_fire(
        session, room_id, within_sec=ACK_WITHIN_SEC, timer_or_alarm=True, fire_id=fire.fire_id,
    )
    if acked is not None and app is not None:
        delivery = getattr(getattr(app, "state", None), "timer_delivery", None)
        if delivery is not None:
            delivery.stop_announcing(acked, room_id)
        poke_all(app)
    return acked
