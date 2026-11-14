"""Library tools (V058) against the database: duplicates moved to the trash
with their playlist places, "Needs review" apply / skip, playlists from
files (with a clash), a file's own MusicBrainz id skipping song
recognition, credited artists found by search, the enricher keeping
review candidates, and cover-art Undo through the route.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from domovoi import admin_auth
from domovoi.config import settings
from domovoi.tests import media_writer as mw
from domovoi.tests.conftest import requires_db
from domovoi.workers.library_cover_art import apply_cover
from domovoi.workers import library_enricher as le
from domovoi.workers.library_indexer import index_music_dir
from domovoi.library_playlists_import import CLASH_SUFFIX, sync_file_playlists
from domovoi.cover_art_writer import cover_cache_path
from domovoi.handlers.shared import spoken_index
from domovoi.handlers.shared.spoken_names import alias_key

MBID = "6f0e7c58-6a49-4e0c-8a4e-6b1d2f9f8f11"
MBID2 = "7f0e7c58-6a49-4e0c-8a4e-6b1d2f9f8f22"
JPEG_OLD = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"o" * 300
JPEG_NEW = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"n" * 500


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
    run("TRUNCATE library_tracks, playlists, library_duplicate_keeps, library_trash "
        "RESTART IDENTITY CASCADE")


def _track(path: Path, title: str, artist: str, dur: int, **extra) -> int:
    cols = {"file_path": str(path), "title": title, "artist": artist, "duration_sec": dur,
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


# ─── 2. Duplicates ───────────────────────────────────────────────────────


@requires_db
def test_duplicates_listed_moved_to_trash_repointed_and_restored(web, tmp_path) -> None:
    music = tmp_path / "music"
    good = mw.write_mp3(music / "Linkin Park" / "In the End.mp3", [mw.TIT2("In the End")],
                        audio=mw.silent_mp3(400))
    upload = mw.write_mp3(music / "uploads" / "Linkin Park - In the End (Official Video).mp3",
                          [mw.TIT2("x")])
    keep = _track(good, "In the End", "Linkin Park", 216, album="Hybrid Theory")
    gone = _track(upload, "Linkin Park - In the End (Official Video)", "Linkin Park", 217,
                  favorited=True)
    other = _track(music / "x.mp3", "Numb", "Linkin Park", 185)
    pid = int(run("INSERT INTO playlists (name) VALUES ('Mix') RETURNING id")[0][0])
    pid2 = int(run("INSERT INTO playlists (name) VALUES ('Both') RETURNING id")[0][0])
    run("INSERT INTO playlist_tracks (playlist_id, track_id, position) VALUES "
        "(:p, :g, 0), (:p, :o, 1), (:q, :g, 0), (:q, :k, 1)",
        {"p": pid, "q": pid2, "g": gone, "o": other, "k": keep})

    d = web.get("/api/music/library-tools/duplicates").json()
    [g] = d["groups"]
    assert g["label"] == "Likely the same recording"
    assert {m["id"] for m in g["members"]} == {keep, gone}
    assert g["best_id"] == keep                          # the bigger file, tagged

    r = web.post("/api/music/library-tools/duplicates/resolve",
                 json={"keep_id": keep, "member_ids": [keep, gone]})
    assert r.status_code == 200 and r.json()["moved"] == [gone]
    assert not upload.exists()
    assert run("SELECT count(*) FROM library_tracks WHERE id = :g", {"g": gone})[0][0] == 0
    mix = [r[0] for r in run("SELECT track_id FROM playlist_tracks WHERE playlist_id = :p "
                             "ORDER BY position", {"p": pid})]
    assert mix == [keep, other]                          # the moved copy's place goes to the kept one
    both = [r[0] for r in run("SELECT track_id FROM playlist_tracks WHERE playlist_id = :p",
                              {"p": pid2})]
    assert both == [keep]                                # no double entry
    assert run("SELECT favorited FROM library_tracks WHERE id = :k", {"k": keep})[0][0] is True
    assert web.get("/api/music/library-tools/duplicates").json()["groups"] == []

    [item] = web.get("/api/music/library-tools/trash").json()["items"]
    assert item["in_trash"] is True
    r = web.post(f"/api/music/library-tools/trash/{item['id']}/restore")
    assert r.status_code == 200, r.text
    assert upload.exists()
    rows = run("SELECT title FROM library_tracks WHERE file_path LIKE :p", {"p": "%Official Video%"})
    assert [r[0] for r in rows] == ["Linkin Park - In the End (Official Video)"]
    assert web.get("/api/music/library-tools/trash").json()["items"] == []


@requires_db
def test_keep_both_is_remembered_until_the_group_changes(web, tmp_path) -> None:
    a = _track(tmp_path / "music" / "a.mp3", "Faint", "Linkin Park", 162)
    b = _track(tmp_path / "music" / "b.mp3", "Faint", "Linkin Park", 175)
    assert len(web.get("/api/music/library-tools/duplicates").json()["groups"]) == 1
    assert web.post("/api/music/library-tools/duplicates/keep-both",
                    json={"member_ids": [a, b]}).status_code == 200
    d = web.get("/api/music/library-tools/duplicates").json()
    assert d["groups"] == [] and d["kept"] == 1
    _track(tmp_path / "music" / "c.mp3", "Faint (Live)", "Linkin Park", 180)
    assert len(web.get("/api/music/library-tools/duplicates").json()["groups"]) == 1


# ─── 4. Needs review ─────────────────────────────────────────────────────


@requires_db
def test_review_apply_sets_names_and_id_and_skip_is_remembered(web, tmp_path) -> None:
    t = _track(tmp_path / "music" / "jumper.mp3", "Jumper", "Third Eye Blind", 272,
               enrich_outcome="disagreed", year=2001, year_source="musicbrainz",
               year_checked_at=None)
    u = _track(tmp_path / "music" / "u.mp3", "Other", "Someone", 200, enrich_outcome="no_match")
    cands = [{"title": "Jumper (1998 Remaster)", "artist": "Third Eye Blind", "album": "Third Eye Blind",
              "length_sec": 273, "score": 0.91, "sources": 12, "recording_id": MBID,
              "release_group_id": MBID2, "source": "acoustid"}]
    run("INSERT INTO library_match_candidates (track_id, reason, candidates) VALUES "
        "(:t, 'disagreed', CAST(:c AS jsonb)), (:u, 'low_confidence', CAST(:c AS jsonb))",
        {"t": t, "u": u, "c": json.dumps(cands)})
    items = web.get("/api/music/library-tools/review").json()["items"]
    assert {i["track_id"] for i in items} == {t, u}
    assert items[0]["candidates"][0]["sources"] == 12

    assert web.post(f"/api/music/library-tools/review/{t}/apply", json={"index": 0}).status_code == 200
    row = run("SELECT title, musicbrainz_recording_id, musicbrainz_release_group_id, enrich_outcome, "
              "year, year_checked_at FROM library_tracks WHERE id = :t", {"t": t})[0]
    assert tuple(row) == ("Jumper (1998 Remaster)", MBID, MBID2, "reviewed", None, None)
    assert web.post(f"/api/music/library-tools/review/{u}/skip").status_code == 200
    assert web.get("/api/music/library-tools/review").json()["items"] == []
    assert web.post(f"/api/music/library-tools/review/{u}/skip").status_code == 404
    assert web.post(f"/api/music/library-tools/review/{t}/apply", json={"index": 3}).status_code == 404


@requires_db
async def test_the_enricher_keeps_candidates_for_a_disagreement(db_session, tmp_path, monkeypatch) -> None:
    f = tmp_path / "Third Eye Blind - Jumper.mp3"
    f.write_bytes(mw.silent_mp3())
    await db_session.execute(text(
        "INSERT INTO library_tracks (file_path, title, artist, duration_sec, added_via) "
        "VALUES (:p, 'Jumper', 'Third Eye Blind', 272, 'manual')"), {"p": str(f)})
    await db_session.commit()
    cover = le.EnrichmentResult("Jumper", "Tweaker", musicbrainz_recording_id=MBID,
                                source="acoustid", score=0.93, sources=2, length_sec=270)

    async def one(path, providers=None, names=None, length=None):
        return le._agreeing(le.Lookup(le.Verdict.MATCH, result=cover, candidates=(cover,)),
                            names, length)

    monkeypatch.setattr(le, "_enrich_one", one)
    monkeypatch.setattr(le, "provider_available", lambda: True)
    monkeypatch.setattr(le.settings, "library_enricher_enabled", True)
    monkeypatch.setattr(le.settings, "library_enricher_delay_sec", 0)
    monkeypatch.setattr(le.connectivity, "current_probe", lambda: None)
    from domovoi import egress

    with egress.override_policy("always"):
        counts = await le.enrich_library()
    assert counts["disagreed"] == 1
    row = (await db_session.execute(text(
        "SELECT c.reason, c.candidates FROM library_match_candidates c"))).one()
    assert row[0] == "disagreed" and row[1][0]["artist"] == "Tweaker"


# ─── 5. A file's own id, credits, playlists from files ───────────────────


@requires_db
async def test_a_file_with_its_own_recording_id_is_never_fingerprinted(db_session, tmp_path, monkeypatch) -> None:
    music = tmp_path / "music"
    p = music / "x.flac"
    p.parent.mkdir()
    p.write_bytes(mw.flac_file([("TITLE", "Song"), ("ARTIST", "A"), ("MUSICBRAINZ_TRACKID", MBID),
                                ("ARTISTS", "A"), ("ARTISTS", "Bea Credited")]))
    monkeypatch.setattr(settings, "music_dir", str(music))
    assert (await index_music_dir())["inserted"] == 1
    row = (await db_session.execute(text(
        "SELECT musicbrainz_recording_id, enrich_outcome, enriched_at IS NOT NULL, artist_credits, "
        "search_text FROM library_tracks"))).one()
    assert row[0] == MBID and row[1] == "file" and row[2] is True
    assert row[3] == ["A", "Bea Credited"] and "bea credited" in row[4]
    assert await le.waiting_count(db_session) == 0      # not in song recognition's queue


@requires_db
def test_search_finds_a_song_by_a_credited_artist(web, tmp_path) -> None:
    _track(tmp_path / "music" / "c.mp3", "Collab", "Main Artist", 200,
           artist_credits=["Main Artist", "Guest Singer"])
    r = web.get("/api/music/library", params={"q": "guest singer"})
    assert [t["title"] for t in r.json()["items"]] == ["Collab"]


def test_the_voice_index_knows_a_credited_artist() -> None:
    idx = spoken_index.build([
        {"id": 1, "title": "Collab", "artist": "Main Artist", "album": None, "file_path": "/m/c.mp3",
         "artist_credits": ["Main Artist", "Guest Singer"]},
        {"id": 2, "title": "Solo", "artist": "Other", "album": None, "file_path": "/m/s.mp3"},
    ])
    i = idx.find("artist", alias_key("Guest Singer"))
    assert i is not None and list(idx.entity(i).track_ids) == [1]


@requires_db
async def test_playlists_from_files_sync_clash_and_read_only(db_session, tmp_path, monkeypatch) -> None:
    music = tmp_path / "music"
    (music / "Seal" / "Playlists").mkdir(parents=True)
    a, b = music / "a.mp3", music / "b.mp3"
    for p in (a, b):
        p.write_bytes(mw.silent_mp3())
    await db_session.execute(text(
        "INSERT INTO library_tracks (file_path, title, file_playlists) VALUES "
        "(:a, 'A', NULL), (:b, 'B', ARRAY['Gym'])"), {"a": str(a), "b": str(b)})
    await db_session.execute(text("INSERT INTO playlists (name) VALUES ('Road Trip')"))
    await db_session.commit()
    (music / "Seal" / "Playlists" / "Road Trip.m3u").write_text("../../b.mp3\n../../a.mp3\n",
                                                                 encoding="utf-8")
    monkeypatch.setattr(settings, "music_dir", str(music))
    assert await sync_file_playlists() == {"created": 2, "updated": 0, "removed": 0}
    rows = (await db_session.execute(text(
        "SELECT p.name, p.imported_from, array_agg(t.title ORDER BY pt.position) FROM playlists p "
        "LEFT JOIN playlist_tracks pt ON pt.playlist_id = p.id LEFT JOIN library_tracks t ON t.id = pt.track_id "
        "GROUP BY p.id ORDER BY p.name"))).all()
    assert [tuple(r) for r in rows] == [
        ("Gym", "files", ["B"]), ("Road Trip", None, [None]),
        (f"Road Trip{CLASH_SUFFIX}", "files", ["B", "A"]),
    ]
    # The household's own playlist is untouched; a second sync changes nothing;
    # a playlist no file names any more goes.
    assert await sync_file_playlists() == {"created": 0, "updated": 0, "removed": 0}
    (music / "Seal" / "Playlists" / "Road Trip.m3u").unlink()
    assert (await sync_file_playlists())["removed"] == 1


@requires_db
def test_an_imported_playlist_is_read_only_and_can_be_copied(web, tmp_path) -> None:
    t = _track(tmp_path / "music" / "a.mp3", "A", "X", 100)
    pid = int(run("INSERT INTO playlists (name, imported_from, import_name) "
                  "VALUES ('Gym', 'files', 'Gym') RETURNING id")[0][0])
    run("INSERT INTO playlist_tracks (playlist_id, track_id, position) VALUES (:p, :t, 0)",
        {"p": pid, "t": t})
    [row] = [p for p in web.get("/api/playlists").json() if p["id"] == pid]
    assert row["imported"] is True
    assert web.patch(f"/api/playlists/{pid}", json={"name": "Mine"}).status_code == 409
    assert web.delete(f"/api/playlists/{pid}/tracks/{t}").status_code == 409
    assert web.post(f"/api/playlists/{pid}/tracks", json={"track_id": t}).status_code == 409
    assert web.delete(f"/api/playlists/{pid}").status_code == 409
    r = web.post(f"/api/playlists/{pid}/copy")
    assert r.status_code == 201 and r.json()["name"] == "Gym (copy)" and r.json()["track_count"] == 1
    assert r.json()["imported"] is False


# ─── 6. Cover art: the record and Undo through the route ─────────────────


@requires_db
def test_cover_art_undo_route_restores_the_file(web, tmp_path) -> None:
    p = mw.write_mp3(tmp_path / "music" / "a.mp3", [mw.TIT2("Song"),
                     mw.APIC(mime="image/jpeg", type=3, desc="own", data=JPEG_OLD)])
    t = _track(p, "Song", "A", 100, musicbrainz_recording_id=MBID, enrich_outcome="matched")
    out = apply_cover(t, str(p), JPEG_NEW, "image/jpeg")
    assert out["state"] == "written"
    run("INSERT INTO library_cover_art (track_id, state, source_kind, source_id, snapshot_path, had_art) "
        "VALUES (:t, 'written', 'release', :s, :snap, TRUE)",
        {"t": t, "s": MBID2, "snap": out["snapshot_path"]})
    d = web.get("/api/music/library-tools/cover-art").json()
    assert d["counts"] == {"written": 1} and d["items"][0]["had_art"] is True
    assert web.get(f"/api/music/library/{t}/cover").content == JPEG_NEW
    assert web.post(f"/api/music/library-tools/cover-art/{t}/undo").status_code == 200
    assert web.get(f"/api/music/library/{t}/cover").content == JPEG_OLD
    assert run("SELECT state FROM library_cover_art WHERE track_id = :t", {"t": t})[0][0] == "undone"
    assert web.post(f"/api/music/library-tools/cover-art/{t}/undo").status_code == 404


@requires_db
def test_the_cover_route_shows_domovois_copy_for_a_file_it_does_not_write(web, tmp_path) -> None:
    m4a = tmp_path / "music" / "b.m4a"
    m4a.write_bytes(mw.bare_m4a())
    t = _track(m4a, "B", "A", 100)
    assert web.get(f"/api/music/library/{t}/cover").status_code == 404
    dest = cover_cache_path(t, "image/jpeg")
    dest.parent.mkdir(parents=True)
    dest.write_bytes(JPEG_NEW)
    r = web.get(f"/api/music/library/{t}/cover")
    assert r.status_code == 200 and r.content == JPEG_NEW


@requires_db
async def test_the_cover_art_worker_writes_once_and_records_no_art(db_session, tmp_path, monkeypatch) -> None:
    from domovoi import egress
    from domovoi.workers import library_cover_art as w

    music = tmp_path / "music"
    has = mw.write_mp3(music / "a.mp3", [mw.TIT2("A")])
    none = mw.write_mp3(music / "b.mp3", [mw.TIT2("B")])
    guessed = mw.write_mp3(music / "c.mp3", [mw.TIT2("C")])
    await db_session.execute(text(
        "INSERT INTO library_tracks (file_path, title, musicbrainz_recording_id, musicbrainz_release_id, "
        "enrich_outcome) VALUES (:a, 'A', :m, :r1, 'matched'), (:b, 'B', :m, :r2, 'file'), "
        "(:c, 'C', :m, :r1, 'no_match')"),
        {"a": str(has), "b": str(none), "c": str(guessed), "m": MBID,
         "r1": "11111111-1111-1111-1111-111111111111", "r2": "22222222-2222-2222-2222-222222222222"})
    await db_session.commit()
    monkeypatch.setattr(settings, "library_cover_art_enabled", True)
    monkeypatch.setattr(settings, "cover_art_dir", str(tmp_path / "cover_art"))
    monkeypatch.setattr(w, "_probe_online", lambda: True)
    asked: list[str] = []

    async def fetch(release, group):
        asked.append(release)
        if release.startswith("1111"):
            return JPEG_NEW, "image/jpeg", "release", release
        return None

    monkeypatch.setattr(w, "fetch_front", fetch)

    async def no_rooms(*a, **k):
        return None

    monkeypatch.setattr("domovoi.mpd_library_sync.update_rooms_for", no_rooms)
    worker = w.LibraryCoverArt()
    with egress.override_policy("always"):
        got = await worker.tick()
        assert got["written"] == 1 and got["no_art"] == 1    # the unconfirmed track is never asked
        await worker.tick()                                  # end of the queue
        assert (await worker.tick())["asked"] == 0           # nothing is asked again
    assert sorted(asked) == ["11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"]
    rows = dict((await db_session.execute(text(
        "SELECT t.title, c.state FROM library_cover_art c JOIN library_tracks t ON t.id = c.track_id"))).all())
    assert rows == {"A": "written", "B": "no_art"}
    from domovoi.cover_art_writer import current_cover

    assert current_cover(has) == JPEG_NEW and current_cover(none) is None
