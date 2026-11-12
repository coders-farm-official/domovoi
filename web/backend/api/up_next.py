"""Up next for browsers and phones — ``/api/music/up-next`` (V053, phase P3).

Up next keeps a player going with songs like the ones it has been playing
(the engine: :mod:`domovoi.up_next`). A browser or a phone asks for them
here; the web process runs the engine and keeps the session
(:mod:`domovoi.up_next.store`). Rooms come in phase P4.

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
from domovoi.up_next import store
from domovoi.up_next.data import MusicListens, SqlLibrary, playlist_label
from domovoi.up_next.engine import next_batch
from domovoi.up_next.model import DEFAULT_CONFIG, FROM_LIST, Track
from domovoi.up_next.pick import era_label
from domovoi.up_next.session import IDS_CAP, SeedRef, UpNextSession, device_listener
from web.backend.api import devices as devices_api
from web.backend.db import session_scope

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/music/up-next", tags=["music"])

_DEVICE_ID = r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,63}$"
NAME_MAX = 120
#: The artists of the last few picks, for spacing across a batch's seam.
SPACING_TAIL = 5

NOT_YOURS_DETAIL = "this up next belongs to another phone or browser"
UNKNOWN_SESSION_DETAIL = "no such up next session"


# ─── Shapes ───────────────────────────────────────────────────────────────


class TargetIn(BaseModel):
    kind: Literal["device"]
    device_id: str = Field(..., pattern=_DEVICE_ID)


class ContextIn(BaseModel):
    kind: Literal["playlist", "album", "artist", "genre", "ids"]
    ref: str | None = Field(default=None, max_length=600)
    track_ids: list[int] | None = Field(default=None, max_length=IDS_CAP)
    name: str | None = Field(default=None, max_length=NAME_MAX)

    @model_validator(mode="after")
    def _shape(self) -> "ContextIn":
        if self.kind == "ids":
            if not self.track_ids:
                raise ValueError("an ids list needs track_ids")
        elif not (self.ref or "").strip():
            raise ValueError(f"a {self.kind} list needs its ref")
        if self.kind == "album" and "|" not in (self.ref or ""):
            raise ValueError("an album's ref is '<artist>|<album>'")
        return self


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


# ─── Helpers ──────────────────────────────────────────────────────────────


def _check_caller(request: Request, device_id: str) -> None:
    caller = devices_api.caller_device(request)
    if caller and caller != device_id:
        raise HTTPException(status_code=403, detail=NOT_YOURS_DETAIL)


async def _person(s: Any, person_id: int | None) -> dict[str, Any] | None:
    if person_id is None:
        return None
    r = (await s.execute(text("SELECT id, name FROM people WHERE id = :p"), {"p": person_id})).first()
    return {"id": int(r[0]), "name": r[1]} if r is not None else None


async def _require_person(s: Any, person_id: int) -> None:
    if await _person(s, person_id) is None:
        raise HTTPException(status_code=422, detail=f"person {person_id} not found")


async def _listener(s: Any, device_id: str, body: SessionIn) -> int | None:
    if body.listener_person_id is not None:
        await _require_person(s, body.listener_person_id)
        return body.listener_person_id
    if body.as_household:
        return None
    row = (await s.execute(
        text("SELECT owner_person_id, shared_screen FROM devices WHERE device_id = :d"),
        {"d": device_id})).first()
    owner, shared = (row[0], bool(row[1])) if row is not None else (None, False)
    return device_listener(listener_person_id=None, owner_person_id=owner, shared_screen=shared)


async def _seed(s: Any, start: StartIn) -> tuple[SeedRef, str | None]:
    """The SeedRef for a start, and the list's name as people know it."""
    c = start.context
    if c is None:
        return SeedRef(track_id=start.track_id), None
    name = (c.name or "").strip() or None
    if c.kind == "ids":
        return SeedRef(track_id=start.track_id, kind="ids", track_ids=tuple(c.track_ids or ()),
                       label=playlist_label(name or "this list")), name or "this list"
    ref = (c.ref or "").strip()
    if c.kind == "playlist":
        try:
            pid = int(ref)
        except ValueError:
            raise HTTPException(status_code=422, detail="a playlist's ref is its id") from None
        row = (await s.execute(text("SELECT name FROM playlists WHERE id = :p"), {"p": pid})).first()
        if row is None:
            raise HTTPException(status_code=404, detail=f"playlist {pid} not found")
        name = row[0]
        ref = str(pid)
    elif c.kind == "artist":
        name = name or ref
        ref = (await s.execute(text("SELECT library_search_norm(:n)"), {"n": ref})).scalar() or ""
    elif c.kind == "album":
        artist, _, album = ref.partition("|")
        norm = (await s.execute(text("SELECT library_search_norm(:n)"), {"n": artist})).scalar() or ""
        name = name or album or "this album"
        ref = f"{norm}|{album}"
    else:  # genre: a genre key
        name = name or ref
    if not ref:
        raise HTTPException(status_code=422, detail=f"a {c.kind} list needs its ref")
    return SeedRef(track_id=start.track_id, kind=c.kind, ref=ref, label=playlist_label(name)), name


