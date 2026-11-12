"""Up next in a room (UP-NEXT-QUEUE-PLAN §5, phase P4).

A room's session is kept like a phone's (:mod:`.store`, target
``room:<room_id>``), but its queue is the room's music player (MPD), so the
core keeps it going:

* **Start** — every way a room begins playing starts (or ends) its session,
  AFTER the player's queue was replaced: :func:`start` (a song: ``library``
  mode, or ``list`` when it was clicked in a list; a whole list played on
  purpose: ``continue``, armed). ``list`` and ``library`` starts append the
  first batch at once, so the room's queue is [song + picks].
* **Refill, reconcile, trim** — :func:`look`, every few seconds for each
  room with an active session (the core's ``UpNextExtender`` worker, which
  also writes the rooms' listening history):

  - picks the room holds are found by their files (a cast hands the
    picks over by track, so MPD gave them songids the session didn't
    know) and remembered by songid;
  - a pick gone from the queue (someone removed it) is ``removed``; one the
    room jumped past is ``skipped``; picks a "less like this" took out, or
    every waiting pick when Autoplay was switched off (``library`` and
    ``continue`` modes), leave the queue — never the song playing now;
  - while it plays and 5 or fewer songs are left after the current one
    (``up_next_refill_at``), the next batch is appended by exact file
    path and each pick remembers its songid;
  - more than :data:`TRIM_KEEP_BEHIND` songs behind the current one: the
    oldest go, so a day-long session keeps a small queue and "previous"
    still works.

* **End** — a new start (:func:`store.create` ends the old one), the queue
  replaced by anything else (a stream, a podcast: the client's
  ``queue_generation`` moved on since the session started), the queue
  cleared, the room stopped once it had
  played, or paused / stopped for :data:`IDLE_END`.

Everything here is best-effort: a failure is logged and the music plays on
exactly as it would have without Up next.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from sqlalchemy import text

from . import service, store
from .data import SqlLibrary
from .model import DEFAULT_CONFIG
from .session import SeedRef, UpNextSession

log = logging.getLogger(__name__)

#: Songs kept behind the current one before the oldest are trimmed (§5).
TRIM_KEEP_BEHIND = 50
#: A session whose room has been paused or stopped this long ends (§5).
IDLE_END_SEC = 2 * 3600.0
#: After a refill that found nothing to add, wait this long before the next.
EMPTY_BACKOFF_SEC = 60.0
#: Rooms whose queue is still being loaded (the rest of a long playlist,
#: domovoi/handlers/shared/room_playlist.py): no picks join until it is in.
LOADING: set[str] = set()


def target_for(room_id: str) -> str:
    return f"room:{room_id}"


# ─── In-memory notes (core process only) ──────────────────────────────────


@dataclass
class _Seen:
    """What the extender remembers of a session between looks."""

    generation: int
    active: bool = False                 # seen playing or paused since it started
    idle_since: float | None = None      # monotonic: paused or stopped since
    backoff_until: float = 0.0           # monotonic: no refill before this


#: session id -> what the extender knows of it. :func:`start` writes the
#: room player's queue generation as it was right after the start replaced
#: the queue; a session first seen by the extender (moved from a phone,
#: started before a restart) takes the generation of that first look.
SEEN: dict[str, _Seen] = {}


def _generation(client: Any) -> int:
    try:
        return int(getattr(client, "queue_generation", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _session_scope():
    from domovoi.db.session import session_scope

    return session_scope()


# ─── Files ↔ library songs ────────────────────────────────────────────────


async def uris_for(s: Any, track_ids: Iterable[int]) -> dict[int, str]:
    """track id -> the MPD path of its file, for tracks inside the music
    folder (a file outside it has no MPD path and is left out)."""
    from domovoi.handlers.shared.library_match import mpd_file_for_library_path

    want = sorted({int(t) for t in track_ids})
    if not want:
        return {}
    rows = (await s.execute(text("SELECT id, file_path FROM library_tracks WHERE id = ANY(:ids)"),
                            {"ids": want})).all()
    out: dict[int, str] = {}
    for tid, fp in rows:
        uri = mpd_file_for_library_path(fp or "")
        if uri:
            out[int(tid)] = uri
    return out


async def tracks_for_files(s: Any, files: Iterable[str]) -> dict[str, int]:
    """MPD path -> library track id, for the paths that are library songs.
    One query for the paths stored the way the indexer writes them (the
    music folder + the MPD path), then the listen logger's matching (the
    path under the music folder, in either slash) for the rest. A stream or
    a file the library doesn't know is left out."""
    from domovoi.handlers.shared.library_match import library_path_for_mpd_file
    from domovoi.workers.listen_logger import track_for_file

    want = sorted({str(x) for x in files if x and "://" not in str(x)})
    if not want:
        return {}
    full: dict[str, str] = {}
    for f in want:
        host = library_path_for_mpd_file(f)
        full[host] = f
        full[host.replace("\\", "/")] = f
        full[host.replace("/", "\\")] = f
    rows = (await s.execute(text("SELECT id, file_path FROM library_tracks WHERE file_path = ANY(:p)"),
                            {"p": sorted(full)})).all()
    out: dict[str, int] = {}
    for tid, fp in rows:
        f = full.get(fp)
        if f is not None and f not in out:
            out[f] = int(tid)
    for f in want:
        if f not in out:
            found = await track_for_file(s, f)
            if found is not None:
                out[f] = found[0]
    return out


