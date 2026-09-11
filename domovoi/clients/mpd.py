"""MPD (Music Player Daemon) client.

Thin async wrapper around `python-mpd2`'s async client. One connection per
command keeps state simple (MPD is local, per-command handshake is ~1 ms).
A future revision may add a persistent idle-listening connection for play-state change
notifications.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Protocol

from domovoi.config import settings

log = logging.getLogger(__name__)


class MPDClient(Protocol):
    async def play_search(self, query: dict[str, str]) -> dict[str, Any] | None: ...
    # Read-only: search and return the first match WITHOUT touching the queue
    # or playback (for "do I have X?" / "find X" library metadata lookups).
    async def search_only(self, query: dict[str, str]) -> dict[str, Any] | None: ...
    async def play_filename(self, *substrings: str) -> dict[str, Any] | None: ...
    async def play_url(self, url: str, *, title: str | None = None, artist: str | None = None) -> bool: ...
    # `prepare_*` variants mirror `play_*` but leave MPD paused on the
    # queued track instead of starting playback. The streaming layer
    # calls `resume()` once the satellite signals (`music_ready`) that
    # its mpg123 subprocess has connected and primed its buffer with
    # silence frames — eliminates the first-second stutter you'd
    # otherwise hear when mpg123 joins a real-time MP3 stream mid-song.
    async def prepare_search(self, query: dict[str, str]) -> dict[str, Any] | None: ...
    async def prepare_filename(self, *substrings: str) -> dict[str, Any] | None: ...
    async def prepare_url(self, url: str, *, title: str | None = None, artist: str | None = None) -> bool: ...
    # Load an ordered list of library tracks into the queue at once (browser
    # "cast this queue to a room" hand-off). Each spec is a dict with any of
    # ``title`` / ``artist`` / ``file_path``; the client resolves each to a
    # file the same three-stage way the single-track paths do (tag search →
    # filename substring → basename). Returns the songs it actually queued,
    # in order. Leaves MPD paused on the first track like the other
    # ``prepare_*`` variants so the streaming layer's music_ready handshake
    # applies unchanged.
    async def prepare_tracks(self, specs: list[dict[str, str]]) -> list[dict[str, Any]]: ...
    # ── Queue editing (the dashboard/app room-queue surface) ──────────────
    # These operate on the EXISTING queue rather than replacing it, which is
    # what separates them from every `play_*`/`prepare_*` method above (all
    # of which clear first). Entries are addressed by MPD **songid**, not
    # position: a songid is stable for the life of a queue entry, so a
    # concurrent move or insert can't turn "remove item 3" into "remove the
    # wrong song". Every dict carries at least ``id`` and ``pos``.
    async def queue_list(self) -> list[dict[str, Any]]: ...
    # Append without clearing; returns the songs actually queued, each with
    # the ``id`` MPD assigned.
    async def queue_add_tracks(self, specs: list[dict[str, str]]) -> list[dict[str, Any]]: ...
    # Returns the songids actually removed (a stale id is skipped, not fatal).
    async def queue_remove(self, song_ids: list[int]) -> list[int]: ...
    async def queue_move(self, song_id: int, to_position: int) -> bool: ...
    async def queue_clear(self) -> None: ...
    async def pause(self) -> None: ...
    async def resume(self) -> None: ...
    async def stop(self) -> None: ...
    async def next(self) -> None: ...
    async def previous(self) -> None: ...
    async def current_song(self) -> dict[str, Any] | None: ...
    async def state(self) -> str: ...
    async def set_volume(self, level: int) -> None: ...
    async def get_volume(self) -> int | None: ...
    async def update_library(self) -> None: ...
    async def seek_cur(self, delta_sec: float) -> None: ...
    async def seek_to(self, position_sec: float) -> None: ...
    async def elapsed_sec(self) -> float | None: ...


class MPDStubClient:
    """In-memory fake for tests and USE_STUBS mode."""

    def __init__(self) -> None:
        self._state = "stop"
        self._song: dict[str, Any] | None = None
        self._volume = 50
        # Queue model: entries carry a monotonically-increasing ``id`` that is
        # never reused, exactly like MPD's songid. ``pos`` is derived from list
        # order on read, so a move can't leave the two inconsistent.
        self._queue: list[dict[str, Any]] = []
        self._next_song_id = 1

    def _assign_ids(self, songs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for song in songs:
            entry = dict(song)
            entry["id"] = self._next_song_id
            self._next_song_id += 1
            out.append(entry)
        return out

    async def play_search(self, query: dict[str, str]) -> dict[str, Any] | None:
        # Fake match from the first query value.
        title = next(iter(query.values()), "unknown")
        self._song = {"file": f"stub/{title}.mp3", "title": title, "artist": "Stub Artist"}
        self._state = "play"
        return self._song

    async def search_only(self, query: dict[str, str]) -> dict[str, Any] | None:
        # Read-only: return a hit WITHOUT mutating playback state.
        title = next(iter(query.values()), "unknown")
        return {"file": f"stub/{title}.mp3", "title": title, "artist": "Stub Artist"}

    async def play_filename(self, *substrings: str) -> dict[str, Any] | None:
        if not substrings:
            return None
        joined = " ".join(substrings)
        self._song = {"file": f"stub/{joined}.mp3"}
        self._state = "play"
        return self._song

    async def play_url(self, url: str, *, title: str | None = None, artist: str | None = None) -> bool:
        self._song = {"file": url, "title": title or "stream", "artist": artist or ""}
        self._state = "play"
        return True

    async def prepare_search(self, query: dict[str, str]) -> dict[str, Any] | None:
        song = await self.play_search(query)
        if song is not None:
            self._state = "pause"
        return song

    async def prepare_filename(self, *substrings: str) -> dict[str, Any] | None:
        song = await self.play_filename(*substrings)
        if song is not None:
            self._state = "pause"
        return song

    async def prepare_url(self, url: str, *, title: str | None = None, artist: str | None = None) -> bool:
        ok = await self.play_url(url, title=title, artist=artist)
        if ok:
            self._state = "pause"
        return ok

    def _songs_for(self, specs: list[dict[str, str]]) -> list[dict[str, Any]]:
        return [
            {
                "file": f"stub/{spec.get('title') or spec.get('file_path') or 'unknown'}.mp3",
                "title": spec.get("title"),
                "artist": spec.get("artist"),
            }
            for spec in specs
        ]

    async def prepare_tracks(self, specs: list[dict[str, str]]) -> list[dict[str, Any]]:
        # Replaces the queue (the cast hand-off), so ids restart from wherever
        # the counter is — never reused, matching MPD.
        queued = self._assign_ids(self._songs_for(specs))
        self._queue = list(queued)
        if queued:
            self._song = queued[0]
            self._state = "pause"
        return queued

    # ── Queue editing ──────────────────────────────────────────────────

    async def queue_list(self) -> list[dict[str, Any]]:
        return [dict(e, pos=i) for i, e in enumerate(self._queue)]

    async def queue_add_tracks(self, specs: list[dict[str, str]]) -> list[dict[str, Any]]:
        queued = self._assign_ids(self._songs_for(specs))
        self._queue.extend(queued)
        return queued

    async def queue_remove(self, song_ids: list[int]) -> list[int]:
        wanted = set(song_ids)
        present = {int(e["id"]) for e in self._queue} & wanted
        self._queue = [e for e in self._queue if int(e["id"]) not in present]
        # Preserve the caller's order in the report so a partial removal is
        # readable against the request.
        return [sid for sid in song_ids if sid in present]

    async def queue_move(self, song_id: int, to_position: int) -> bool:
        idx = next(
            (i for i, e in enumerate(self._queue) if int(e["id"]) == song_id), None
        )
        if idx is None:
            return False
        entry = self._queue.pop(idx)
        self._queue.insert(max(0, min(to_position, len(self._queue))), entry)
        return True

    async def queue_clear(self) -> None:
        self._queue = []
        self._state = "stop"
        self._song = None

    async def pause(self) -> None:
        if self._state == "play":
            self._state = "pause"

    async def resume(self) -> None:
        if self._state == "pause":
            self._state = "play"

    async def stop(self) -> None:
        self._state = "stop"

    async def next(self) -> None:
        pass

    async def previous(self) -> None:
        pass

    async def current_song(self) -> dict[str, Any] | None:
        if self._state == "stop":
            return None
        return self._song

    async def state(self) -> str:
        return self._state

    async def set_volume(self, level: int) -> None:
        self._volume = max(0, min(100, level))

    async def get_volume(self) -> int | None:
        return self._volume

    async def update_library(self) -> None:
        pass

    async def seek_cur(self, delta_sec: float) -> None:
        cur = float((self._song or {}).get("_elapsed", 0.0)) if self._song else 0.0
        new = max(0.0, cur + delta_sec)
        if self._song is not None:
            self._song["_elapsed"] = new

    async def seek_to(self, position_sec: float) -> None:
        if self._song is not None:
            self._song["_elapsed"] = max(0.0, position_sec)

    async def elapsed_sec(self) -> float | None:
        if self._state == "stop" or self._song is None:
            return None
        return float(self._song.get("_elapsed", 0.0))


class RealMPDClient:
    """Real MPD connection manager. Connects per-command."""

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[Any]:
        from mpd.asyncio import MPDClient as _MPDClient

        c = _MPDClient()
        await c.connect(self.host, self.port)
        try:
            yield c
        finally:
            try:
                c.disconnect()
            except Exception:
                pass

    async def _queue_first_search_hit(
        self, c: Any, search_args: list[str], *, start: bool
    ) -> dict[str, Any] | None:
        """Run MPD `search`, queue the first hit, and either play or pause.

        `start=False` leaves MPD on the queued track but paused so the
        satellite's mpg123 can connect and prime its buffer against the
        always-on silence stream before the song actually plays.
        Resume happens via the streaming layer once the Pi sends
        `music_ready`. See the prepare_* docstrings.
        """
        try:
            results = await c.search(*search_args)
        except Exception as e:
            log.warning("MPD search failed: %s", e)
            return None
        if not results:
            return None
        first = results[0]
        await c.clear()
        await c.add(first["file"])
        if start:
            await c.play()
        else:
            await c.play()
            await c.pause(1)
        return first

    async def play_search(self, query: dict[str, str]) -> dict[str, Any] | None:
        """Clear queue, search by one or more tag:value pairs, add first match, play.

        `query` keys are MPD tag names: "title", "artist", "album", "any".
        """
        async with self._connect() as c:
            search_args: list[str] = []
            for k, v in query.items():
                search_args.extend([k, v])
            return await self._queue_first_search_hit(c, search_args, start=True)

    async def search_only(self, query: dict[str, str]) -> dict[str, Any] | None:
        """Search by tag:value pair(s) and return the first match WITHOUT
        clearing the queue or starting playback. Backs the read-only library
        lookups ("do I have X?", "find X") so a metadata question never
        hijacks whatever is currently playing in the room."""
        async with self._connect() as c:
            search_args: list[str] = []
            for k, v in query.items():
                search_args.extend([k, v])
            try:
                results = await c.search(*search_args)
            except Exception as e:
                log.warning("MPD search_only failed: %s", e)
                return None
            return results[0] if results else None

    async def play_filename(self, *substrings: str) -> dict[str, Any] | None:
        """Search by filename substring(s) (ANDed), play the first match.

        Used as a fallback when tag-based `play_search` misses — common
        for libraries with empty/missing ID3 tags where MPD knows the file
        exists at /music/<name>.mp3 but has no Title/Artist metadata to
        match a `search title ... artist ...` query against. MPD's
        `search file <substring>` is case-insensitive substring; multiple
        `file` terms AND together so we can require both title and
        artist words to appear in the path.
        """
        if not substrings:
            return None
        async with self._connect() as c:
            search_args: list[str] = []
            for s in substrings:
                if s:
                    search_args.extend(["file", s])
            if not search_args:
                return None
            return await self._queue_first_search_hit(c, search_args, start=True)

    async def play_url(self, url: str, *, title: str | None = None, artist: str | None = None) -> bool:
        """Clear queue, add a stream URL, play. Returns True on success.

        MPD opens an HTTP connection on `play`. Time-limited URLs (like
        the ones media providers resolve for external streams) work as long as MPD reads
        the stream before they expire — typically not an issue.

        If ``title``/``artist`` are provided, stamp them onto the queued
        song via ``addtagid`` so ``currentsong`` returns useful metadata.
        External proxy streams often carry no ID3, so without this MPD reports
        just the URL — which the dashboard would otherwise render as a
        raw ``videoplayback?expire=…`` blob.
        """
        async with self._connect() as c:
            return await self._queue_url(c, url, title=title, artist=artist, start=True)

    async def _queue_url(
        self,
        c: Any,
        url: str,
        *,
        title: str | None,
        artist: str | None,
        start: bool,
    ) -> bool:
        try:
            await c.clear()
            song_id = await c.addid(url)
            if title:
                try:
                    await c.addtagid(song_id, "title", title)
                except Exception as e:
                    log.debug("MPD addtagid title failed: %s", e)
            if artist:
                try:
                    await c.addtagid(song_id, "artist", artist)
                except Exception as e:
                    log.debug("MPD addtagid artist failed: %s", e)
            if start:
                await c.play()
            else:
                # `pause 1` on its own is a no-op when MPD is stopped, so
                # we have to start playback first and immediately pause
                # to land on the queued track in the paused state.
                await c.play()
                await c.pause(1)
            return True
        except Exception as e:
            log.warning("MPD queue_url failed (%s): %s", url[:80], e)
            return False

    async def prepare_search(self, query: dict[str, str]) -> dict[str, Any] | None:
        """Like `play_search` but leaves MPD paused on the queued track.

        The streaming layer fires `resume()` once the satellite's mpg123
        sends `music_ready`. Until then, MPD's httpd encoder keeps
        emitting silence frames (always_on=yes), which mpg123 reads
        into its ALSA pre-roll. When resume fires, song frames flow
        into an already-primed buffer — no startup underrun.
        """
        async with self._connect() as c:
            search_args: list[str] = []
            for k, v in query.items():
                search_args.extend([k, v])
            return await self._queue_first_search_hit(c, search_args, start=False)

    async def prepare_filename(self, *substrings: str) -> dict[str, Any] | None:
        """Paused-queue sibling of `play_filename`; see `prepare_search`."""
        if not substrings:
            return None
        async with self._connect() as c:
            search_args: list[str] = []
            for s in substrings:
                if s:
                    search_args.extend(["file", s])
            if not search_args:
                return None
            return await self._queue_first_search_hit(c, search_args, start=False)

    async def prepare_url(self, url: str, *, title: str | None = None, artist: str | None = None) -> bool:
        """Paused-queue sibling of `play_url`; see `prepare_search`."""
        async with self._connect() as c:
            return await self._queue_url(c, url, title=title, artist=artist, start=False)

    async def _resolve_track(
        self, c: Any, spec: dict[str, str]
    ) -> dict[str, Any] | None:
        """Resolve one track spec to an MPD song dict WITHOUT touching the
        queue, using the same three-stage lookup the single-track admin
        paths use: tag search → filename substrings → basename. Returns the
        first hit or ``None``."""
        from pathlib import PurePath

        title = (spec.get("title") or "").strip()
        artist = (spec.get("artist") or "").strip()
        file_path = (spec.get("file_path") or "").strip()

        arg_lists: list[list[str]] = []
        if title and artist:
            arg_lists.append(["title", title, "artist", artist])
        if title:
            arg_lists.append(["title", title])
        subs = [s for s in (title, artist) if s]
        if subs:
            args: list[str] = []
            for s in subs:
                args.extend(["file", s])
            arg_lists.append(args)
        if file_path:
            base = PurePath(file_path).name
            if base:
                arg_lists.append(["file", base])

        for args in arg_lists:
            try:
                results = await c.search(*args)
            except Exception as e:
                log.debug("MPD prepare_tracks search %r failed: %s", args, e)
                continue
            if results:
                return results[0]
        return None

    async def prepare_tracks(self, specs: list[dict[str, str]]) -> list[dict[str, Any]]:
        """Clear the queue, resolve+add each spec in order, leave MPD paused
        on the first track. Skips specs MPD can't find (a stale/renamed file
        shouldn't abort the whole cast); returns the songs actually queued."""
        if not specs:
            return []
        async with self._connect() as c:
            await c.clear()
            queued: list[dict[str, Any]] = []
            for spec in specs:
                song = await self._resolve_track(c, spec)
                if song is None:
                    continue
                try:
                    await c.add(song["file"])
                    queued.append(dict(song))
                except Exception as e:
                    log.warning("MPD prepare_tracks add %r failed: %s", song.get("file"), e)
            if queued:
                # Start then immediately pause to land on the first queued
                # track in the paused state — same trick prepare_url uses.
                await c.play()
                await c.pause(1)
            return queued

    # ── Queue editing ──────────────────────────────────────────────────

    async def queue_list(self) -> list[dict[str, Any]]:
        """The room's current queue, in order. MPD's ``playlistinfo`` already
        carries ``Id`` (songid) and ``Pos``; both are normalized to the lower-
        case ``id``/``pos`` the callers use, so a caller never has to know
        which of MPD's two capitalizations a given build returns."""
        async with self._connect() as c:
            try:
                entries = await c.playlistinfo()
            except Exception as e:
                log.warning("MPD playlistinfo failed: %s", e)
                return []
        out: list[dict[str, Any]] = []
        for i, raw in enumerate(entries):
            entry = dict(raw)
            song_id = entry.get("id") or entry.get("Id")
            pos = entry.get("pos") or entry.get("Pos")
            try:
                entry["id"] = int(song_id) if song_id is not None else None
            except (TypeError, ValueError):
                entry["id"] = None
            try:
                entry["pos"] = int(pos) if pos is not None else i
            except (TypeError, ValueError):
                entry["pos"] = i
            out.append(entry)
        # An entry with no songid can't be addressed, so it can't be edited —
        # drop it rather than handing the UI a row whose buttons would 404.
        return [e for e in out if e["id"] is not None]

    async def queue_add_tracks(
        self, specs: list[dict[str, str]]
    ) -> list[dict[str, Any]]:
        """APPEND to the queue — deliberately no ``clear()``, which is the
        whole difference from :meth:`prepare_tracks`. Uses ``addid`` so the
        new songid comes back in the same round trip; a spec MPD can't resolve
        is skipped (a renamed file shouldn't abort the whole add)."""
        if not specs:
            return []
        async with self._connect() as c:
            queued: list[dict[str, Any]] = []
            for spec in specs:
                song = await self._resolve_track(c, spec)
                if song is None:
                    continue
                try:
                    song_id = await c.addid(song["file"])
                except Exception as e:
                    log.warning(
                        "MPD queue_add_tracks addid %r failed: %s",
                        song.get("file"), e,
                    )
                    continue
                entry = dict(song)
                try:
                    entry["id"] = int(song_id)
                except (TypeError, ValueError):
                    # No usable id ⇒ the caller can't address it later; the
                    # song IS queued, it just won't carry provenance.
                    entry["id"] = None
                queued.append(entry)
            return queued

    async def queue_remove(self, song_ids: list[int]) -> list[int]:
        """``deleteid`` each id. A stale id (already played off the queue, or
        removed by someone else) raises and is skipped — two people pruning
        the same queue shouldn't produce an error either of them can act on."""
        if not song_ids:
            return []
        removed: list[int] = []
        async with self._connect() as c:
            for song_id in song_ids:
                try:
                    await c.deleteid(song_id)
                    removed.append(song_id)
                except Exception as e:
                    log.debug("MPD deleteid %s failed (stale?): %s", song_id, e)
        return removed

    async def queue_move(self, song_id: int, to_position: int) -> bool:
        """``moveid`` — move by songid, to an absolute position. False when
        MPD refuses (unknown id, or a position past the end)."""
        async with self._connect() as c:
            try:
                await c.moveid(song_id, max(0, to_position))
                return True
            except Exception as e:
                log.debug("MPD moveid %s→%s failed: %s", song_id, to_position, e)
                return False

    async def queue_clear(self) -> None:
        async with self._connect() as c:
            await c.clear()

    async def pause(self) -> None:
        async with self._connect() as c:
            await c.pause(1)

    async def resume(self) -> None:
        async with self._connect() as c:
            await c.pause(0)

    async def stop(self) -> None:
        async with self._connect() as c:
            await c.stop()

    async def next(self) -> None:
        async with self._connect() as c:
            await c.next()

    async def previous(self) -> None:
        async with self._connect() as c:
            await c.previous()

    async def current_song(self) -> dict[str, Any] | None:
        async with self._connect() as c:
            song = await c.currentsong()
            return dict(song) if song else None

    async def state(self) -> str:
        """Return MPD's playback state — ``play``, ``pause``, or
        ``stop``. Anything unexpected (or a connection failure
        propagated by the surrounding ``_connect``) is treated as
        ``stop`` so callers can default to the safe direction
        without inspecting exceptions."""
        async with self._connect() as c:
            status = await c.status()
        raw = status.get("state", "stop")
        return raw if raw in ("play", "pause", "stop") else "stop"

    async def set_volume(self, level: int) -> None:
        clamped = max(0, min(100, level))
        async with self._connect() as c:
            await c.setvol(clamped)

    async def get_volume(self) -> int | None:
        async with self._connect() as c:
            status = await c.status()
            vol = status.get("volume")
            return int(vol) if vol is not None else None

    async def update_library(self) -> None:
        """Trigger MPD to rescan the music_directory for new files."""
        async with self._connect() as c:
            await c.update()

    async def seek_cur(self, delta_sec: float) -> None:
        """Relative seek within the current song. Used by spoken audio's
        "skip forward/back 30 seconds" and chapter navigation. MPD's
        ``seekcur`` accepts a signed offset with a ``+``/``-`` prefix."""
        sign = "+" if delta_sec >= 0 else "-"
        async with self._connect() as c:
            await c.seekcur(f"{sign}{abs(delta_sec):.0f}")

    async def seek_to(self, position_sec: float) -> None:
        """Absolute seek to ``position_sec`` within the current song —
        chapter jump and resume-from-saved-position both land here."""
        async with self._connect() as c:
            await c.seekcur(f"{max(0.0, position_sec):.0f}")

    async def elapsed_sec(self) -> float | None:
        """Current playback offset in seconds (MPD ``status.elapsed``), or
        None when stopped / unavailable. Read for position-save."""
        async with self._connect() as c:
            status = await c.status()
        raw = status.get("elapsed")
        try:
            return float(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None


# Room → (control_port, http_port). Populated by `mpd_provisioner.ensure_room`
# (lazy first-connect path) and `warm_known_rooms` (domovoi startup).
# Reads are sync because handlers shouldn't pay a DB hit on the hot path —
# provisioning is the only writer, and it's serialized through the
# advisory-lock allocation in mpd_provisioner.
_room_ports: dict[str, tuple[int, int]] = {}

# Per-room client cache. Kept separate from `_room_ports` so tests can
# inject stubs without faking out the port map (and vice versa).
_clients: dict[str, MPDClient] = {}


def _resolve_room(room_id: str | None) -> str | None:
    """Pick which room a room-agnostic call should target.

    For programmatic /v1/intent calls (curl, tests) and fan-out helpers
    that don't have a WebSocket-bound room, fall back to the first
    provisioned room so single-room dev still works. Returns None only
    when nothing has been provisioned yet.
    """
    if room_id and room_id in _room_ports:
        return room_id
    if not _room_ports:
        return None
    return next(iter(_room_ports.keys()))


class MPDNotProvisioned(RuntimeError):
    """No room has an MPD daemon yet.

    Raised when a handler asks for a client before any satellite has ever
    connected. This is a NORMAL state on a fresh install — the server is
    fully usable over ``/v1/intent`` before a single Pi exists — so the
    router catches it and degrades to a spoken explanation rather than
    letting it surface as a 500. Typed (not a bare RuntimeError) so that
    catch stays narrow and never swallows a real MPD fault.
    """


def get_mpd_client_for(room_id: str | None) -> MPDClient:
    """Return the MPD client bound to ``room_id``'s daemon.

    Resolution order:
      1. ``room_id`` if it's been provisioned.
      2. The first provisioned room (programmatic / fan-out callers).
      3. A throwaway stub when ``USE_STUBS=true`` (test ergonomics — the
         suite doesn't run docker so it never provisions a real room).
      4. Raise — handlers in production should never reach this; satellite
         connect provisions before any handler runs.
    """
    if settings.use_stubs:
        # Tests: per-room stub if seeded, otherwise auto-create one. Keeps
        # the `_clients["kitchen"] = stub` pattern in tests working without
        # forcing them to also seed `_room_ports`.
        key = room_id or "_default_"
        cached = _clients.get(key)
        if cached is not None:
            return cached
        client: MPDClient = MPDStubClient()
        _clients[key] = client
        return client

    key = _resolve_room(room_id)
    if key is None:
        raise MPDNotProvisioned(
            "No MPD rooms provisioned yet. A satellite must connect to "
            "/v1/stream/{room_id} at least once before handlers can use MPD."
        )
    cached = _clients.get(key)
    if cached is not None:
        return cached
    port, _http = _room_ports[key]
    client = RealMPDClient(settings.mpd_host, port)
    _clients[key] = client
    return client


def iter_mpd_clients() -> list[tuple[str, MPDClient]]:
    """Return ``(room_id, client)`` for every provisioned room.

    Empty when no satellite has ever connected. Used by fan-out helpers
    (post-download rescan, voice "rescan library") that need to
    update every per-room MPD database — each daemon has its own DB so
    one update doesn't index the file in the others.
    """
    if settings.use_stubs:
        # In stub mode, return whatever stubs the tests have seeded.
        return list(_clients.items())
    return [(room, get_mpd_client_for(room)) for room in _room_ports.keys()]


def mpd_stream_url_for(room_id: str | None) -> str | None:
    """Per-room HTTP stream URL the satellite consumes. ``None`` when unprovisioned."""
    key = _resolve_room(room_id)
    if key is None:
        return None
    _, http = _room_ports[key]
    return f"{settings.mpd_http_base.rstrip('/')}:{http}"
