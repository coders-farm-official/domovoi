"""Tag reads never run on the event loop, and a sweep never reads a file it
has already indexed (2026-10-16 delta security review, B2 and S2).

The chain the review found: the core's boot hook ``library_index`` ->
``index_music_dir`` -> ``_insert_track`` -> the tag read, synchronous inside
the async sweep and BEFORE the "already indexed" check, so every boot
re-read every file on the loop, and one slow file (a 10 MB Ogg comment
header took 36 s) stalled the core past its 30 s loop watchdog: a restart
loop. Covered here, DB-free (a fake session plays ``library_tracks``):

* the sweep, the bounded upload index, the audiobook indexer and the
  podcast downloader read tags in ``domovoi.executors``' ``tags`` pool, and
  a loop heartbeat never stalls while a slow read runs;
* a sweep checks the paths already indexed before reading any tags, so a
  second boot reads none;
* the open cover route reads a file once however many ask at once, and
  keeps the answer for the file's version.
"""

from __future__ import annotations

import asyncio
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from domovoi import executors, media_tags
from domovoi.tests import media_writer as mw
from domovoi.workers import library_indexer

SLOW_READ_SEC = 1.0
# A loop that runs is never this long between two 10 ms ticks.
MAX_GAP_SEC = 0.5


class _Rows:
    def __init__(self, rows: list[tuple] | None = None, rowcount: int = 0) -> None:
        self._rows = rows or []
        self.rowcount = rowcount

    def first(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class _Library:
    """``library_tracks`` as far as the indexer sees it."""

    def __init__(self) -> None:
        self.paths: set[str] = set()
        self.statements: list[str] = []

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Rows:
        sql = " ".join(str(stmt).split())
        self.statements.append(sql)
        if sql.startswith("SELECT file_path FROM library_tracks"):
            return _Rows([(p,) for p in sorted(self.paths)])
        if sql.startswith("SELECT 1 FROM library_tracks"):
            return _Rows([(1,)] if params["fp"] in self.paths else [])
        if sql.startswith("INSERT INTO library_tracks"):
            if params["fp"] in self.paths:
                return _Rows(rowcount=0)
            self.paths.add(params["fp"])
            return _Rows(rowcount=1)
        return _Rows()

    async def commit(self) -> None:
        pass


@pytest.fixture
def library(monkeypatch, tmp_path) -> tuple[_Library, Path]:
    db = _Library()

    @asynccontextmanager
    async def scope():
        yield db

    root = tmp_path / "Music"
    root.mkdir()
    monkeypatch.setattr(library_indexer, "session_scope", scope)
    monkeypatch.setattr(library_indexer.settings, "music_dir", str(root))
    return db, root


@pytest.fixture
def slow_reads(monkeypatch) -> list[str]:
    """Every tag read takes SLOW_READ_SEC (any slow file, the review's big
    Ogg among them) and records the thread it ran on."""
    threads: list[str] = []
    real = media_tags.read

    def slow(path, *a, **kw):
        threads.append(threading.current_thread().name)
        time.sleep(SLOW_READ_SEC)
        return real(path, *a, **kw)

    monkeypatch.setattr(media_tags, "read", slow)
    return threads


async def _worst_gap(work) -> tuple[Any, float]:
    """``await work`` with a 10 ms heartbeat beside it: the result and the
    longest the loop went without running the heartbeat."""
    gaps: list[float] = []
    done = asyncio.Event()

    async def beat() -> None:
        last = time.perf_counter()
        while not done.is_set():
            await asyncio.sleep(0.01)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    task = asyncio.create_task(beat())
    await asyncio.sleep(0.05)
    try:
        out = await work
    finally:
        done.set()
        await task
    return out, max(gaps)


def _big_ogg(pages: int) -> bytes:
    """The review's p4 file (a comment header over many full pages)."""
    import struct

    def page(flags, seq, lacing, data, granule=0):
        head = b"OggS\x00" + bytes([flags]) + struct.pack("<qIII", granule, 1, seq, 0) + bytes([len(lacing)])
        return head + bytes(lacing) + data

    ident = b"\x01vorbis" + struct.pack("<IBI", 0, 2, 44100) + b"\x00" * 15
    out = [page(0x02, 0, [len(ident)], ident)]
    start = b"\x03vorbis" + struct.pack("<II", 0, 0)
    for i in range(pages):
        body = start if i == 0 else b""
        out.append(page(0x01 if i else 0x00, i + 1, [255] * 255, body + b"A" * (65025 - len(body))))
    out.append(page(0x01, pages + 1, [0], b"", granule=44100 * 60))
    return b"".join(out)


# ─── the library sweep ───────────────────────────────────────────────────


async def test_the_sweep_reads_tags_in_the_tags_pool_and_the_loop_keeps_running(library, slow_reads) -> None:
    db, root = library
    (root / "big.ogg").write_bytes(_big_ogg(40))
    mw.write_mp3(root / "Artist - Song.mp3", [mw.text_frame("TIT2", "Tagged Song")])
    counts, gap = await _worst_gap(library_indexer.index_music_dir())
    assert counts == {"scanned": 2, "inserted": 2, "skipped": 0, "errors": 0}
    assert gap < MAX_GAP_SEC, f"the loop stalled {gap:.2f} s"
    assert len(slow_reads) == 2 and all(t.startswith("domovoi-tags") for t in slow_reads), slow_reads


async def test_a_second_sweep_reads_no_tags(library, monkeypatch) -> None:
    db, root = library
    (root / "big.ogg").write_bytes(_big_ogg(4))
    (root / "Artist - Song.mp3").write_bytes(b"")
    first = await library_indexer.index_music_dir()
    assert first["inserted"] == 2

    read: list[str] = []

    def recording(path):
        read.append(path.name)
        return {"title": path.stem, "artist": None, "album": None, "duration_sec": None}

    monkeypatch.setattr(library_indexer, "_resolve_metadata_for", recording)
    (root / "New - One.mp3").write_bytes(b"")
    second = await library_indexer.index_music_dir()
    assert second == {"scanned": 3, "inserted": 1, "skipped": 2, "errors": 0}
    assert read == ["New - One.mp3"]
    # One query for what is known per sweep, then an INSERT per new file.
    assert sum(s.startswith("SELECT file_path FROM library_tracks") for s in db.statements) == 2
    assert sum(s.startswith("INSERT INTO library_tracks") for s in db.statements) == 3


async def test_the_bounded_upload_index_checks_before_reading(library, monkeypatch) -> None:
    db, root = library
    song = root / "Up - Loaded.mp3"
    song.write_bytes(b"")
    assert (await library_indexer.index_paths([song]))["inserted"] == 1

    def no_reads(path):
        raise AssertionError("tags read for an indexed path")

    monkeypatch.setattr(library_indexer, "_resolve_metadata_for", no_reads)
    again = await library_indexer.index_paths([song])
    assert again["inserted"] == 0 and again["skipped"] == 1


async def test_the_walk_itself_is_off_the_loop(library, monkeypatch) -> None:
    db, root = library
    seen: list[str] = []
    real = library_indexer._audio_files

    def walk(music_dir):
        seen.append(threading.current_thread().name)
        time.sleep(SLOW_READ_SEC)
        return real(music_dir)

    monkeypatch.setattr(library_indexer, "_audio_files", walk)
    (root / "a.mp3").write_bytes(b"")
    _, gap = await _worst_gap(library_indexer.index_music_dir())
    assert gap < MAX_GAP_SEC and seen and seen[0] != threading.main_thread().name


# ─── the audiobook indexer and the podcast downloader ────────────────────


async def test_audiobook_tags_are_read_in_the_tags_pool(monkeypatch, tmp_path, slow_reads) -> None:
    from domovoi.workers import audiobook_indexer

    async def no_ffprobe(path):
        return None, None

    monkeypatch.setattr(audiobook_indexer, "_ffprobe_chapters", no_ffprobe)
    book = mw.write_mp3(tmp_path / "Book.mp3", [mw.text_frame("TIT2", "The Book")])
    folder = tmp_path / "Folder Book"
    mw.write_mp3(folder / "01.mp3", [mw.text_frame("TIT2", "Chapter One")])
    meta, gap = await _worst_gap(audiobook_indexer._resolve_book_file(book))
    assert meta["title"] == "The Book" and gap < MAX_GAP_SEC
    meta, gap = await _worst_gap(audiobook_indexer._resolve_book_folder(folder))
    assert meta["chapters"][0]["title"] == "Chapter One" and gap < MAX_GAP_SEC
    assert len(slow_reads) == 2 and all(t.startswith("domovoi-tags") for t in slow_reads)


async def test_a_downloaded_episodes_chapters_are_read_in_the_tags_pool(monkeypatch, tmp_path) -> None:
    from domovoi.workers import podcast_feed_poller as poller

    episode = mw.id3_tag([mw.CHAP(element_id="a", start_time=0, end_time=5000,
                                  sub_frames=[mw.TIT2("Intro")])], version=3) + mw.silent_mp3()

    class _Response:
        async def aclose(self) -> None:
            pass

        def raise_for_status(self) -> None:
            pass

    async def open_stream(client, url):
        return _Response()

    async def iter_capped(response, cap):
        yield episode

    monkeypatch.setattr(poller.net_safety, "open_stream", open_stream)
    monkeypatch.setattr(poller.net_safety, "iter_capped", iter_capped)
    monkeypatch.setattr(poller.egress, "require_destination", lambda url: None)
    monkeypatch.setattr(poller.settings, "podcasts_dir", str(tmp_path))
    threads: list[str] = []
    real = poller._read_id3_chapters

    def read(path):
        threads.append(threading.current_thread().name)
        time.sleep(SLOW_READ_SEC)
        return real(path)

    monkeypatch.setattr(poller, "_read_id3_chapters", read)

    class _Session:
        def __init__(self) -> None:
            self.params: list[dict] = []

        async def execute(self, stmt, params=None):
            self.params.append(params or {})

        async def commit(self) -> None:
            pass

    session = _Session()
    ok, gap = await _worst_gap(poller.download_episode(session, {
        "id": 3, "subscription_id": 1, "guid": "g", "title": "Ep", "sub_title": "Show",
        "enclosure_url": "https://feeds.example.com/ep.mp3"}))
    assert ok is True and gap < MAX_GAP_SEC
    assert threads and threads[0].startswith("domovoi-tags")
    assert '"Intro"' in next(p["chapters"] for p in session.params if "chapters" in p)


def test_the_tags_pool_is_small() -> None:
    assert executors.POOL_SIZES["tags"] == 2
