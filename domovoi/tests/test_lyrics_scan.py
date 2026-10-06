"""The local lyrics scan (domovoi/workers/lyrics_scan.py, contract §5.3
[K1]–[K10], §5.4 [O1]–[O4]).

The first half is DB-free (a fake store that remembers what the scan
wrote, so a second tick sees its own work); the second half runs the real
upserts on the test lane (``requires_db``), including the scan and the
``.lrc`` writer running at the same time. Files are generated in tmp dirs;
every lyric line is INVENTED ([C2]).
"""

from __future__ import annotations

import asyncio
import dataclasses
import importlib.util
import itertools
import json
import logging
import os
from collections import Counter
from pathlib import Path

import pytest
from sqlalchemy import text

from domovoi.config import settings
from domovoi.lyrics import SCAN_VERSION, sidecar, store
from domovoi.lyrics import status as lyrics_state
from domovoi.lyrics.lrc import format_lrc
from domovoi.lyrics.sidecar import sha256_hex
from domovoi.tests.conftest import requires_db
from domovoi.tests.test_library_cover_art import silent_mp3
from domovoi.workers.lyrics_scan import LyricsScanError, LyricsScanner

needs_mutagen = pytest.mark.skipif(
    importlib.util.find_spec("mutagen") is None,
    reason="mutagen (the [real-clients] extra) writes the tagged fixtures",
)

L1 = "the lantern hums beside the river door"
L2 = "and every copper kettle sings at dawn"
L3 = "we carried paper boats along the hall"
WORDS = ("lantern", "kettle", "paper boats", "river door")
OWNER_LRC = f"[00:01.00]{L1}\n[00:02.00]{L2}\n".encode()