# ─── Start / end ──────────────────────────────────────────────────────────


async def start(
    room_id: str,
    *,
    mode: str,
    seed: SeedRef,
    name: str | None = None,
    person_id: int | None = None,
    autoplay: bool = True,
    client: Any = None,
) -> str | None:
    """Start the room's session (ending the one before). Call it AFTER the
    start replaced the room's queue. ``list`` / ``library`` append the first
    batch now when ``client`` is the room's player. Returns the session id,
    or None when it couldn't start (logged; the music plays regardless)."""
    try:
        sess = UpNextSession.start(target=target_for(room_id), mode=mode, seed=seed,
                                   person_id=person_id, autoplay=autoplay)
        async with _session_scope() as s:
            await store.create(s, sess, name=name)
        SEEN[sess.id] = _Seen(generation=_generation(client))
    except Exception as e:  # noqa: BLE001 — Up next is a nicety; the play stands
        log.warning("up next: couldn't start %s's session: %s", room_id, e)
        return None
    log.info("up next: %s session %s started in %s", mode, sess.id, room_id)
    if client is not None and mode != "continue":
        try:
            await fill(room_id, client, sess.id)
        except Exception as e:  # noqa: BLE001 — the extender tries again
            log.warning("up next: first batch for %s not queued: %s", room_id, e)
    return sess.id


async def start_from_request(
    room_id: str, *, track_id: int | None, context: Any, person_id: int | None,
    autoplay: bool, client: Any, whole_list: bool = False,
) -> str | None:
    """:func:`start` from a phone's, a browser's or the voice path's
    ``{track_id, context}`` (a :class:`service.ContextIn` or its dict).
    Raises :class:`service.SeedError` for a list the house doesn't have."""
    ctx = context
    if isinstance(ctx, dict):
        ctx = service.ContextIn(**ctx)
    async with _session_scope() as s:
        seed, name = await service.build_seed(s, track_id, ctx)
    mode = "continue" if whole_list else ("list" if seed.kind else "library")
    return await start(room_id, mode=mode, seed=seed, name=name, person_id=person_id,
                       autoplay=autoplay, client=client)