async def _track_row(s: Any, track_id: int | None) -> dict[str, Any] | None:
    if track_id is None:
        return None
    r = (await s.execute(text("SELECT id, title, artist FROM library_tracks WHERE id = :t"),
                         {"t": track_id})).mappings().first()
    return dict(r) if r is not None else None


def _pick_json(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "seq": p["seq"], "track_id": p["track_id"], "title": p["title"] or "", "artist": p["artist"] or "",
        "album": p["album"] or "", "duration_sec": p["duration_sec"], "reason_code": p["reason_code"],
        "reason_label": p["reason_label"], "status": p["status"], "mpd_song_id": p["mpd_song_id"],
        "batch_no": p["batch_no"],
    }


async def _session_json(s: Any, st: store.Stored, *, picks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    sess = st.sess
    kind, ident = sess.target_kind, sess.target_id
    seed_track = await _track_row(s, sess.seed.track_id)
    if picks is None:
        picks = await store.picks(s, sess.id, statuses=("queued",))
    return {
        "id": sess.id,
        "target": {"kind": kind, ("device_id" if kind == "device" else "room_id"): ident},
        "mode": sess.mode,
        "autoplay": sess.autoplay,
        "person": await _person(s, sess.person_id),
        "seed": {
            "track_id": sess.seed.track_id,
            "title": (seed_track or {}).get("title"),
            "artist": (seed_track or {}).get("artist"),
            "label": sess.seed.label,
            "list": ({"kind": sess.seed.kind, "name": st.list_name} if sess.seed.kind else None),
        },
        "picks": [_pick_json(p) for p in picks],
        "batch_no": sess.batch_no,
        "have_through_seq": await store.max_seq(s, sess.id),
        "ended": sess.ended or st.ended_at is not None,
    }


async def _load(s: Any, request: Request, session_id: str, *, lock: bool) -> store.Stored:
    st = await (store.lock if lock else store.get)(s, session_id)
    if st is None:
        raise HTTPException(status_code=404, detail=UNKNOWN_SESSION_DETAIL)
    if st.sess.target_kind == "device":
        _check_caller(request, st.sess.target_id)
    return st


def _list_reason(seed: Track | None, t: Track, fallback: str) -> str:
    """A list pick's label, said the way a person would: what it shares with
    the song the session started from (the list is weighted toward those),
    else the list itself."""
    if seed is not None:
        if seed.artist_norm and t.artist_norm == seed.artist_norm:
            return "same artist"
        if seed.year is not None and t.year is not None and abs(seed.year - t.year) <= DEFAULT_CONFIG.era_years:
            return era_label(seed.year, t.year)
        shared = [g for g in t.genre_keys if g in set(seed.genre_keys)]
        if shared:
            return shared[0]
    return fallback


async def _draw(s: Any, st: store.Stored) -> tuple[int, list[dict[str, Any]], bool]:
    """Draw the session's next batch and keep it: ``(batch_no, picks, ended)``."""
    sess = st.sess
    library = SqlLibrary(s)
    tail = await store.picks(s, sess.id, newest=SPACING_TAIL)
    prev_keys = [p["artist_norm"] or f"#{p['track_id']}" for p in tail]
    batch = await next_batch(sess, library=library, listens=MusicListens(s),
                             now=datetime.now(timezone.utc), prev_keys=prev_keys)
    labels: dict[int, str] = {}
    if sess.mode == "list" and batch.picks:
        ids = [p.track_id for p in batch.picks]
        if sess.seed.track_id is not None:
            ids.append(sess.seed.track_id)
        known = await library.tracks(ids)
        seed_t = known.get(sess.seed.track_id) if sess.seed.track_id is not None else None
        fallback = playlist_label(st.list_name) if st.list_name else "from this list"
        for i, p in enumerate(batch.picks):
            if p.reason_code == FROM_LIST and p.track_id in known:
                labels[i] = _list_reason(seed_t, known[p.track_id], fallback)
    first = None
    if batch.picks:
        first = await store.add_picks(s, sess.id, batch.batch_no, batch.picks, labels)
    await store.save(s, st)
    if first is None:
        return batch.batch_no, [], batch.ended
    return batch.batch_no, await store.picks(s, sess.id, after_seq=first - 1, batch_no=batch.batch_no), batch.ended


async def _take_back(s: Any, st: store.Stored, track: Track) -> list[int]:
    """The waiting picks by ``track``'s artist (or that very track, when it
    has none), taken out of the session."""
    seqs = await store.queued_by(s, st.sess.id, artist_norm=track.artist_norm or None,
                                 track_id=None if track.artist_norm else track.id)
    return await store.set_status(s, st.sess.id, seqs, "removed")


# ─── Routes ───────────────────────────────────────────────────────────────


@router.post("/sessions", dependencies=[Depends(require_device)])
async def start_session(body: SessionIn, request: Request) -> dict[str, Any]:
    """Start Up next on this device (it replaces the device's previous
    session). The answer is the session with its first batch: ``list`` and
    ``library`` modes draw it now, ``continue`` mode when the list is
    nearly over (``POST …/next``)."""
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
    (the player drops them from its queue)."""
    async with session_scope() as s:
        st = await _load(s, request, session_id, lock=True)
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
    """Turn Autoplay on or off, or change whose taste the mix follows."""
    async with session_scope() as s:
        st = await _load(s, request, session_id, lock=True)
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
