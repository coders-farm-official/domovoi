"""The local lyrics scan: a ``.lrc`` beside each song, and the lyrics
inside the file (contract §5.3, [K1]–[K10]; §5.4 for whose ``.lrc`` it is).

Always on — it only reads the household's own files, and nothing leaves
the house for them. Every few minutes (``lyrics_scan_interval_sec``) it
looks at every library track: each folder is listed once, each audio file
and its sidecar ``stat``-ed, and a track is READ AGAIN only when it has
never been read, the reading rules changed (``SCAN_VERSION``), the audio
file changed (size, mtime — on POSIX also its ctime, which a chmod or a
chown moves), or its ``.lrc`` changed, appeared or disappeared. Unchanged
tracks are not written.

Reading a track: the household's ``.lrc`` wins (whatever it holds) — the
same-stem one, else an "Artist - Title" one; a ``.lrc`` Domovoi wrote
itself is a copy of LRCLIB's lyrics: it is never read as the household's
(the lyrics then come from an "Artist - Title" file of the household's,
the tags, or nowhere) and it never hides a file of the household's; a file
Domovoi wrote that has since been edited or deleted turns the row to
``edited`` / ``deleted`` — the household's now, never written again.

**Safety.** Reading only: the scan never writes, renames or deletes a file.
A folder that cannot be listed skips its tracks this tick (nothing is
written — an unreadable folder must not look like a removed ``.lrc``), and
a file that cannot be read gives no verdict either: a song read before
keeps what was stored, and is tried again next tick. A missing audio file
is flagged and its stored lyrics are kept. Tracks are read in batches of
at most 100, each batch entirely inside ``sidecar.SIDECAR_LOCK`` —
classify, read, write, commit — so it never meets a ``.lrc`` Domovoi wrote
a moment ago whose hash is not recorded yet ([O4]). At most two minutes of
work per tick; the rest waits for the next.

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
from domovoi.lyrics import sidecar
from domovoi.lyrics import status as lyrics_state
from domovoi.lyrics import store
from domovoi.lyrics.sidecar import sha256_hex, song_renamed
from domovoi.lyrics.sources import (
    DirRow,
    FileStat,
    artist_title_matches,
    can_read,
    file_stat,
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
    """One song as the scan looked at it (outside the lock).

    ``stem_name``: its best same-stem ``.lrc`` ([F-S1]/[F-S2]);
    ``alt_name``: the one "Artist - Title" ``.lrc`` that matches it
    ([F-S3]), whether or not it has a same-stem one. ``sidecar_name`` is
    the one in use — the household's same-stem file; else (there is none,
    or it is Domovoi's own copy of LRCLIB's lyrics) the "Artist - Title"
    file; else Domovoi's own — and what [K5] compares with the row."""

    row: store.ScanRow
    audio: FileStat | None          # None = the audio file is gone
    sidecar_name: str | None
    sidecar: FileStat | None
    stem_name: str | None = None
    stem: FileStat | None = None
    alt_name: str | None = None
    alt: FileStat | None = None


def _list_names(directory: str) -> list[str] | None:
    """[K3]: the folder's names, or None when it cannot be listed."""
    try:
        with os.scandir(directory) as it:
            return [entry.name for entry in it]
    except OSError:
        return None


def _in_use(stem_name: str | None, alt_name: str | None, stem_is_own: bool) -> str | None:
    """Which ``.lrc`` a song's lyrics come from ([F-S2], [F-S3], [O1]): the
    household's same-stem file; else the "Artist - Title" file — also when
    the only same-stem file is Domovoi's own, which never hides one of the
    household's (amended 2026-10-06); else Domovoi's own, kept as the
    song's sidecar but never read as its lyrics."""
    if stem_name is None:
        return alt_name
    if stem_is_own and alt_name is not None:
        return alt_name
    return stem_name


def _looks_own(r: store.ScanRow, directory: Path, stem_name: str, music_root: Path) -> bool:
    """Whether ``stem_name`` is Domovoi's own file by the row as read
    before the lock ([O1]: the name it wrote, the bytes it wrote). Asked
    only for a song that also has an "Artist - Title" match, so this read
    happens for a handful of songs at most."""
    if r.lrc_state != "written" or not r.lrc_name or r.lrc_name != stem_name or not r.lrc_sha256:
        return False
    data = read_sidecar_bytes(directory / stem_name, music_root).data
    return data is not None and sha256_hex(data) == r.lrc_sha256


