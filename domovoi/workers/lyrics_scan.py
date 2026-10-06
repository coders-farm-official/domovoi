"""The local lyrics scan: a ``.lrc`` beside each song, and the lyrics
inside the file (contract §5.3, [K1]–[K10]; §5.4 for whose ``.lrc`` it is).

Always on — it only reads the household's own files, and nothing leaves
the house for them. Every few minutes (``lyrics_scan_interval_sec``) it
looks at every library track: each folder is listed once, each audio file
and its sidecar ``stat``-ed, and a track is READ AGAIN only when it has
never been read, the reading rules changed (``SCAN_VERSION``), the audio
file changed (mtime or size), or its ``.lrc`` changed, appeared or
disappeared. Unchanged tracks are not written.

Reading a track: the household's ``.lrc`` wins (whatever it holds); a
``.lrc`` Domovoi wrote itself is a copy of LRCLIB's lyrics and is never
read as the household's (the lyrics then come from the tags, or there are
none); a file Domovoi wrote that has since been edited or deleted turns the
row to ``edited`` / ``deleted`` — the household's now, never written again.

**Safety.** Reading only: the scan never writes, renames or deletes a file.
A folder that cannot be listed skips its tracks this tick (nothing is
written — an unreadable folder must not look like a removed ``.lrc``). A
missing audio file is flagged and its stored lyrics are kept. Tracks are
read in batches of at most 100, each batch entirely inside
``sidecar.SIDECAR_LOCK`` — classify, read, write, commit — so it never
meets a ``.lrc`` Domovoi wrote a moment ago whose hash is not recorded yet
([O4]). At most two minutes of work per tick; the rest waits for the next.

One INFO line per tick that read something; never a file's lyrics, and
file names at DEBUG at most.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol, Sequence

from domovoi.config import settings
from domovoi.lyrics import SCAN_VERSION, sidecar
from domovoi.lyrics import status as lyrics_state
from domovoi.lyrics import store
from domovoi.lyrics.sidecar import sha256_hex
from domovoi.lyrics.sources import (
    DirRow,
    FileStat,
    artist_title_sidecars,
    parse_sidecar,
    read_embedded,
    read_sidecar_bytes,
    sidecar_candidates,
    stat_file,
)
from domovoi.workers.base import Worker

log = logging.getLogger(__name__)

#: Tracks read (and written) per batch, inside one hold of SIDECAR_LOCK.
BATCH_SIZE = 100
#: Work per tick, in seconds ([K7]).
TICK_BUDGET_SEC = 120.0
#: How often a long folder's stat loop looks at the clock.
_CLOCK_EVERY = 200


class LyricsScanStore(Protocol):
    """The scan's reads and writes (a fake stands in for it in tests)."""

    async def rows(self) -> list[store.ScanRow]: ...

    async def lrc_states(self, track_ids: Sequence[int]) -> dict[int, store.LrcState]: ...

    async def write(self, writes: Sequence[store.LocalWrite], missing: Sequence[int]) -> None: ...


class DbLyricsScanStore:
    """The real store; ``write`` is one transaction per batch."""

    async def rows(self) -> list[store.ScanRow]:
        from domovoi.db.session import session_scope

        async with session_scope() as s:
            return await store.scan_rows(s)

    async def lrc_states(self, track_ids: Sequence[int]) -> dict[int, store.LrcState]:
        from domovoi.db.session import session_scope

        async with session_scope() as s:
            return await store.lrc_states(s, track_ids)

    async def write(self, writes: Sequence[store.LocalWrite], missing: Sequence[int]) -> None:
        from domovoi.db.session import session_scope

        async with session_scope() as s:
            for w in writes:
                await store.write_local(s, w)
            for track_id in missing:
                await store.write_missing(s, track_id)


# ─── Looking (outside the lock) ───────────────────────────────────────────


@dataclass(frozen=True)
class _Seen:
    row: store.ScanRow
    audio: FileStat | None          # None = the audio file is gone
    sidecar_name: str | None
    sidecar: FileStat | None


def _list_names(directory: str) -> list[str] | None:
    """[K3]: the folder's names, or None when it cannot be listed."""
    try:
        with os.scandir(directory) as it:
            return [entry.name for entry in it]
    except OSError:
        return None