def tagged_mp3(path: Path, lyrics: str | None = None) -> Path:
    """A silent MP3, with an English USLT frame when ``lyrics`` is given."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(silent_mp3())
    if lyrics is not None and importlib.util.find_spec("mutagen") is not None:
        from mutagen.id3 import ID3, USLT

        tags = ID3()
        tags.add(USLT(encoding=3, lang="eng", desc="", text=lyrics))
        tags.save(str(path))
    return path


def domovoi_lrc() -> bytes:
    return format_lrc([(1000, L3)], title="Lantern Song", artist="The Example Band", album=None,
                      duration_sec=205, version="1.0.0").encode()


class FakeScanStore:
    """Rows in memory; ``write`` updates them as the real upsert would."""

    def __init__(self) -> None:
        self.by_id: dict[int, store.ScanRow] = {}
        self.local: dict[int, store.LocalWrite] = {}
        self.missing: list[int] = []
        self.states: dict[int, store.LrcState] = {}
        self.write_calls = 0
        self.transitions: list[tuple[int, str]] = []

    def add(self, track_id: int, path: Path, title="Lantern Song", artist="The Example Band") -> None:
        self.by_id[track_id] = store.ScanRow(track_id, str(path), title, artist, False, False, 0,
                                            None, None, False, None, None, None)

    async def rows(self):
        return sorted(self.by_id.values(), key=lambda r: (not r.never_scanned, r.id))

    async def lrc_states(self, ids):
        return {i: self.states[i] for i in ids if i in self.states}

    async def write(self, writes, missing):
        self.write_calls += 1
        for w in writes:
            self.local[w.track_id] = w
            r = self.by_id[w.track_id]
            self.by_id[w.track_id] = dataclasses.replace(
                r, has_row=True, checked=True, scan_version=SCAN_VERSION,
                file_mtime_ns=w.file_mtime_ns, file_size=w.file_size, file_missing=False,
                sidecar_name=w.sidecar_name, sidecar_mtime_ns=w.sidecar_mtime_ns,
                sidecar_size=w.sidecar_size)
            if w.lrc_transition:
                self.transitions.append((w.track_id, w.lrc_transition))
                st = self.states.get(w.track_id)
                if st is not None and st.state == "written":
                    self.states[w.track_id] = store.LrcState(w.lrc_transition, st.name, st.sha256)
        for tid in missing:
            self.missing.append(tid)
            self.by_id[tid] = dataclasses.replace(self.by_id[tid], has_row=True, checked=True,
                                                 file_missing=True)


@pytest.fixture(autouse=True)
def _fresh_status():
    lyrics_state.reset_for_tests()
    yield
    lyrics_state.reset_for_tests()


@pytest.fixture
def music(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "Music"
    root.mkdir()
    monkeypatch.setattr(settings, "music_dir", str(root))
    return root


def run(coro):
    return asyncio.run(coro)


def snapshot(root: Path) -> dict[str, tuple[int, int]]:
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in sorted(root.rglob("*")) if p.is_file()}


# ═══ DB-free ════════════════════════════════════════════════════════════


@needs_mutagen
def test_first_sight_reads_the_sidecar_then_the_tags(music, caplog) -> None:
    s = FakeScanStore()
    a = tagged_mp3(music / "a" / "Lantern Song.mp3", lyrics=L3)
    (music / "a" / "Lantern Song.lrc").write_bytes(OWNER_LRC)
    b = tagged_mp3(music / "b" / "Glass Harbor.mp3", lyrics=f"{L2}\n{L3}")
    c = tagged_mp3(music / "b" / "Velvet Kites.mp3")
    for i, p in enumerate((a, b, c), 1):
        s.add(i, p)
    with caplog.at_level(logging.DEBUG):
        counts = run(LyricsScanner(store=s).tick())
    assert counts["read"] == 3 and counts["with_lyrics"] == 2
    wa, wb, wc = s.local[1], s.local[2], s.local[3]
    assert (wa.source, wa.detail, wa.sidecar_name) == ("sidecar", "Lantern Song.lrc", "Lantern Song.lrc")
    assert wa.lyrics.synced == ((1000, L1), (2000, L2))            # the sidecar beats the tags
    assert (wb.source, wb.detail, wb.lyrics.plain, wb.sidecar_name) == ("embedded", "USLT", f"{L2}\n{L3}", None)
    assert (wc.source, wc.lyrics) == (None, None)
    assert wa.file_size == a.stat().st_size and wa.file_mtime_ns == a.stat().st_mtime_ns
    S = lyrics_state.SCAN
    assert (S.state, S.tracks, S.unscanned, S.read_last_pass) == ("done", 3, 0, 3)
    assert S.last_pass_at is not None and S.last_error is None
    info = [r.getMessage() for r in caplog.records if r.levelno >= logging.INFO]
    assert len(info) == 1
    assert info[0].startswith("lyrics scan: 3 files looked at, 3 read again, 2 with lyrics, 0 unreadable (")
    assert not any(w in caplog.text for w in WORDS)


def test_unchanged_files_are_not_written_again(music) -> None:
    s = FakeScanStore()
    s.add(1, tagged_mp3(music / "Lantern Song.mp3"))
    run(LyricsScanner(store=s).tick())
    assert s.write_calls == 1
    counts = run(LyricsScanner(store=s).tick())
    assert s.write_calls == 1 and counts["read"] == 0 and counts["looked_at"] == 1
    assert lyrics_state.SCAN.read_last_pass == 0


def test_changes_to_the_audio_file_or_its_sidecar_are_read_again(music) -> None:
    s = FakeScanStore()
    audio = tagged_mp3(music / "Lantern Song.mp3")
    s.add(1, audio)
    scanner = LyricsScanner(store=s)
    run(scanner.tick())
    lrc = music / "Lantern Song.lrc"
    lrc.write_bytes(OWNER_LRC)                                     # appears
    assert run(scanner.tick())["read"] == 1
    assert s.local[1].source == "sidecar"
    lrc.write_bytes(OWNER_LRC + f"[00:03.00]{L3}\n".encode())     # changes (size)
    assert run(scanner.tick())["read"] == 1
    assert s.local[1].lyrics.synced[-1] == (3000, L3)
    st = lrc.stat()
    os.utime(lrc, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))   # changes (mtime)
    assert run(scanner.tick())["read"] == 1
    lrc.unlink()                                                   # disappears
    assert run(scanner.tick())["read"] == 1
    assert s.local[1].source is None and s.local[1].sidecar_name is None
    with audio.open("ab") as f:                                    # the audio file changes
        f.write(b"\x00" * 10)
    assert run(scanner.tick())["read"] == 1
    assert run(scanner.tick())["read"] == 0


def test_a_new_scan_version_reads_everything_again(music) -> None:
    s = FakeScanStore()
    s.add(1, tagged_mp3(music / "Lantern Song.mp3"))
    run(LyricsScanner(store=s).tick())
    s.by_id[1] = dataclasses.replace(s.by_id[1], scan_version=SCAN_VERSION - 1)
    assert run(LyricsScanner(store=s).tick())["read"] == 1


@needs_mutagen
def test_domovois_own_file_is_never_read_as_the_households(music) -> None:
    s = FakeScanStore()
    audio = tagged_mp3(music / "Lantern Song.mp3", lyrics=L2)
    own = domovoi_lrc()
    (music / "Lantern Song.lrc").write_bytes(own)
    s.add(1, audio)
    s.states[1] = store.LrcState("written", "Lantern Song.lrc", sha256_hex(own))
    run(LyricsScanner(store=s).tick())
    w = s.local[1]
    assert (w.source, w.lyrics.plain, w.sidecar_name, w.lrc_transition) == (
        "embedded", L2, "Lantern Song.lrc", None)


def test_an_edited_domovoi_file_becomes_the_households(music) -> None:
    s = FakeScanStore()
    audio = tagged_mp3(music / "Lantern Song.mp3")
    own = domovoi_lrc()
    lrc = music / "Lantern Song.lrc"
    lrc.write_bytes(own + f"[00:05.00]{L1}\n".encode())          # edited by the household
    s.add(1, audio)
    s.states[1] = store.LrcState("written", "Lantern Song.lrc", sha256_hex(own))
    run(LyricsScanner(store=s).tick())
    w = s.local[1]
    assert w.lrc_transition == "edited" and w.source == "sidecar"
    assert w.lyrics.synced[-1] == (5000, L1)
    assert s.states[1].state == "edited"


def test_a_deleted_domovoi_file_is_noted(music) -> None:
    s = FakeScanStore()
    s.add(1, tagged_mp3(music / "Lantern Song.mp3"))
    s.states[1] = store.LrcState("written", "Lantern Song.lrc", "0" * 64)
    run(LyricsScanner(store=s).tick())
    assert s.local[1].lrc_transition == "deleted" and s.local[1].source is None


def test_a_missing_audio_file_is_flagged_once(music) -> None:
    s = FakeScanStore()
    audio = tagged_mp3(music / "Lantern Song.mp3")
    s.add(1, audio)
    scanner = LyricsScanner(store=s)
    run(scanner.tick())
    audio.unlink()
    assert run(scanner.tick())["missing"] == 1 and s.missing == [1]
    assert run(scanner.tick())["missing"] == 0 and s.missing == [1]      # still gone: unchanged
    tagged_mp3(audio)                                                     # back
    assert run(scanner.tick())["read"] == 1


def test_an_unlistable_folder_writes_nothing(music, tmp_path) -> None:
    s = FakeScanStore()
    s.add(1, tmp_path / "unmounted share" / "Lantern Song.mp3")
    counts = run(LyricsScanner(store=s).tick())
    assert s.write_calls == 0 and s.missing == [] and counts["unreadable"] == 1
    assert lyrics_state.SCAN.unscanned == 1


def test_an_artist_title_lrc_is_used_when_unambiguous(music) -> None:
    s = FakeScanStore()
    s.add(1, tagged_mp3(music / "01.mp3"), "Lantern Song", "The Example Band")
    s.add(2, tagged_mp3(music / "02.mp3"), "Glass Harbor", "The Example Band")
    (music / "The Example Band - Lantern Song.lrc").write_bytes(OWNER_LRC)
    run(LyricsScanner(store=s).tick())
    assert s.local[1].source == "sidecar"
    assert s.local[1].sidecar_name == "The Example Band - Lantern Song.lrc"
    assert s.local[2].source is None


def test_lyrics_too_big_to_store_are_skipped(music) -> None:
    s = FakeScanStore()
    s.add(1, tagged_mp3(music / "Lantern Song.mp3"))
    (music / "Lantern Song.lrc").write_bytes(("\n".join(f"{L1} {i}" for i in range(8000))).encode())
    run(LyricsScanner(store=s).tick())
    w = s.local[1]
    assert w.source is None and w.sidecar_name == "Lantern Song.lrc"   # still the sidecar


def test_the_scan_never_changes_a_file(music) -> None:
    s = FakeScanStore()
    for i, name in enumerate(("Lantern Song", "Glass Harbor", "Velvet Kites"), 1):
        s.add(i, tagged_mp3(music / f"{name}.mp3", lyrics=L1))
    (music / "Lantern Song.lrc").write_bytes(OWNER_LRC)
    (music / "Glass Harbor.lrc").write_bytes(domovoi_lrc())
    s.states[2] = store.LrcState("written", "Glass Harbor.lrc", "f" * 64)   # edited → theirs
    before = snapshot(music)
    run(LyricsScanner(store=s).tick())
    run(LyricsScanner(store=s).tick())
    assert snapshot(music) == before


def test_the_budget_stops_a_tick_and_the_next_carries_on(music) -> None:
    now = [0.0]

    class Slow(FakeScanStore):                  # every batch written costs 10 s
        async def write(self, writes, missing):
            await super().write(writes, missing)
            now[0] += 10.0

    s = Slow()
    for i in range(1, 6):
        s.add(i, tagged_mp3(music / f"album {i}" / "Lantern Song.mp3"))

    def scan() -> dict:
        return run(LyricsScanner(store=s, clock=lambda: now[0], budget_sec=25.0, batch_size=1).tick())

    first = scan()
    assert first["read"] == 3                   # 0, 10, 20 s: the fourth would start past 25 s
    S = lyrics_state.SCAN
    assert S.state == "running" and S.last_pass_at is None and S.unscanned == 2
    second = scan()                             # the two never read come first
    assert second["read"] == 2
    assert S.state == "done" and S.unscanned == 0 and S.read_last_pass == 5
    assert scan()["read"] == 0 and S.read_last_pass == 0


def test_a_folder_too_slow_to_look_at_in_one_tick_still_makes_progress(music) -> None:
    """Looking at one big folder uses up the whole budget: every tick
    still reads one batch (the never-read songs first), so it finishes."""
    s = FakeScanStore()
    for i in range(1, 251):
        s.add(i, tagged_mp3(music / "everything" / f"Song {i:03d}.mp3"))

    def scan() -> dict:
        times = iter([0.0, 0.0] + [1000.0] * 10_000)    # past the deadline from the first look
        return run(LyricsScanner(store=s, clock=lambda: next(times), batch_size=100).tick())

    assert scan()["read"] == 100
    assert scan()["read"] == 100
    assert lyrics_state.SCAN.unscanned == 50 and lyrics_state.SCAN.state == "running"


def test_a_failing_tick_reports_the_type_only(music) -> None:
    class Broken(FakeScanStore):
        async def rows(self):
            raise RuntimeError(f"database said: {L1}")

    with pytest.raises(LyricsScanError) as e:
        run(LyricsScanner(store=Broken()).tick())
    assert "RuntimeError" in str(e.value) and "lantern" not in str(e.value)
    assert lyrics_state.SCAN.state == "error" and lyrics_state.SCAN.last_error == "RuntimeError"


def test_the_batch_holds_the_sidecar_lock(music) -> None:
    held: list[bool] = []

    class Watching(FakeScanStore):
        async def lrc_states(self, ids):
            held.append(sidecar.SIDECAR_LOCK.locked())
            return await super().lrc_states(ids)

        async def write(self, writes, missing):
            held.append(sidecar.SIDECAR_LOCK.locked())
            await super().write(writes, missing)

    s = Watching()
    s.add(1, tagged_mp3(music / "Lantern Song.mp3"))
    run(LyricsScanner(store=s).tick())
    assert held == [True, True]
    assert not sidecar.SIDECAR_LOCK.locked()


# ═══ The real upserts (requires_db) ═════════════════════════════════════


_PATHS = itertools.count()


@pytest.fixture
async def v021(db_session):
    from domovoi.tests.lyrics_testkit import apply_v021, clear_lyrics

    await apply_v021()
    yield
    await clear_lyrics()


async def _seed(path: Path, title: str = "Lantern Song", artist: str = "The Example Band",
                album: str | None = "Glass Harbor", duration: int | None = 205) -> int:
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        return (await s.execute(text(
            "INSERT INTO library_tracks (file_path, title, artist, album, duration_sec) "
            "VALUES (:p, :t, :a, :al, :d) RETURNING id"
        ), {"p": str(path), "t": title, "a": artist, "al": album, "d": duration})).scalar_one()


async def _rows(sql: str, **params) -> list[tuple]:
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        return [tuple(r) for r in (await s.execute(text(sql), params)).all()]


@requires_db
@needs_mutagen
async def test_rows_are_created_on_first_sight_and_left_alone_when_unchanged(v021, music) -> None:
    a = await _seed(tagged_mp3(music / "Lantern Song.mp3", lyrics=f"{L1}\n{L2}"))
    (music / "Glass Harbor.lrc").write_bytes(OWNER_LRC)
    b = await _seed(tagged_mp3(music / "Glass Harbor.mp3"), title="Glass Harbor")
    c = await _seed(tagged_mp3(music / "Velvet Kites.mp3"), title="Velvet Kites")
    counts = await LyricsScanner().tick()
    assert counts["read"] == 3
    rows = {r[0]: r[1:] for r in await _rows(
        "SELECT track_id, local_source, local_detail, local_plain, local_synced IS NOT NULL, "
        "sidecar_name, scan_version, file_missing, local_checked_at IS NOT NULL, local_offset_ms "
        "FROM track_lyrics")}
    assert rows[a] == ("embedded", "USLT", f"{L1}\n{L2}", False, None, SCAN_VERSION, False, True, None)
    assert rows[b] == ("sidecar", "Glass Harbor.lrc", f"{L1}\n{L2}", True, "Glass Harbor.lrc",
                       SCAN_VERSION, False, True, 0)
    assert rows[c] == (None, None, None, False, None, SCAN_VERSION, False, True, None)
    shown = await _rows("SELECT track_id, source, has_synced FROM track_lyrics_shown ORDER BY track_id")
    assert shown == [(a, "embedded", False), (b, "sidecar", True), (c, None, False)]
    stamps = await _rows("SELECT track_id, updated_at FROM track_lyrics ORDER BY track_id")
    again = await LyricsScanner().tick()
    assert again["read"] == 0
    assert await _rows("SELECT track_id, updated_at FROM track_lyrics ORDER BY track_id") == stamps


@requires_db
@needs_mutagen
async def test_a_missing_file_keeps_its_lyrics(v021, music) -> None:
    audio = tagged_mp3(music / "Lantern Song.mp3", lyrics=L1)
    tid = await _seed(audio)
    await LyricsScanner().tick()
    audio.unlink()
    assert (await LyricsScanner().tick())["missing"] == 1
    assert await _rows("SELECT file_missing, local_source, local_plain FROM track_lyrics "
                       "WHERE track_id = :id", id=tid) == [(True, "embedded", L1)]


@requires_db
async def test_edited_and_deleted_transitions_reach_the_row(v021, music) -> None:
    from domovoi.db.session import session_scope

    own = domovoi_lrc()
    a = await _seed(tagged_mp3(music / "Lantern Song.mp3"))
    b = await _seed(tagged_mp3(music / "Glass Harbor.mp3"), title="Glass Harbor")
    (music / "Lantern Song.lrc").write_bytes(own + b"[00:09.00]an added line\n")
    async with session_scope() as s:
        await store.ensure_rows(s, [a, b])
        await s.execute(text(
            "UPDATE track_lyrics SET lrc_state = 'written', lrc_sha256 = :sha, "
            "lrc_name = CASE WHEN track_id = :a THEN 'Lantern Song.lrc' ELSE 'Glass Harbor.lrc' END"
        ), {"sha": sha256_hex(own), "a": a})
    await LyricsScanner().tick()
    rows = dict(await _rows("SELECT track_id, lrc_state FROM track_lyrics"))
    assert rows == {a: "edited", b: "deleted"}
    assert await _rows("SELECT local_source FROM track_lyrics WHERE track_id = :id", id=a) == [("sidecar",)]


@requires_db
@needs_mutagen
async def test_the_scan_and_the_writer_at_the_same_time_finish_and_agree(
    v021, music, monkeypatch,
) -> None:
    """[O4]: 150 songs (two scan batches) while the LRCLIB step writes 75
    ``.lrc`` files. Both finish; afterwards no file Domovoi wrote is read
    as the household's."""
    from domovoi.db.session import session_scope
    from domovoi.workers.lyrics_fetch import LyricsFetcher

    monkeypatch.setattr(settings, "lyrics_lrclib_enabled", True)
    monkeypatch.setattr(settings, "lyrics_write_lrc", True)
    ids = []
    for i in range(150):
        ids.append(await _seed(tagged_mp3(music / f"disc {i % 3}" / f"Song {i:03d}.mp3", lyrics=L3),
                               title=f"Song {i:03d}"))
    async with session_scope() as s:
        await store.ensure_rows(s, ids)
        await s.execute(text(
            "UPDATE track_lyrics SET local_checked_at = now(), lrclib_status = 'found', "
            "lrclib_plain = :plain, lrclib_synced = CAST(:synced AS JSONB), lrclib_match = 'get', "
            "lrclib_id = track_id WHERE track_id = ANY(:half)"
        ), {"plain": L1, "synced": json.dumps([[1000, L1]]), "half": ids[::2]})
    fetcher = LyricsFetcher(client=None)
    counts: Counter = Counter()
    await asyncio.wait_for(asyncio.gather(LyricsScanner(batch_size=100).tick(),
                                          fetcher._lrc_step(counts)), timeout=120)
    assert counts["lrc_written"] == 75
    await LyricsScanner().tick()                          # see every new file once
    rows = await _rows("SELECT track_id, lrc_state, lrc_name, lrc_sha256, local_source, sidecar_name "
                       "FROM track_lyrics WHERE lrc_state IS NOT NULL")
    assert len(rows) == 75
    for track_id, state, name, sha, local_source, sidecar_name in rows:
        assert state == "written"
        assert local_source == "embedded"                 # never source 1
        assert sidecar_name == name
        (path,) = (await _rows("SELECT file_path FROM library_tracks WHERE id = :id", id=track_id))[0]
        assert sha256_hex((Path(path).parent / name).read_bytes()) == sha
    assert not sidecar.SIDECAR_LOCK.locked()
