"""Up next for browsers and phones — ``/api/music/up-next`` (V053, phase P3).

Up next keeps a player going with songs like the ones it has been playing
(the engine: :mod:`domovoi.up_next`). A browser or a phone asks for them
here; the web process runs the engine and keeps the session
(:mod:`domovoi.up_next.store`). A ROOM's session (phase P4) is the core's:
it starts with the room's play paths and the core refills the room's queue
as it plays (:mod:`domovoi.up_next.rooms`, the ``up_next_extender``
worker); here a room target starts one through the core's play-track,
``move`` carries a session between a phone or browser and a room (a cast,
a hand-back), and ``GET /room/{room_id}`` reads a room's on the queue's
tier. ``next`` on a room's session is 409; PATCH and feedback work on it
like a phone's, for any household caller the room's queue block allows.

* ``POST /sessions`` — start one for this device (``target.kind`` is
  ``device``). ``start``:

  - ``{track_id, context}``: a song clicked in a list (``list`` mode: the
    rest of that list, weighted toward songs like the ones playing, then
    round again while autoplay is on);
  - ``{track_id}``: a song on its own (``library`` mode: similar songs from
    the whole library);
  - ``{context, whole_list: true}``: a whole list played or shuffled on
    purpose (``continue`` mode: nothing until the list is nearly over, then
    similar songs). ``track_id`` is then the list's first song, if known.

  ``context`` is ``{kind: playlist|album|artist|genre|ids, ref?, track_ids?,
  name?}``: a playlist's id, ``"<artist>|<album>"``, an artist's name, a
  genre key, or up to 500 track ids (search results, the library page).
  ``name`` is what the queue's header calls the list (a playlist's own name
  is read from the database). A new start ends this device's previous
  session. The answer is the session with its first batch (none in
  ``continue`` mode). DEVICE tier.
* ``GET /sessions/{id}`` — the session and its picks still waiting. The
  READ half of the device tier (it names whose mix it is).
* ``POST /sessions/{id}/next`` ``{have_through_seq}`` — the next batch.
  **Idempotent:** picks after ``have_through_seq`` that were already handed
  out come back as they were, so a retried request never skips a batch.
  ``removed`` lists the picks the session took back since ("less like
  this", two early skips of one artist), for the player to drop. DEVICE.
* ``POST /sessions/{id}/feedback`` ``{seq | track_id, kind, played_sec?,
  duration_sec?}`` — ``remove`` (one pick out), ``less_like_this`` (that
  artist is out for the rest of the session; their waiting picks come back
  in ``removed``), ``skip`` (an early skip demotes the artist and genre;
  a second early skip of one artist excludes it). DEVICE.
* ``PATCH /sessions/{id}`` ``{autoplay?, listener_person_id?}`` — the
  Autoplay switch, and whose taste the mix follows (``null``: the
  household). DEVICE.

Whose mix it is (plan §2.3): the ``listener_person_id`` the player sends
(its "listening as"), else, unless it is a shared screen, the person the
device is claimed by, else the household. ``as_household`` says "nobody in
particular" on purpose. Self-asserted within the household, like the
listening history and the spoken-audio resume positions.

A device acts on its own sessions only: when a caller names itself
(``X-Domovoi-Device-Id``, ``X-Device-Id``, the registration cookie) and the
session is another device's, 403.

The songs a session plays feed it through the listening history: a
browser's or phone's listens (``POST /api/music/listens``) that carry the
session's id move its drift seeds and apply its early-skip rule
(:func:`feed_listens`), also when the household keeps no history.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Iterable, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text

from domovoi import music_listens
from domovoi.admin_auth import require_device, require_device_read
from domovoi.up_next import service, store
from domovoi.up_next.data import SqlLibrary
from domovoi.up_next.service import ContextIn  # noqa: F401 — the request shape, re-exported
from domovoi.up_next.session import SeedRef, UpNextSession, device_listener
from web.backend.api import devices as devices_api
from web.backend.db import session_scope
from web.backend.domovoi_client import auth_forward_headers, bridge_response, get_admin, post_admin

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/music/up-next", tags=["music"])

_DEVICE_ID = r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,63}$"
_ROOM_ID = r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,63}$"
NAME_MAX = service.NAME_MAX
SPACING_TAIL = service.SPACING_TAIL

NOT_YOURS_DETAIL = "this up next belongs to another phone or browser"
UNKNOWN_SESSION_DETAIL = "no such up next session"
ROOM_LIST_DETAIL = (
    "a room plays a whole list as it is: cast it with /api/music/play-tracks "
    "(or play-playlist), and the room's up next follows it"
)
ROOM_REFILL_DETAIL = "a room's up next is refilled by the home server as it plays"


# ─── Shapes ───────────────────────────────────────────────────────────────


class TargetIn(BaseModel):
    """``{kind:'device', device_id}`` (a browser or a phone) or
    ``{kind:'room', room_id}`` (a room's music player, phase P4)."""

    kind: Literal["device", "room"]
    device_id: str | None = Field(default=None, pattern=_DEVICE_ID)
    room_id: str | None = Field(default=None, pattern=_ROOM_ID)

    @model_validator(mode="after")
    def _shape(self) -> "TargetIn":
        if self.kind == "device" and not self.device_id:
            raise ValueError("a device target needs its device_id")
        if self.kind == "room" and not self.room_id:
            raise ValueError("a room target needs its room_id")
        return self

    @property
    def target(self) -> str:
        return f"device:{self.device_id}" if self.kind == "device" else f"room:{self.room_id}"


class StartIn(BaseModel):
    track_id: int | None = Field(default=None, ge=1)
    context: ContextIn | None = None
    whole_list: bool = False

    @model_validator(mode="after")
    def _shape(self) -> "StartIn":
        if self.whole_list and self.context is None:
            raise ValueError("whole_list needs the list (context)")
        if not self.whole_list and self.track_id is None:
            raise ValueError("start needs the song (track_id)")
        return self


class SessionIn(BaseModel):
    target: TargetIn
    start: StartIn
    listener_person_id: int | None = Field(default=None, ge=1)
    as_household: bool = False
    autoplay: bool = True


class NextIn(BaseModel):
    have_through_seq: int = Field(..., ge=0)


class FeedbackIn(BaseModel):
    seq: int | None = Field(default=None, ge=1)
    track_id: int | None = Field(default=None, ge=1)
    kind: Literal["skip", "less_like_this", "remove"]
    played_sec: float | None = Field(default=None, ge=0, le=music_listens.MAX_SECONDS)
    duration_sec: float | None = Field(default=None, ge=0, le=music_listens.MAX_SECONDS)

    @model_validator(mode="after")
    def _shape(self) -> "FeedbackIn":
        if self.seq is None and self.track_id is None:
            raise ValueError("name the pick (seq) or the song (track_id)")
        return self


class PatchIn(BaseModel):
    autoplay: bool | None = None
    # Present and null: the household. Absent: unchanged.
    listener_person_id: int | None = Field(default=None, ge=1)


class MoveIn(BaseModel):
    target: TargetIn


# ─── Helpers ──────────────────────────────────────────────────────────────

# Kept under their P3 names (tests and callers use them).
_person = service.person_json
_track_row = service.track_row
_pick_json = service.pick_json
_session_json = service.session_json
_list_reason = service.list_reason
_draw = service.draw
_take_back = service.take_back


def _check_caller(request: Request, device_id: str) -> None:
    caller = devices_api.caller_device(request)
    if caller and caller != device_id:
        raise HTTPException(status_code=403, detail=NOT_YOURS_DETAIL)


async def _require_person(s: Any, person_id: int) -> None:
    if await _person(s, person_id) is None:
        raise HTTPException(status_code=422, detail=f"person {person_id} not found")


async def _listener(s: Any, device_id: str | None, body: SessionIn) -> int | None:
    if body.listener_person_id is not None:
        await _require_person(s, body.listener_person_id)
        return body.listener_person_id
    if body.as_household or not device_id:
        return None
    row = (await s.execute(
        text("SELECT owner_person_id, shared_screen FROM devices WHERE device_id = :d"),
        {"d": device_id})).first()
    owner, shared = (row[0], bool(row[1])) if row is not None else (None, False)
    return device_listener(listener_person_id=None, owner_person_id=owner, shared_screen=shared)


async def room_listener_for(
    request: Request, *, device_id: str | None, listener_person_id: int | None, as_household: bool,
) -> int | None:
    """Whose mix a room's session follows when a phone or browser starts it
    (plan §2.3): the "listening as" it sent, else its claimed person unless
    it is a shared screen, else the household. Self-asserted within the
    household, like every listener choice. A person nobody has is a 422."""
    from web.backend.api import files as files_api

    caller = files_api.caller_device_id(request, device_id) or devices_api.caller_device(request)
    body = SessionIn(target=TargetIn(kind="room", room_id="x"), start=StartIn(track_id=1),
                     listener_person_id=listener_person_id, as_household=as_household)
    async with session_scope() as s:
        return await _listener(s, caller, body)


async def _seed(s: Any, start: "StartIn") -> tuple[SeedRef, str | None]:
    """The SeedRef for a start, and the list's name as people know it."""
    try:
        return await service.build_seed(s, start.track_id, start.context)
    except service.SeedError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from None


async def _load(s: Any, request: Request, session_id: str, *, lock: bool) -> store.Stored:
    st = await (store.lock if lock else store.get)(s, session_id)
    if st is None:
        raise HTTPException(status_code=404, detail=UNKNOWN_SESSION_DETAIL)
    if st.sess.target_kind == "device":
        _check_caller(request, st.sess.target_id)
    return st


async def _assert_room_editable(request: Request, room_id: str) -> None:
    """A room's session is the household's: any household phone or browser
    may change it, unless an admin took that room's queue away from the one
    asking (the queue block, WEB-11)."""
    from web.backend.api.music import _assert_may_replace_queue

    await _assert_may_replace_queue(request, None, room_id)


# ─── Routes ───────────────────────────────────────────────────────────────


@router.post("/sessions", dependencies=[Depends(require_device)])
async def start_session(body: SessionIn, request: Request) -> dict[str, Any]:
    """Start Up next on this device (it replaces the device's previous
    session). The answer is the session with its first batch: ``list`` and
    ``library`` modes draw it now, ``continue`` mode when the list is
    nearly over (``POST …/next``).

    A ROOM target (phase P4) plays the song in that room and starts the
    room's session there (the core's play-track: it replaces the room's
    queue, so the queue block applies); the room's player then holds the
    song and the first batch, and the core refills it as it plays. A whole
    list is cast with play-tracks instead (422 here)."""
    if body.target.kind == "room":
        return await _start_in_room(body, request)
    device_id = body.target.device_id
    _check_caller(request, device_id)
    async with session_scope() as s:
        if body.start.track_id is not None and await _track_row(s, body.start.track_id) is None:
            raise HTTPException(status_code=404, detail=f"track {body.start.track_id} not found")
        seed, name = await _seed(s, body.start)
        person = await _listener(s, device_id, body)
        mode = "continue" if body.start.whole_list else ("list" if seed.kind else "library")
        sess = UpNextSession.start(target=f"device:{device_id}", mode=mode, seed=seed,
                                   person_id=person, autoplay=body.autoplay)
        st = await store.create(s, sess, name=name)
        picks: list[dict[str, Any]] = []
        if mode != "continue":
            _, picks, _ = await _draw(s, st)
        out = await _session_json(s, st, picks=picks)
    log.info("up next: %s session %s started on %s", mode, sess.id, device_id)
    return out


async def _start_in_room(body: SessionIn, request: Request) -> Any:
    room_id = body.target.room_id or ""
    if body.start.whole_list:
        raise HTTPException(status_code=422, detail=ROOM_LIST_DETAIL)
    await _assert_room_editable(request, room_id)
    person = await room_listener_for(request, device_id=None,
                                     listener_person_id=body.listener_person_id,
                                     as_household=body.as_household)
    out: dict[str, Any] = {"room_id": room_id, "track_id": body.start.track_id,
                           "person_id": person, "autoplay": body.autoplay}
    if body.start.context is not None:
        out["context"] = body.start.context.model_dump(exclude_none=True)
    status, payload = await post_admin("/v1/admin/music/play-track", out,
                                       headers=auth_forward_headers(request))
    if status != 200 or not isinstance(payload, dict):
        return bridge_response(status, payload)
    from web.backend.api.music_listens import note_room_start

    await note_room_start(request, None, room_id)
    sid = ((payload.get("up_next") or {}).get("session_id"))
    async with session_scope() as s:
        st = await store.get(s, sid) if sid else None
        if st is None:
            # The song plays; its Up next couldn't start (the core said why
            # in its log). Nothing that pretends otherwise.
            raise HTTPException(status_code=503, detail="the song plays, but up next couldn't start there")
        return await _session_json(s, st)


@router.get("/room/{room_id}")
async def room_session(room_id: str, request: Request) -> Any:
    """The room's active session, or ``null``. Read like the room's queue
    (``GET /api/music/queue/{room}``): open to the household, and to a
    guest while the room plays; the core decides, and leaves ``person``
    out for a guest."""
    status, payload = await get_admin(f"/v1/admin/music/up-next/room/{room_id}",
                                      headers=auth_forward_headers(request))
    if status == 0:
        raise HTTPException(status_code=502, detail="domovoi core unreachable; can't read the room's up next")
    if status != 200:
        return bridge_response(status, payload)
    return (payload or {}).get("session") if isinstance(payload, dict) else None


@router.get("/sessions/{session_id}", dependencies=[Depends(require_device_read)])
async def get_session(session_id: str, request: Request) -> dict[str, Any]:
    """The session and the picks it still has waiting."""
    async with session_scope() as s:
        st = await _load(s, request, session_id, lock=False)
        return await _session_json(s, st)


@router.post("/sessions/{session_id}/next", dependencies=[Depends(require_device)])
async def next_picks(session_id: str, body: NextIn, request: Request) -> dict[str, Any]:
    """``{batch_no, picks, ended, removed}``. Asked again with the same
    ``have_through_seq``, the same batch comes back."""
    async with session_scope() as s:
        st = await _load(s, request, session_id, lock=True)
        if st.sess.target_kind == "room":
            raise HTTPException(status_code=409, detail=ROOM_REFILL_DETAIL)
        removed = await store.removed_seqs(s, st.sess.id)
        ahead = await store.picks(s, st.sess.id, after_seq=body.have_through_seq,
                                  statuses=("queued", "played", "skipped"))
        if ahead:
            first_batch = ahead[0]["batch_no"]
            same = [p for p in ahead if p["batch_no"] == first_batch]
            return {"batch_no": first_batch, "picks": [_pick_json(p) for p in same],
                    "ended": False, "removed": removed}
        if st.ended_at is not None:
            return {"batch_no": st.sess.batch_no, "picks": [], "ended": True, "removed": removed}
        batch_no, picks, ended = await _draw(s, st)
        return {"batch_no": batch_no, "picks": [_pick_json(p) for p in picks], "ended": ended,
                "removed": removed}


@router.post("/sessions/{session_id}/feedback", dependencies=[Depends(require_device)])
async def feedback(session_id: str, body: FeedbackIn, request: Request) -> dict[str, Any]:
    """``{removed}``: the seqs of the picks this took out of the session
    (the player drops them from its queue). For a ROOM's session the core's
    extender takes them out of the room's queue on its next look (within
    a few seconds), never the song playing now."""
    async with session_scope() as s:
        st = await _load(s, request, session_id, lock=True)
        if st.sess.target_kind == "room":
            await _assert_room_editable(request, st.sess.target_id)
        sid = st.sess.id
        seq, track_id = body.seq, body.track_id
        if seq is not None:
            row = (await s.execute(
                text("SELECT track_id FROM up_next_picks WHERE session_id = :s AND seq = :q"),
                {"s": sid, "q": seq})).first()
            if row is None:
                raise HTTPException(status_code=404, detail=f"no pick {seq} in this session")
            track_id = int(row[0])
        elif body.kind == "remove":
            waiting = await store.queued_by(s, sid, track_id=track_id)
            seq = waiting[0] if waiting else None
        library = SqlLibrary(s)
        track = (await library.tracks([track_id])).get(track_id)
        if track is None:
            raise HTTPException(status_code=404, detail=f"track {track_id} not found")
        removed: list[int] = []
        if body.kind == "remove":
            if seq is not None:
                removed = await store.set_status(s, sid, [seq], "removed")
        elif body.kind == "less_like_this":
            st.sess.less_like_this(track)
            if seq is not None:
                await store.set_status(s, sid, [seq], "removed")
            removed = sorted(set(await _take_back(s, st, track)) | ({seq} if seq is not None else set()))
        else:  # skip
            excluded = st.sess.record_listen(track, played_sec=body.played_sec or 0.0,
                                             duration_sec=body.duration_sec, outcome="skipped")
            if seq is not None:
                await store.set_status(s, sid, [seq], "skipped")
            if excluded:
                removed = await _take_back(s, st, track)
        await store.save(s, st)
    return {"removed": removed}


@router.patch("/sessions/{session_id}", dependencies=[Depends(require_device)])
async def patch_session(session_id: str, body: PatchIn, request: Request) -> dict[str, Any]:
    """Turn Autoplay on or off, or change whose taste the mix follows. For
    a room's session, Autoplay off takes the waiting picks out of the room's
    queue (the core's extender, on its next look); a list keeps the rest of
    itself, as on a phone."""
    async with session_scope() as s:
        st = await _load(s, request, session_id, lock=True)
        if st.sess.target_kind == "room":
            await _assert_room_editable(request, st.sess.target_id)
        sess = st.sess
        if body.autoplay is not None:
            sess.autoplay = body.autoplay
            if body.autoplay and sess.mode == "list" and sess.ended:
                sess.ended = False      # a list that stopped at its end goes round again
        if "listener_person_id" in body.model_fields_set:
            if body.listener_person_id is not None:
                await _require_person(s, body.listener_person_id)
            sess.set_listener(body.listener_person_id)
        await store.save(s, st)
        return await _session_json(s, st)


@router.post("/sessions/{session_id}/move", dependencies=[Depends(require_device)])
async def move_session(session_id: str, body: MoveIn, request: Request) -> dict[str, Any]:
    """Casting: the session carries on on another player (plan §2.1).

    * to a ROOM — after the phone or browser cast its queue there
      (``play-tracks``, which can also move it in the same call:
      ``up_next_session_id``). The room's own session, if any, ends; the
      core's extender finds the picks in the room's queue by their files
      and refills it as it plays. The room's queue block applies.
    * to a DEVICE — when playback comes back to this phone or browser: only
      to the device that asks (403 otherwise).

    A device's session moves only at that device's request."""
    new = body.target
    if new.kind == "device":
        _check_caller(request, new.device_id or "")
    else:
        await _assert_room_editable(request, new.room_id or "")
    async with session_scope() as s:
        st = await _load(s, request, session_id, lock=True)
        if st.ended_at is not None:
            raise HTTPException(status_code=409, detail="this up next has ended")
        if not await store.move(s, st, new.target):
            raise HTTPException(status_code=409, detail="the session changed meanwhile; try again")
        if new.kind == "room":
            # The room holds what was cast; which picks it holds, the
            # extender finds out by their files on its next look.
            await store.set_song_ids(s, st.sess.id, {p["seq"]: None for p in await store.picks(s, st.sess.id)})
        out = await _session_json(s, st)
    log.info("up next: session %s moved to %s", session_id, new.target)
    return out


# ─── The listening history feeds the session ──────────────────────────────


async def feed_listens(rows: Iterable[dict[str, Any]]) -> int:
    """Listens a browser or phone sent (``POST /api/music/listens``) that
    name an Up next session of THAT device: each moves the session's drift
    seeds (a song listened to) or applies its early-skip rule, and marks
    the pick played or skipped. Best-effort, never fails the post; returns
    how many rows it used. ``rows``: ``track_id, device_id, session_id,
    played_sec, duration_sec, outcome, started_at``."""
    by_session: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if r.get("session_id") and r.get("device_id"):
            by_session.setdefault(str(r["session_id"]), []).append(r)
    used = 0
    for sid, items in by_session.items():
        try:
            async with session_scope() as s:
                st = await store.lock(s, sid)
                if st is None or st.ended_at is not None or st.sess.target_kind != "device":
                    continue
                mine = [r for r in items if r["device_id"] == st.sess.target_id]
                if not mine:
                    continue
                tracks = await SqlLibrary(s).tracks(r["track_id"] for r in mine)
                for r in sorted(mine, key=lambda x: x.get("started_at") or datetime.min.replace(tzinfo=timezone.utc)):
                    t = tracks.get(int(r["track_id"]))
                    if t is None:
                        continue
                    outcome = r["outcome"]
                    excluded = st.sess.record_listen(
                        t, played_sec=float(r.get("played_sec") or 0),
                        duration_sec=r.get("duration_sec"), outcome=outcome)
                    waiting = await store.queued_by(s, st.sess.id, track_id=t.id)
                    if waiting:
                        await store.set_status(s, st.sess.id, waiting[:1],
                                               "skipped" if outcome == "skipped" else "played")
                    if excluded:
                        await _take_back(s, st, t)
                    used += 1
                await store.save(s, st)
        except Exception as e:  # noqa: BLE001 — a mix is a nicety; the listen still counts
            log.debug("up next: listens not fed to %s: %s", sid, e)
    return used
