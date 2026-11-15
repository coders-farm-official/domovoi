"""Library tools follow-ups (1.3.0): credited artists on track rows, the
cover version (V059: ``cover_v``, the ``?v=`` cover URL, the
``library.cover`` realtime channel), and playlists made from files staying
read-only for a finished download and a plugin's ingest.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from domovoi import admin_auth, library_cover_version as cv
from domovoi.config import settings
from domovoi.library_playlists_import import ATTACH_IMPORTED_NOTE
from domovoi.tests import media_writer as mw
from domovoi.tests.conftest import requires_db
from domovoi.workers.library_cover_art import _record, apply_cover
from web.backend.api.music import credited_artists

MBID = "6f0e7c58-6a49-4e0c-8a4e-6b1d2f9f8f11"
JPEG_OLD = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"o" * 300
JPEG_NEW = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"n" * 500


# ─── Pure ────────────────────────────────────────────────────────────────


def test_credited_artists_keep_the_files_order_and_drop_only_the_primary() -> None:
    assert credited_artists(None, "A") == []
    assert credited_artists([], "A") == []
    assert credited_artists(["A"], "A") == []
    assert credited_artists(["a "], "A") == []
    assert credited_artists(["A", "C", "B", "c", " "], "A") == ["A", "C", "B"]
    # The file may not name the primary credit at all.
    assert credited_artists(["B", "C"], "A feat. B") == ["B", "C"]


def test_the_cover_signature_moves_with_the_file_and_the_folder_image(tmp_path) -> None:
    song = tmp_path / "album" / "a.mp3"
    song.parent.mkdir()
    song.write_bytes(b"ID3" + b"\0" * 50)
    first = cv.cover_signature(song)
    assert first and cv.cover_signature(song) == first
    (song.parent / "notes.txt").write_text("x")
    assert cv.cover_signature(song) == first                 # not a cover image
    (song.parent / "Cover.JPG").write_bytes(JPEG_OLD)
    with_folder = cv.cover_signature(song)
    assert with_folder != first
    st = song.stat()
    os.utime(song, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    assert cv.cover_signature(song) not in (first, with_folder)
    assert cv.cover_signature(tmp_path / "gone.mp3") is None
    many = cv.cover_signatures([song, tmp_path / "gone.mp3"])
    assert many[str(song)] == cv.cover_signature(song) and many[str(tmp_path / "gone.mp3")] is None


def test_the_cover_url_carries_the_version() -> None:
    assert cv.cover_url(7, 3) == "/api/music/library/7/cover?v=3"
    assert cv.cover_url(7, None) == "/api/music/library/7/cover?v=0"


# ─── Against the database ────────────────────────────────────────────────


async def _run(sql: str, params: dict | None = None):
    from web.backend.db import session_scope

    async with session_scope() as s:
        res = await s.execute(text(sql), params or {})
        try:
            rows = res.all()
        except Exception:  # noqa: BLE001 — not a SELECT
            rows = None
        await s.commit()
        return rows


def run(sql: str, params: dict | None = None):
    return asyncio.run(_run(sql, params))


def _clean() -> None:
    run("TRUNCATE library_tracks, playlists, library_cover_art RESTART IDENTITY CASCADE")


def _track(path: Path, title: str, artist: str, **extra) -> int:
    cols = {"file_path": str(path), "title": title, "artist": artist, "duration_sec": 100,
            "added_via": "manual", **extra}
    names = ", ".join(cols)
    binds = ", ".join(f":{k}" for k in cols)
    return int(run(f"INSERT INTO library_tracks ({names}) VALUES ({binds}) RETURNING id", cols)[0][0])


@pytest.fixture
def web(tmp_path, monkeypatch):
    from web.backend.main import app

    monkeypatch.setattr(settings, "music_dir", str(tmp_path / "music"))
    monkeypatch.setattr(settings, "cover_art_dir", str(tmp_path / "cover_art"))
    (tmp_path / "music").mkdir()
    app.dependency_overrides[admin_auth.require_admin_mutation] = lambda: None
    app.dependency_overrides[admin_auth.require_admin_read] = lambda: None
    _clean()
    try:
        with TestClient(app, headers={"X-Requested-With": "domovoi-tests"}) as c:
            yield c
    finally:
        app.dependency_overrides.pop(admin_auth.require_admin_mutation, None)
        app.dependency_overrides.pop(admin_auth.require_admin_read, None)
        _clean()


@requires_db
def test_track_rows_carry_credited_artists_and_a_versioned_cover_url(web, tmp_path) -> None:
    collab = _track(tmp_path / "music" / "c.mp3", "Collab", "Main",
                    artist_credits=["Main", "Guest", "Third"])
    solo = _track(tmp_path / "music" / "s.mp3", "Solo", "Main", artist_credits=["Main"])
    plain = _track(tmp_path / "music" / "p.mp3", "Plain", "Other")
    run("UPDATE library_tracks SET cover_rev = 4 WHERE id = :id", {"id": collab})
    items = {t["id"]: t for t in web.get("/api/music/library").json()["items"]}
    assert items[collab]["artist"] == "Main"
    assert items[collab]["artists"] == ["Main", "Guest", "Third"]
    assert items[solo]["artists"] == [] and items[plain]["artists"] == []
    assert items[collab]["cover_v"] == 4
    assert items[collab]["cover_url"] == f"/api/music/library/{collab}/cover?v=4"
    assert items[plain]["cover_v"] == 0 and items[plain]["cover_url"].endswith("?v=0")
    one = web.get(f"/api/music/library/{collab}").json()
    assert one["artists"] == ["Main", "Guest", "Third"] and one["cover_v"] == 4
    patched = web.patch(f"/api/music/library/{collab}", json={"favorited": True}).json()
    assert patched["artists"] == ["Main", "Guest", "Third"] and patched["cover_v"] == 4
    pid = int(run("INSERT INTO playlists (name) VALUES ('Mine') RETURNING id")[0][0])
    run("INSERT INTO playlist_tracks (playlist_id, track_id, position) VALUES (:p, :t, 0)",
        {"p": pid, "t": collab})
    [row] = web.get(f"/api/playlists/{pid}/tracks").json()
    assert row["artists"] == ["Main", "Guest", "Third"] and row["cover_url"].endswith("?v=4")
    # The virtual Favorites list (id 0) reads the same columns.
    [fav] = web.get("/api/playlists/0/tracks").json()
    assert fav["id"] == collab and fav["cover_v"] == 4 and fav["artists"] == ["Main", "Guest", "Third"]
    # The cover route ignores ?v= and keeps its ETag.
    r1 = web.get(f"/api/music/library/{collab}/cover?v=4")
    r2 = web.get(f"/api/music/library/{collab}/cover")
    assert r1.status_code == r2.status_code


@requires_db
def test_undo_moves_the_cover_version(web, tmp_path) -> None:
    p = mw.write_mp3(tmp_path / "music" / "a.mp3", [mw.TIT2("Song"),
                     mw.APIC(mime="image/jpeg", type=3, desc="own", data=JPEG_OLD)])
    t = _track(p, "Song", "A", musicbrainz_recording_id=MBID, enrich_outcome="matched")
    out = apply_cover(t, str(p), JPEG_NEW, "image/jpeg")
    assert out["state"] == "written" and out["cover_sig"] == cv.cover_signature(p)
    run("INSERT INTO library_cover_art (track_id, state, source_kind, source_id, snapshot_path, had_art) "
        "VALUES (:t, 'written', 'release', :s, :snap, TRUE)",
        {"t": t, "s": MBID, "snap": out["snapshot_path"]})
    before = web.get(f"/api/music/library/{t}").json()["cover_v"]
    assert web.post(f"/api/music/library-tools/cover-art/{t}/undo").status_code == 200
    row = run("SELECT cover_rev, cover_sig, cover_changed_at FROM library_tracks WHERE id = :t", {"t": t})[0]
    assert row[0] == before + 1 and row[1] == cv.cover_signature(p) and row[2] is not None
    after = web.get(f"/api/music/library/{t}").json()
    assert after["cover_url"] == f"/api/music/library/{t}/cover?v={before + 1}"
    items = web.get("/api/music/library-tools/cover-art").json()["items"]
    assert items == [] or all("cover_v" in i for i in items)


@requires_db
async def test_the_cover_art_worker_record_moves_the_version(db_session) -> None:
    await db_session.execute(text(
        "INSERT INTO library_tracks (file_path, title) VALUES ('/m/a.mp3', 'A'), ('/m/b.mp3', 'B')"))
    await db_session.commit()
    await _record(1, {"state": "written", "cover_sig": "abc"}, "release", MBID)
    await _record(2, {"state": "no_art"}, "release", MBID)
    rows = dict((await db_session.execute(text(
        "SELECT id, (cover_rev, cover_sig) FROM library_tracks ORDER BY id"))).all())
    assert rows == {1: (1, "abc"), 2: (0, None)}


@requires_db
async def test_a_rescan_moves_the_version_only_when_the_art_changes(db_session, tmp_path, monkeypatch) -> None:
    from domovoi.workers.library_indexer import index_music_dir

    async def no_rooms(*a, **k):
        return None

    monkeypatch.setattr("domovoi.workers.library_indexer.update_rooms_for", no_rooms)

    async def no_sync():
        return {}

    monkeypatch.setattr("domovoi.library_playlists_import.sync_file_playlists", no_sync)
    music = tmp_path / "music"
    mw.write_mp3(music / "Album" / "a.mp3", [mw.TIT2("A")])
    b = mw.write_mp3(music / "Other" / "b.mp3", [mw.TIT2("B")])
    monkeypatch.setattr(settings, "music_dir", str(music))

    async def state():
        rows = (await db_session.execute(text(
            "SELECT title, cover_rev, cover_sig IS NOT NULL FROM library_tracks ORDER BY title"))).all()
        await db_session.commit()
        return [tuple(r) for r in rows]

    assert (await index_music_dir())["inserted"] == 2
    assert await state() == [("A", 0, False), ("B", 0, False)]
    await index_music_dir()                       # the first reading: recorded, not a change
    assert await state() == [("A", 0, True), ("B", 0, True)]
    # Nothing changed: no tag is read and no cover version moves.
    from domovoi.workers import library_indexer

    def no_reads(path):
        raise AssertionError(f"tags read for an unchanged file: {path}")

    monkeypatch.setattr(library_indexer, "_resolve_metadata_for", no_reads)
    sigs_before = (await db_session.execute(text(
        "SELECT cover_sig, cover_changed_at FROM library_tracks ORDER BY title"))).all()
    await db_session.commit()
    await index_music_dir()
    assert await state() == [("A", 0, True), ("B", 0, True)]
    assert (await db_session.execute(text(
        "SELECT cover_sig, cover_changed_at FROM library_tracks ORDER BY title"))).all() == sigs_before
    await db_session.commit()
    (music / "Album" / "folder.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 50)
    await index_music_dir()                       # a new album-folder image
    assert await state() == [("A", 1, True), ("B", 0, True)]
    del b

    from web.backend import realtime

    snap = await realtime._snapshot_library_covers()
    [only] = snap["tracks"]
    assert only["cover_v"] == 1 and only["cover_url"].endswith("?v=1")


@requires_db
async def test_bump_fires_the_library_cover_notify(db_session) -> None:
    from web.backend import realtime

    assert realtime.NOTIFY_CHANNEL_TO_REALTIME[cv.NOTIFY_CHANNEL] == "library.cover"
    assert "library.cover" in realtime.CORE_REALTIME_CHANNELS
    await db_session.execute(text("INSERT INTO library_tracks (file_path, title) VALUES ('/m/a.mp3', 'A')"))
    assert await cv.bump(db_session, []) == 0
    assert await cv.bump(db_session, [1, 1, 99]) == 1
    await db_session.commit()
    assert (await db_session.execute(text("SELECT cover_rev FROM library_tracks"))).scalar_one() == 1


# ─── Playlists made from files stay read-only ────────────────────────────


async def _imported_playlist(db_session) -> tuple[int, int]:
    pid = (await db_session.execute(text(
        "INSERT INTO playlists (name, imported_from, import_name) "
        "VALUES ('Gym', 'files', 'Gym') RETURNING id"))).scalar_one()
    tid = (await db_session.execute(text(
        "INSERT INTO library_tracks (file_path, title) VALUES ('/music/x.mp3', 'X') RETURNING id"
    ))).scalar_one()
    return int(pid), int(tid)


@requires_db
async def test_a_download_never_lands_on_a_playlist_made_from_files(db_session, caplog) -> None:
    from domovoi.acquisitions import ACQUISITIONS
    from domovoi.capabilities import CAPABILITIES, MEDIA_ACQUISITION_FULFILLER

    pid, tid = await _imported_playlist(db_session)
    ACQUISITIONS.register_fulfiller("providerx", kinds={"query"})
    try:
        await ACQUISITIONS.enqueue(db_session, kind="query", text="x", requested_by="voice:playlist",
                                   attach_to_playlist_id=pid)
        claimed = await ACQUISITIONS.claim_next(db_session, slug="providerx")
        with caplog.at_level("INFO", logger="domovoi.acquisitions"):
            await ACQUISITIONS.complete(db_session, claimed.id, library_track_id=tid,
                                        file_path="/music/x.mp3")
    finally:
        CAPABILITIES.unregister(MEDIA_ACQUISITION_FULFILLER, slug="providerx")
    row = (await db_session.execute(text(
        "SELECT status, result, error FROM media_acquisitions WHERE id = :id"), {"id": claimed.id})).one()
    assert row.status == "done" and row.error is None
    assert row.result["library_track_id"] == tid
    assert row.result["attach_skipped"] == ATTACH_IMPORTED_NOTE
    assert (await db_session.execute(text(
        "SELECT count(*) FROM playlist_tracks WHERE playlist_id = :p"), {"p": pid})).scalar_one() == 0
    assert sum("read-only" in r.getMessage() for r in caplog.records) == 1


@requires_db
async def test_a_plugin_ingest_never_lands_on_a_playlist_made_from_files(
        db_session, monkeypatch, tmp_path, caplog) -> None:
    from domovoi.clients import mpd as mpd_mod
    from domovoi.sdk.library import LibraryAPI

    pid, _ = await _imported_playlist(db_session)
    own = int((await db_session.execute(text(
        "INSERT INTO playlists (name) VALUES ('Mine') RETURNING id"))).scalar_one())
    monkeypatch.setattr(settings, "music_dir", str(tmp_path))
    mpd_mod._clients.clear()
    api = LibraryAPI("testplug")
    try:
        for i, target in enumerate((pid, own)):
            raw = tmp_path / "dl" / f"y{i}.mp3"
            raw.parent.mkdir(parents=True, exist_ok=True)
            raw.write_bytes(b"ID3fake")
            with caplog.at_level("INFO", logger="domovoi.sdk.library"):
                await api.ingest_track(db_session, file_path=raw, title=f"Song {i}", artist="Band",
                                       source="testplug", source_id=f"v{i}", added_via="voice",
                                       attach_to_playlist_id=target)
    finally:
        mpd_mod._clients.clear()
    counts = dict((await db_session.execute(text(
        "SELECT playlist_id, count(*) FROM playlist_tracks GROUP BY playlist_id"))).all())
    assert counts == {own: 1}
    assert sum("read-only" in r.getMessage() for r in caplog.records) == 1


# ─── Queue rows ──────────────────────────────────────────────────────────


@requires_db
def test_queue_rows_carry_credited_artists_and_the_cover_version(tmp_path, monkeypatch) -> None:
    import web.backend.api.music_queue as queue_api
    from web.backend.main import app

    _clean()
    run("TRUNCATE queue_device_blocks, room_queue_items, devices RESTART IDENTITY CASCADE")
    collab = _track(tmp_path / "c.mp3", "Collab", "Main", artist_credits=["Main", "Guest"])
    run("UPDATE library_tracks SET cover_rev = 2 WHERE id = :id", {"id": collab})
    items = [
        {"song_id": 1, "pos": 0, "file": "c.mp3", "title": "Collab", "artist": "Main", "track_id": collab},
        {"song_id": 2, "pos": 1, "file": "s.mp3", "title": "Stream", "track_id": None},
        {"song_id": 3, "pos": 2, "file": "g.mp3", "title": "Gone", "track_id": 999},
    ]

    async def _get_admin(path, timeout=10.0, headers=None):
        return 200, {"room_id": "kitchen", "items": items, "current_song_id": 1}

    monkeypatch.setattr(queue_api, "get_admin", _get_admin)
    try:
        with TestClient(app, headers={"X-Requested-With": "domovoi-tests"}) as c:
            got = c.get("/api/music/queue/kitchen").json()["items"]
    finally:
        _clean()
    assert got[0]["artists"] == ["Main", "Guest"] and got[0]["cover_v"] == 2
    assert got[0]["cover_url"] == f"/api/music/library/{collab}/cover?v=2"
    for row in got[1:]:
        assert row["artists"] == [] and row["cover_v"] is None and row["cover_url"] is None