async def end(room_id: str, why: str = "") -> list[str]:
    """End the room's active session (a stop, a clear, a play that isn't
    Up next's). Best-effort; returns the ids ended."""
    try:
        async with _session_scope() as s:
            ended = await store.end_active(s, target_for(room_id))
    except Exception as e:  # noqa: BLE001
        log.debug("up next: ending %s's session failed: %s", room_id, e)
        return []
    for sid in ended:
        SEEN.pop(sid, None)
        log.info("up next: session %s in %s ended%s", sid, room_id, f" ({why})" if why else "")
    return ended


async def move_here(room_id: str, session_id: str, client: Any = None) -> bool:
    """A phone's or browser's session carries on in the room it just cast
    to (``play-tracks`` with ``up_next_session_id``). The room's own session
    ends. Its picks are found in the room's queue by file on the next look."""
    try:
        async with _session_scope() as s:
            st = await store.lock(s, session_id)
            if st is None or st.ended_at is not None:
                return False
            if not await store.move(s, st, target_for(room_id)):
                return False
            await store.set_song_ids(s, st.sess.id, {p["seq"]: None for p in await store.picks(s, st.sess.id)})
        SEEN[session_id] = _Seen(generation=_generation(client))
        log.info("up next: session %s moved to %s", session_id, room_id)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("up next: moving session %s to %s failed: %s", session_id, room_id, e)
        return False


# ─── Refill ───────────────────────────────────────────────────────────────


async def fill(room_id: str, client: Any, session_id: str) -> int:
    """Draw the session's next batch and append it to the room's queue by
    exact file; each pick remembers the songid it got. Returns the picks
    queued. A pick whose file MPD refused (or that sits outside the music
    folder) is ``removed`` at once, so nothing waits for a song that isn't
    coming."""
    add = getattr(client, "queue_add_files", None)
    if add is None:
        return 0
    async with _session_scope() as s:
        st = await store.lock(s, session_id)
        if st is None or st.ended_at is not None or st.sess.ended:
            return 0
        _, picks, _ = await service.draw(s, st)
        uris = await uris_for(s, [p["track_id"] for p in picks])
    if not picks:
        seen = SEEN.get(session_id)
        if seen is not None:
            seen.backoff_until = time.monotonic() + EMPTY_BACKOFF_SEC
        return 0
    wanted = [(p["seq"], uris.get(int(p["track_id"]))) for p in picks]
    sendable = [(seq, uri) for seq, uri in wanted if uri]
    answers: list[Any] = []
    if sendable:
        answers = list(await add([uri for _, uri in sendable]))
    got: dict[int, int] = {}
    for (seq, _), ans in zip(sendable, answers):
        if ans and ans.get("id") is not None:
            got[int(seq)] = int(ans["id"])
    lost = [seq for seq, _ in wanted if seq not in got]
    async with _session_scope() as s:
        await store.set_song_ids(s, session_id, got)
        if lost:
            await store.set_status(s, session_id, lost, "removed")
    log.debug("up next: %d picks queued in %s (%d not playable)", len(got), room_id, len(lost))
    return len(got)