def _observe(
    directory: str,
    rows: Sequence[store.ScanRow],
    folder_rows: Sequence[store.ScanRow],
    expired: Callable[[], bool],
    music_root: Path,
) -> tuple[list[_Seen] | None, int, bool]:
    """Blocking: list ``directory`` once and ``stat`` each of ``rows``.
    Returns (what was seen — None when the folder can't be listed — the
    number of rows that could not be looked at, whether it stopped early
    for the budget)."""
    names = _list_names(directory)
    if names is None:
        return None, len(rows), False
    named = artist_title_matches(
        names, [DirRow(r.id, Path(r.file_path).name, r.artist, r.title) for r in folder_rows]
    )
    folder = Path(directory)
    seen: list[_Seen] = []
    unreadable = 0
    for i, r in enumerate(rows):
        if i and i % _CLOCK_EVERY == 0 and expired():
            return seen, unreadable, True
        audio_path = Path(r.file_path)
        try:
            audio: FileStat | None = file_stat(os.stat(audio_path))
        except (FileNotFoundError, NotADirectoryError):
            audio = None
        except OSError:
            unreadable += 1                 # can't tell: not looked at this tick
            continue
        cands = sidecar_candidates(audio_path.name, names)
        stem_name = cands[0] if cands else None
        stem = stat_file(folder / stem_name) if stem_name else None
        if stem is None:
            stem_name = None
        alt_name = named.get(r.id)
        alt = stat_file(folder / alt_name) if alt_name else None
        if alt is None:
            alt_name = None
        own = (stem_name is not None and alt_name is not None
               and _looks_own(r, folder, stem_name, music_root))
        use = _in_use(stem_name, alt_name, own)
        if use is None:
            use_stat = None
        elif use == alt_name:
            use_stat = alt
        else:
            use_stat = stem
        seen.append(_Seen(r, audio, use, use_stat, stem_name, stem, alt_name, alt))
    return seen, unreadable, False


def _change(s: _Seen) -> str | None:
    """[K4]/[K5]: "missing", "read" or None (unchanged)."""
    r = s.row
    if s.audio is None:
        if r.has_row and r.checked and r.file_missing:
            return None                     # still gone: nothing new
        return "missing"
    if not r.read_before:
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
    local lyrics ([K6]).

    A file that cannot be read gives no verdict: for a song read before,
    nothing is written (what was stored stands, and the next tick tries
    again); for one never read, what could be read is written with that
    file's stat left empty, so the next tick reads the song again."""
    r = s.row
    directory = Path(r.file_path).parent
    stem_name, stem_stat = s.stem_name, s.stem
    stem_bytes: bytes | None = None
    stem_error: str | None = None
    if stem_name:
        got = read_sidecar_bytes(directory / stem_name, music_root)
        if got.error == "missing":          # gone since it was looked at
            stem_name, stem_stat = None, None
        else:
            stem_bytes, stem_error = got.data, got.error

    transition: str | None = None
    own = False
    # A .lrc Domovoi wrote under the song's OLD file name (the file was
    # renamed since) is no longer this song's: nothing to classify.
    if (state is not None and state.state == "written" and state.name
            and not song_renamed(state.name, r.file_path)):
        own_path = directory / state.name
        if not os.path.lexists(own_path):
            transition = "deleted"                                   # [O3]
        else:
            if stem_name == state.name and stem_error is None:
                data, err = stem_bytes, None
            else:
                got = read_sidecar_bytes(own_path, music_root)
                data, err = got.data, got.error
            if data is not None:
                if sha256_hex(data) == state.sha256:
                    own = stem_name == state.name                    # [O1]
                else:
                    transition = "edited"                            # [O2]
            elif err == "unreadable":
                # No verdict while it can't be read: nothing is written.
                return _Read(unreadable=True)
            else:
                transition = "edited"       # a directory, a symlink out, oversized

    # The .lrc in use (_in_use): the household's same-stem file, else the
    # "Artist - Title" one, else Domovoi's own (never read as lyrics).
    use_name = _in_use(stem_name, s.alt_name, own)
    use_stat: FileStat | None = None
    use_bytes: bytes | None = None
    use_error: str | None = None
    theirs = False
    if use_name is not None and use_name == s.alt_name:
        got = read_sidecar_bytes(directory / use_name, music_root)
        if got.error == "missing":          # gone since it was looked at
            use_name = stem_name if own else None
            use_stat = stem_stat if own else None
        else:
            use_stat, use_bytes, use_error, theirs = s.alt, got.data, got.error, True
    elif use_name is not None:
        use_stat, use_bytes, use_error, theirs = stem_stat, stem_bytes, stem_error, not own

    source = detail = None
    lyrics = None
    sidecar_unreadable = theirs and use_error == "unreadable"
    if theirs and use_bytes is not None:
        parsed = parse_sidecar(use_bytes)
        if parsed.plain is not None and store.fits(parsed):
            source, detail, lyrics = "sidecar", use_name, parsed
    if sidecar_unreadable and r.read_before:
        return _Read(unreadable=True)
    audio_unreadable = False
    if source is None:
        if not can_read(r.file_path):
            audio_unreadable = True
            if r.read_before:
                return _Read(unreadable=True)
        else:
            emb = read_embedded(r.file_path, track_id=r.id)
            if emb is not None and store.fits(emb.lyrics):
                source, detail, lyrics = "embedded", emb.detail, emb.lyrics
    assert s.audio is not None
    return _Read(
        write=store.LocalWrite(
            track_id=r.id,
            file_mtime_ns=None if audio_unreadable else s.audio.mtime_ns,
            file_size=None if audio_unreadable else s.audio.size,
            source=source, detail=detail, lyrics=lyrics,
            sidecar_name=use_name,
            sidecar_mtime_ns=use_stat.mtime_ns if use_stat and not sidecar_unreadable else None,
            sidecar_size=use_stat.size if use_stat and not sidecar_unreadable else None,
            lrc_transition=transition,
        ),
        unreadable=sidecar_unreadable or audio_unreadable,
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
                _observe, directory, folder_rows, folder_rows, expired, music_root,
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
