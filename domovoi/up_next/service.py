"""Up next, the parts both processes share around a stored session.

Phase P3 kept these inside the web's ``/api/music/up-next`` router; phase
P4 (rooms) needs the same things in the core, which starts and refills a
room's session (:mod:`.rooms`). So they live here, on a plain database
session, with no FastAPI:

* :class:`ContextIn` — the list a song was clicked in, as a request sends
  it (the same shape for a phone, a browser and a room);
* :func:`build_seed` — that list as the engine's :class:`~.session.SeedRef`
  plus the name people know it by;
* :func:`draw` — draw a session's next batch and keep it;
* :func:`take_back` — a "less like this" swapping an artist's waiting picks
  out;
* :func:`session_json` / :func:`pick_json` — the answer shapes
  (``Session`` and ``Pick`` in docs/API_REFERENCE.md §3.5f).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text

from . import store
from .data import MusicListens, SqlLibrary, playlist_label
from .engine import next_batch
from .model import DEFAULT_CONFIG, FROM_LIST, Track
from .pick import era_label
from .session import IDS_CAP, SeedRef

NAME_MAX = 120
#: The artists of the last few picks, for spacing across a batch's seam.
SPACING_TAIL = 5


class SeedError(Exception):
    """A start named a list or a song the house doesn't have (``status``:
    404) or named it the wrong way (422)."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


class ContextIn(BaseModel):
    """The list a song was clicked in: ``playlist`` (ref = its id),
    ``album`` (``"<artist>|<album>"``), ``artist`` (a name), ``genre`` (a
    genre key) or ``ids`` (up to 500 track ids: search results, the
    library page). ``name`` is what the queue's head calls the list."""

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


async def build_seed(s: Any, track_id: int | None, c: ContextIn | None) -> tuple[SeedRef, str | None]:
    """The SeedRef for a start, and the list's name as people know it.
    Raises :class:`SeedError` for a playlist the house doesn't have."""
    if c is None:
        return SeedRef(track_id=track_id), None
    name = (c.name or "").strip() or None
    if c.kind == "ids":
        return SeedRef(track_id=track_id, kind="ids", track_ids=tuple(c.track_ids or ()),
                       label=playlist_label(name or "this list")), name or "this list"
    ref = (c.ref or "").strip()
    if c.kind == "playlist":
        try:
            pid = int(ref)
        except ValueError:
            raise SeedError(422, "a playlist's ref is its id") from None
        row = (await s.execute(text("SELECT name FROM playlists WHERE id = :p"), {"p": pid})).first()
        if row is None:
            raise SeedError(404, f"playlist {pid} not found")
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
        raise SeedError(422, f"a {c.kind} list needs its ref")
    return SeedRef(track_id=track_id, kind=c.kind, ref=ref, label=playlist_label(name)), name


async def person_json(s: Any, person_id: int | None) -> dict[str, Any] | None:
    if person_id is None:
        return None
    r = (await s.execute(text("SELECT id, name FROM people WHERE id = :p"), {"p": person_id})).first()
    return {"id": int(r[0]), "name": r[1]} if r is not None else None


async def track_row(s: Any, track_id: int | None) -> dict[str, Any] | None:
    if track_id is None:
        return None
    r = (await s.execute(text("SELECT id, title, artist FROM library_tracks WHERE id = :t"),
                         {"t": track_id})).mappings().first()
    return dict(r) if r is not None else None


def pick_json(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "seq": p["seq"], "track_id": p["track_id"], "title": p["title"] or "", "artist": p["artist"] or "",
        "album": p["album"] or "", "duration_sec": p["duration_sec"], "reason_code": p["reason_code"],
        "reason_label": p["reason_label"], "status": p["status"], "mpd_song_id": p["mpd_song_id"],
        "batch_no": p["batch_no"],
    }


async def session_json(s: Any, st: store.Stored, *, picks: list[dict[str, Any]] | None = None,
                       with_person: bool = True) -> dict[str, Any]:
    """The ``Session`` answer. ``with_person=False`` leaves ``person`` out
    altogether: a guest reading a room's session never learns whose taste
    it follows (plan §4)."""
    sess = st.sess
    kind, ident = sess.target_kind, sess.target_id
    seed_track = await track_row(s, sess.seed.track_id)
    if picks is None:
        picks = await store.picks(s, sess.id, statuses=("queued",))
    out: dict[str, Any] = {
        "id": sess.id,
        "target": {"kind": kind, ("device_id" if kind == "device" else "room_id"): ident},
        "mode": sess.mode,
        "autoplay": sess.autoplay,
    }
    if with_person:
        out["person"] = await person_json(s, sess.person_id)
    out.update({
        "seed": {
            "track_id": sess.seed.track_id,
            "title": (seed_track or {}).get("title"),
            "artist": (seed_track or {}).get("artist"),
            "label": sess.seed.label,
            "list": ({"kind": sess.seed.kind, "name": st.list_name} if sess.seed.kind else None),
        },
        "picks": [pick_json(p) for p in picks],
        "batch_no": sess.batch_no,
        "have_through_seq": await store.max_seq(s, sess.id),
        "ended": sess.ended or st.ended_at is not None,
    })
    return out


def list_reason(seed: Track | None, t: Track, fallback: str) -> str:
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


async def draw(s: Any, st: store.Stored) -> tuple[int, list[dict[str, Any]], bool]:
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
                labels[i] = list_reason(seed_t, known[p.track_id], fallback)
    first = None
    if batch.picks:
        first = await store.add_picks(s, sess.id, batch.batch_no, batch.picks, labels)
    await store.save(s, st)
    if first is None:
        return batch.batch_no, [], batch.ended
    return batch.batch_no, await store.picks(s, sess.id, after_seq=first - 1, batch_no=batch.batch_no), batch.ended


async def take_back(s: Any, st: store.Stored, track: Track) -> list[int]:
    """The waiting picks by ``track``'s artist (or that very track, when it
    has none), taken out of the session."""
    seqs = await store.queued_by(s, st.sess.id, artist_norm=track.artist_norm or None,
                                 track_id=None if track.artist_norm else track.id)
    return await store.set_status(s, st.sess.id, seqs, "removed")