async def look(room_id: str, client: Any, snap: dict[str, Any], *,
               now: float | None = None, refill_at: int = DEFAULT_CONFIG.up_next_refill_at) -> dict[str, Any]:
    """One look at a room with a session (the extender's tick, after the
    listen logger read ``snap``). Returns what it did, for tests and logs:
    ``{session, ended, linked, removed, trimmed, queued}``."""
    did: dict[str, Any] = {"session": None, "ended": None, "linked": 0, "removed": 0,
                           "trimmed": 0, "queued": 0}
    now = time.monotonic() if now is None else now
    async with _session_scope() as s:
        st = await store.active_for(s, target_for(room_id))
    if st is None:
        return did
    sid = st.sess.id
    did["session"] = sid
    gen = _generation(client)
    seen = SEEN.get(sid)
    if seen is None:
        seen = SEEN[sid] = _Seen(generation=gen)
    state = snap.get("state") or "stop"

    async def _end(why: str) -> dict[str, Any]:
        async with _session_scope() as s2:
            await store.end(s2, sid)
        SEEN.pop(sid, None)
        did["ended"] = why
        log.info("up next: session %s in %s ended (%s)", sid, room_id, why)
        return did

    if gen > seen.generation:
        return await _end("the queue was replaced")
    if state in ("play", "pause"):
        seen.active = True
    if state == "play":
        seen.idle_since = None
    elif seen.idle_since is None:
        seen.idle_since = now
    if state == "stop" and seen.active:
        return await _end("the room stopped")
    if seen.idle_since is not None and now - seen.idle_since >= IDLE_END_SEC:
        return await _end("idle")
    if state != "play":
        return did

    queue = await client.queue_list()
    ids = [int(e["id"]) for e in queue if e.get("id") is not None]
    songid = snap.get("songid")
    try:
        cur = ids.index(int(songid)) if songid is not None else None
    except ValueError:
        cur = None
    if cur is None:
        return did
    files = {int(e["id"]): str(e.get("file") or "") for e in queue if e.get("id") is not None}

    drop: list[int] = []
    async with _session_scope() as s:
        st = await store.lock(s, sid)
        if st is None or st.ended_at is not None:
            return did
        all_picks = await store.picks(s, sid)
        held = {int(p["mpd_song_id"]) for p in all_picks if p["mpd_song_id"] is not None}
        # Picks the room holds but the session doesn't know by songid yet
        # (a cast): by file, in order, from the song playing now on.
        unlinked = [p for p in all_picks if p["status"] == "queued" and p["mpd_song_id"] is None]
        if unlinked:
            uris = await uris_for(s, [p["track_id"] for p in unlinked])
            free = [i for i in ids[cur:] if i not in held]
            links: dict[int, int] = {}
            for p in unlinked:
                uri = uris.get(int(p["track_id"]))
                hit = next((i for i in free if files.get(i) == uri), None) if uri else None
                if hit is not None:
                    links[int(p["seq"])] = hit
                    free.remove(hit)
                    p["mpd_song_id"] = hit
            if links:
                await store.set_song_ids(s, sid, links)
                did["linked"] = len(links)
        pos = {i: n for n, i in enumerate(ids)}
        gone, passed, out = [], [], []
        for p in all_picks:
            m = p["mpd_song_id"]
            if p["status"] != "queued" or m is None:
                if p["status"] == "removed" and m is not None and pos.get(int(m), -1) > cur:
                    out.append(int(m))
                continue
            at = pos.get(int(m))
            if at is None:
                gone.append(int(p["seq"]))
            elif at < cur:
                passed.append(int(p["seq"]))
            elif at > cur and not st.sess.autoplay and st.sess.mode != "list":
                # Autoplay off: the mix stops; a list keeps the rest of itself.
                out.append(int(m))
                await store.set_status(s, sid, [p["seq"]], "removed")
        if gone:
            await store.set_status(s, sid, gone, "removed")
        if passed:
            await store.set_status(s, sid, passed, "skipped")
        drop = sorted(set(out))
    if drop:
        removed = await client.queue_remove(drop)
        did["removed"] = len(removed)
        ids = [i for i in ids if i not in set(removed)]
        cur = ids.index(int(songid))
    if cur > TRIM_KEEP_BEHIND:
        old = ids[: cur - TRIM_KEEP_BEHIND]
        trimmed = await client.queue_remove(old)
        did["trimmed"] = len(trimmed)
        ids = [i for i in ids if i not in set(trimmed)]
        cur = ids.index(int(songid))
    left = len(ids) - cur - 1
    may_draw = not st.sess.ended and (st.sess.autoplay or st.sess.mode == "list")
    if left <= refill_at and may_draw and now >= seen.backoff_until and room_id not in LOADING:
        did["queued"] = await fill(room_id, client, sid)
    return did


# ─── The listening history feeds the session ──────────────────────────────