def _observe(
    directory: str,
    rows: Sequence[store.ScanRow],
    folder_rows: Sequence[store.ScanRow],
    expired: Callable[[], bool],
) -> tuple[list[_Seen] | None, int, bool]:
    """Blocking: list ``directory`` once and ``stat`` each of ``rows``.
    Returns (what was seen — None when the folder can't be listed — the
    number of rows that could not be looked at, whether it stopped early
    for the budget)."""
    names = _list_names(directory)
    if names is None:
        return None, len(rows), False
    named = artist_title_sidecars(
        names, [DirRow(r.id, Path(r.file_path).name, r.artist, r.title) for r in folder_rows]
    )
    seen: list[_Seen] = []
    unreadable = 0
    for i, r in enumerate(rows):
        if i and i % _CLOCK_EVERY == 0 and expired():
            return seen, unreadable, True
        audio_path = Path(r.file_path)
        try:
            st = os.stat(audio_path)
            audio: FileStat | None = FileStat(int(st.st_mtime_ns), int(st.st_size))
        except (FileNotFoundError, NotADirectoryError):
            audio = None
        except OSError:
            unreadable += 1                 # can't tell: not looked at this tick
            continue
        cands = sidecar_candidates(audio_path.name, names)
        sc_name = cands[0] if cands else named.get(r.id)
        sc_stat = stat_file(Path(directory) / sc_name) if sc_name else None
        if sc_stat is None:
            sc_name = None
        seen.append(_Seen(r, audio, sc_name, sc_stat))
    return seen, unreadable, False


def _change(s: _Seen) -> str | None:
    """[K4]/[K5]: "missing", "read" or None (unchanged)."""
    r = s.row
    if s.audio is None:
        if r.has_row and r.checked and r.file_missing:
            return None                     # still gone: nothing new
        return "missing"
    if r.never_scanned or r.file_missing or r.scan_version < SCAN_VERSION:
        return "read"
    if (r.file_mtime_ns, r.file_size) != (s.audio.mtime_ns, s.audio.size):
        return "read"
    seen_sc = (s.sidecar_name,
               s.sidecar.mtime_ns if s.sidecar else None,
               s.sidecar.size if s.sidecar else None)
    if (r.sidecar_name, r.sidecar_mtime_ns, r.sidecar_size) != seen_sc:
        return "read"
    return None


# ─── Reading (inside the lock) ────────────────────────────────────────────


@dataclass(frozen=True)
class _Read:
    write: store.LocalWrite | None = None
    missing: int | None = None
    unreadable: bool = False
    has_lyrics: bool = False


def _read_one(s: _Seen, state: store.LrcState | None, music_root: Path) -> _Read:
    """Blocking: classify the sidecar ([O1]–[O3]) and read the track's
    local lyrics ([K6])."""
    r = s.row
    directory = Path(r.file_path).parent
    sc_name, sc_stat = s.sidecar_name, s.sidecar
    sc_bytes: bytes | None = None
    sc_error: str | None = None
    if sc_name:
        got = read_sidecar_bytes(directory / sc_name, music_root)
        if got.error == "missing":          # gone since it was looked at
            sc_name, sc_stat = None, None
        else:
            sc_bytes, sc_error = got.data, got.error

    transition: str | None = None
    own = False
    if state is not None and state.state == "written" and state.name:
        own_path = directory / state.name
        if not os.path.lexists(own_path):
            transition = "deleted"                                   # [O3]
        else:
            if sc_name == state.name and sc_error is None:
                data, err = sc_bytes, None
            else:
                got = read_sidecar_bytes(own_path, music_root)
                data, err = got.data, got.error
            if data is not None:
                if sha256_hex(data) == state.sha256:
                    own = sc_name == state.name                      # [O1]
                else:
                    transition = "edited"                            # [O2]
            elif err == "unreadable":
                # No verdict while it can't be read: nothing is written.
                return _Read(unreadable=True)
            else:
                transition = "edited"       # a directory, a symlink out, oversized

    source = detail = None
    lyrics = None
    if sc_name and not own and sc_bytes is not None:
        parsed = parse_sidecar(sc_bytes)
        if parsed.plain is not None and store.fits(parsed):
            source, detail, lyrics = "sidecar", sc_name, parsed
    if source is None:
        emb = read_embedded(r.file_path, track_id=r.id)
        if emb is not None and store.fits(emb.lyrics):
            source, detail, lyrics = "embedded", emb.detail, emb.lyrics
    assert s.audio is not None
    return _Read(
        write=store.LocalWrite(
            track_id=r.id, file_mtime_ns=s.audio.mtime_ns, file_size=s.audio.size,
            source=source, detail=detail, lyrics=lyrics,
            sidecar_name=sc_name,
            sidecar_mtime_ns=sc_stat.mtime_ns if sc_stat else None,
            sidecar_size=sc_stat.size if sc_stat else None,
            lrc_transition=transition,
        ),
        unreadable=sc_error == "unreadable",
        has_lyrics=source is not None,
    )


def _read_batch(
    plans: Sequence[tuple[str, _Seen]], states: dict[int, store.LrcState], music_root: Path,
) -> list[_Read]:
    out: list[_Read] = []
    for kind, s in plans:
        if kind == "missing":
            out.append(_Read(missing=s.row.id))
        else:
            out.append(_read_one(s, states.get(s.row.id), music_root))
    return out


# ─── The worker ───────────────────────────────────────────────────────────


class LyricsScanError(RuntimeError):
    """What a failed tick raises to the runner: the exception TYPE only (a
    database error's text can quote a row's lyrics)."""


def _music_root() -> Path:
    return Path(settings.music_dir).expanduser().resolve(strict=False)