async def song_ended(room_id: str, *, songid: Any, track_id: int | None, played_sec: float,
                     duration_sec: float | None, outcome: str) -> tuple[str | None, str]:
    """A song the room played ended (the listen logger closed it): the
    session's drift seeds move, its early-skip rule applies, and a pick is
    marked played or skipped. Returns ``(session_id, origin)`` for the
    listen row: ``auto`` for a pick, else ``explicit``; no session -> None."""
    try:
        async with _session_scope() as s:
            st = await store.active_for(s, target_for(room_id))
            if st is None:
                return None, "explicit"
            st = await store.lock(s, st.sess.id)
            if st is None:
                return None, "explicit"
            sid = st.sess.id
            pick = None
            if songid is not None:
                try:
                    pick = (await store.picks_by_song(s, sid, [int(songid)])).get(int(songid))
                except (TypeError, ValueError):
                    pick = None
            if pick is not None and pick["status"] == "queued":
                await store.set_status(s, sid, [pick["seq"]], "skipped" if outcome == "skipped" else "played")
            if track_id is not None:
                track = (await SqlLibrary(s).tracks([track_id])).get(int(track_id))
                if track is not None:
                    excluded = st.sess.record_listen(track, played_sec=played_sec,
                                                     duration_sec=duration_sec, outcome=outcome)
                    if excluded:
                        await service.take_back(s, st, track)
                    await store.save(s, st)
            return sid, ("auto" if pick is not None else "explicit")
    except Exception as e:  # noqa: BLE001 — a mix is a nicety; the listen still counts
        log.debug("up next: %s's song not fed to its session: %s", room_id, e)
        return None, "explicit"


# ─── What the room's queue shows ──────────────────────────────────────────


async def queue_view(room_id: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    """The room queue's Up next fields (``GET …/queue/{room}``): each item
    gains ``origin`` (``auto`` for a pick, else ``explicit``), and for a
    pick ``reason_label`` and ``up_next_seq``; every item its ``track_id``
    when it is a library song (else null). Returns ``{session_id, autoplay,
    up_next}`` for the queue itself (all null without a session). Never
    names a person: the queue is household-visible."""
    out: dict[str, Any] = {"session_id": None, "autoplay": None, "up_next": None}
    for it in items:
        it.setdefault("origin", "explicit")
        it.setdefault("reason_label", None)
        it.setdefault("up_next_seq", None)
        it.setdefault("track_id", None)
    try:
        async with _session_scope() as s:
            by_file = await tracks_for_files(s, [it.get("file") for it in items])
            for it in items:
                it["track_id"] = by_file.get(str(it.get("file") or ""))
            st = await store.active_for(s, target_for(room_id))
            if st is None:
                return out
            picks = await store.picks_by_song(s, st.sess.id, [int(it["song_id"]) for it in items])
            for it in items:
                p = picks.get(int(it["song_id"]))
                if p is not None and p["status"] != "removed":
                    it["origin"] = "auto"
                    it["reason_label"] = p["reason_label"]
                    it["up_next_seq"] = int(p["seq"])
            seed = await service.track_row(s, st.sess.seed.track_id)
            out.update({
                "session_id": st.sess.id,
                "autoplay": st.sess.autoplay,
                "up_next": {
                    "session_id": st.sess.id,
                    "mode": st.sess.mode,
                    "autoplay": st.sess.autoplay,
                    "seed": {
                        "track_id": st.sess.seed.track_id,
                        "title": (seed or {}).get("title"),
                        "artist": (seed or {}).get("artist"),
                        "label": st.sess.seed.label,
                        "list": ({"kind": st.sess.seed.kind, "name": st.list_name}
                                 if st.sess.seed.kind else None),
                    },
                    "ended": st.sess.ended,
                },
            })
    except Exception as e:  # noqa: BLE001 — the queue reads without its reasons
        log.debug("up next: %s's queue read without its picks: %s", room_id, e)
    return out