class LyricsScanner(Worker):
    """Reads the library's local lyrics (``.lrc`` sidecars, tags) into
    ``track_lyrics``. See the module docstring."""

    name = "lyrics_scan"
    enabled_setting = None          # always on: it only reads local files
    interval_setting = "lyrics_scan_interval_sec"
    stub_suppressed = True
    requires_online = False

    def __init__(
        self,
        *,
        store: LyricsScanStore | None = None,
        clock: Callable[[], float] = time.monotonic,
        budget_sec: float = TICK_BUDGET_SEC,
        batch_size: int = BATCH_SIZE,
    ) -> None:
        self._store: LyricsScanStore = store or DbLyricsScanStore()
        self._clock = clock
        self._budget = float(budget_sec)
        self._batch_size = max(1, int(batch_size))

    async def tick(self) -> dict[str, int]:
        S = lyrics_state.SCAN
        S.last_tick_at = time.time()
        S.state = "running"
        try:
            counts = await self._tick()
        except Exception as e:  # noqa: BLE001 — reported by TYPE only, then re-raised
            S.state = "error"
            S.last_error = type(e).__name__[:64]
            raise LyricsScanError(f"lyrics scan failed ({type(e).__name__})") from None
        return counts

    async def _tick(self) -> dict[str, int]:
        S = lyrics_state.SCAN
        started = self._clock()
        deadline = started + self._budget

        def expired() -> bool:
            return self._clock() > deadline

        rows = await self._store.rows()
        music_root = await asyncio.to_thread(_music_root)
        folders: dict[str, list[store.ScanRow]] = {}
        for r in rows:
            folders.setdefault(str(Path(r.file_path).parent), []).append(r)

        counts = {"looked_at": 0, "read": 0, "with_lyrics": 0, "unreadable": 0, "missing": 0}
        done_ids: set[int] = set()
        pending: list[tuple[str, _Seen]] = []
        complete = True
        batches = 0

        def out_of_time() -> bool:
            # Every tick reads at least one batch, so a folder too big (or
            # too slow) to look at within the budget still makes progress.
            return batches > 0 and expired()

        for directory, folder_rows in folders.items():
            if expired():
                complete = False
                break
            seen, unreadable, stopped = await asyncio.to_thread(
                _observe, directory, folder_rows, folder_rows, expired,
            )
            counts["unreadable"] += unreadable
            if seen is None:
                log.debug("lyrics scan: a folder could not be listed (%d tracks skipped)",
                          len(folder_rows))
                continue
            counts["looked_at"] += len(seen)
            for s in seen:
                kind = _change(s)
                if kind is not None:
                    pending.append((kind, s))
                if len(pending) >= self._batch_size:
                    if out_of_time():
                        complete = False
                        break
                    await self._run_batch(pending, music_root, counts, done_ids)
                    batches += 1
                    pending = []
            if stopped or not complete:
                complete = False
                break
        if pending and (complete or not out_of_time()):
            await self._run_batch(pending, music_root, counts, done_ids)
            batches += 1
            pending = []
        if pending:
            complete = False

        elapsed = self._clock() - started
        S.tracks = len(rows)
        S.unscanned = sum(1 for r in rows if r.never_scanned and r.id not in done_ids)
        S.last_error = None
        S.read_this_pass += counts["read"] + counts["missing"]
        if complete:
            S.last_pass_at = time.time()
            S.read_last_pass = S.read_this_pass
            S.read_this_pass = 0
            S.state = "done"
        else:
            S.state = "running"
        if counts["read"] or counts["missing"]:
            log.info(
                "lyrics scan: %d files looked at, %d read again, %d with lyrics, %d unreadable (%.1f s)",
                counts["looked_at"], counts["read"] + counts["missing"], counts["with_lyrics"],
                counts["unreadable"], elapsed,
            )
        return counts

    async def _run_batch(
        self, plans: list[tuple[str, _Seen]], music_root: Path,
        counts: dict[str, int], done_ids: set[int],
    ) -> None:
        """[K7]: classify, read, write and COMMIT one batch inside
        SIDECAR_LOCK; the ``lrc_*`` state is read after taking the lock."""
        batch = list(plans)
        async with sidecar.SIDECAR_LOCK:
            states = await self._store.lrc_states([s.row.id for _, s in batch])
            results = await asyncio.to_thread(_read_batch, batch, states, music_root)
            writes = [res.write for res in results if res.write is not None]
            missing = [res.missing for res in results if res.missing is not None]
            if writes or missing:
                await self._store.write(writes, missing)
        for res in results:
            if res.write is not None:
                counts["read"] += 1
                done_ids.add(res.write.track_id)
                if res.has_lyrics:
                    counts["with_lyrics"] += 1
            if res.missing is not None:
                counts["missing"] += 1
                done_ids.add(res.missing)
            if res.unreadable:
                counts["unreadable"] += 1


__all__ = [
    "BATCH_SIZE",
    "DbLyricsScanStore",
    "LyricsScanError",
    "LyricsScanStore",
    "LyricsScanner",
    "TICK_BUDGET_SEC",
]
