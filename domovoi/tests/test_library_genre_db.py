"""Genre and year in the database (V050, Up next P0), and the Genres API.

* V050: the columns, their checks, ``artist_norm`` generated from
  ``library_search_norm``, the indexes;
* the indexer writes genre, year and the reader's version on a new row;
* the tag backfill reads the rows the indexer never will, keeps a
  MusicBrainz year a file does not contradict, and leaves title / artist /
  album alone;
* the year fetch's writes: only where there is no year, every track of the
  recording, marked asked;
* a provider download (``LibraryAPI.ingest_track``) carries a year and is
  read again;
* ``GET /api/music/genres`` / ``/api/music/genre`` / ``/api/music/library?
  genre=``: counts, the "no genre" bucket, the read's progress, paging,
  search, 404.
* V052: a MusicBrainz genre only where the file has none, the file's
  genre replacing it on a re-read, rows the year fetch already asked asked
  once more, and the Genres page's "waiting for MusicBrainz" count.

Needs the database (``requires_db``); every test starts from an empty
``library_tracks`` (``db_session`` truncates it, the web tests do the same).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from domovoi.clients.musicbrainz import MbRecordingDetails
from domovoi.config import settings
from domovoi.library_genres import TAGS_VERSION
from domovoi.tests import media_writer as mw
from domovoi.tests.conftest import requires_db

MBID_A = "11111111-1111-4111-8111-111111111111"
MBID_B = "22222222-2222-4222-8222-222222222222"


# ─── V050 ─────────────────────────────────────────────────────────────────


@requires_db
async def test_v050_columns_and_defaults(db_session) -> None:
    await db_session.execute(text(
        "INSERT INTO library_tracks (file_path, title, artist) VALUES ('/m/a.mp3', 'A', 'T.I.')"
    ))
    row = (await db_session.execute(text(
        "SELECT genre, genre_keys, year, year_source, year_checked_at, tags_version, artist_norm "
        "FROM library_tracks"
    ))).one()
    assert tuple(row) == (None, None, None, None, None, 0, "ti")


@requires_db
@pytest.mark.parametrize("bad", [
    "UPDATE library_tracks SET year = 999",
    "UPDATE library_tracks SET year_source = 'guess'",
    "UPDATE library_tracks SET genre = repeat('x', 201)",
    "UPDATE library_tracks SET genre_keys = ARRAY(SELECT 'g' || i FROM generate_series(1, 17) i)",
])
async def test_v050_checks(db_session, bad) -> None:
    await db_session.execute(text(
        "INSERT INTO library_tracks (file_path, title) VALUES ('/m/a.mp3', 'A')"
    ))
    with pytest.raises(DBAPIError):
        await db_session.execute(text(bad))


@requires_db
async def test_v050_indexes(db_session) -> None:
    names = {r[0] for r in (await db_session.execute(text(
        "SELECT indexname FROM pg_indexes WHERE tablename = 'library_tracks'"
    ))).all()}
    assert {"idx_library_tracks_artist_norm", "idx_library_tracks_year",
            "idx_library_tracks_genre_keys", "idx_library_tracks_tags_pending",
            "idx_library_tracks_year_pending"} <= names


# ─── The indexer and the backfill ─────────────────────────────────────────


@requires_db
async def test_the_indexer_writes_genre_year_and_the_version(db_session, monkeypatch, tmp_path) -> None:
    from domovoi.workers.library_indexer import index_music_dir

    music = tmp_path / "music"
    mw.write_mp3(music / "a.mp3", [mw.text_frame("TIT2", "A"), mw.text_frame("TCON", "Soul; Funk"),
                                   mw.text_frame("TDRC", "1971")])
    mw.write_mp3(music / "b.mp3", [mw.text_frame("TIT2", "B")])
    monkeypatch.setattr("domovoi.workers.library_indexer.settings.music_dir", str(music))
    assert (await index_music_dir())["inserted"] == 2
    rows = (await db_session.execute(text(
        "SELECT title, genre, genre_keys, year, year_source, tags_version FROM library_tracks ORDER BY title"
    ))).all()
    assert [tuple(r) for r in rows] == [
        ("A", "Soul; Funk", ["soul", "funk"], 1971, "tag", TAGS_VERSION),
        ("B", None, None, None, None, TAGS_VERSION),
    ]


@requires_db
async def test_the_backfill_reads_rows_the_indexer_already_had(db_session, monkeypatch, tmp_path) -> None:
    from domovoi.workers.library_tag_backfill import LibraryTagBackfill

    music = tmp_path / "music"
    a = mw.write_mp3(music / "a.mp3", [mw.text_frame("TCON", "(17)"), mw.text_frame("TDRC", "1994")])
    b = mw.write_mp3(music / "b.mp3", [mw.text_frame("TCON", "Jazz")])          # no year in the file
    monkeypatch.setattr(settings, "music_dir", str(music))
    await db_session.execute(text(
        "INSERT INTO library_tracks (file_path, title, artist, album, year, year_source) VALUES "
        "(:a, 'Edited title', 'Edited artist', 'Edited album', NULL, NULL), "
        "(:b, 'B', 'X', NULL, 1959, 'musicbrainz'), "
        "(:gone, 'Gone', 'Y', NULL, NULL, NULL)"
    ), {"a": str(a), "b": str(b), "gone": str(music / "gone.mp3")})
    await db_session.commit()

    assert await LibraryTagBackfill().tick() == {"read": 2, "missing": 1, "deferred": 0, "refused": 0}
    rows = (await db_session.execute(text(
        "SELECT title, artist, album, genre, genre_keys, year, year_source, tags_version "
        "FROM library_tracks ORDER BY id"
    ))).all()
    assert [tuple(r) for r in rows] == [
        # Title / artist / album the household edited are left alone.
        ("Edited title", "Edited artist", "Edited album", "Rock", ["rock"], 1994, "tag", TAGS_VERSION),
        # The file has no year: the MusicBrainz one stays.
        ("B", "X", None, "Jazz", ["jazz"], 1959, "musicbrainz", TAGS_VERSION),
        # Not there: stamped read, so the pass ends.
        ("Gone", "Y", None, None, None, None, None, TAGS_VERSION),
    ]
    assert (await LibraryTagBackfill().tick())["read"] == 0


@requires_db
async def test_a_row_the_database_refuses_never_sinks_its_batch(db_session) -> None:
    """Security review of V050, finding 2: a value Postgres refuses (a NUL
    in text) used to roll back the whole batch, which was then re-read
    every second for ever. Each row now has its own savepoint; the refused
    one is stamped with no genre, the rest are written."""
    from domovoi.workers.library_tag_backfill import DbBackfillStore

    await db_session.execute(text(
        "INSERT INTO library_tracks (file_path, title) VALUES ('/m/1.mp3', '1'), "
        "('/m/2.mp3', '2'), ('/m/3.mp3', '3')"
    ))
    await db_session.commit()
    ids = [r[0] for r in (await db_session.execute(text("SELECT id FROM library_tracks ORDER BY id"))).all()]
    ok = {"genre": "Soul", "genre_keys": ["soul"], "year": 1970}
    bad = {"genre": "Rock\x00evil", "genre_keys": ["rock"], "year": 1980}   # past the sanitiser
    refused = await DbBackfillStore().write([(ids[0], ok), (ids[1], bad), (ids[2], ok)], TAGS_VERSION)
    assert refused == 1
    rows = (await db_session.execute(text(
        "SELECT genre, year, tags_version FROM library_tracks ORDER BY id"))).all()
    assert [tuple(r) for r in rows] == [
        ("Soul", 1970, TAGS_VERSION), (None, None, TAGS_VERSION), ("Soul", 1970, TAGS_VERSION),
    ]


@requires_db
async def test_nul_and_bidi_characters_never_reach_the_database(db_session, monkeypatch, tmp_path) -> None:
    from domovoi.workers.library_indexer import index_music_dir
    from domovoi.workers.library_tag_backfill import LibraryTagBackfill

    music = tmp_path / "music"
    music.mkdir()
    (music / "new.flac").write_bytes(mw.flac_file(comments=[
        ("TITLE", "Ti\x00tle"), ("ARTIST", "Art\x00ist"), ("GENRE", "Rock;\x00evil"),
        ("ALBUM", "Al\x00bum")]))
    old = mw.write_mp3(music / "old.mp3", [mw.text_frame("TCON", "\u202eRock\u202c")])
    monkeypatch.setattr(settings, "music_dir", str(music))
    await db_session.execute(text("INSERT INTO library_tracks (file_path, title) VALUES (:p, 'Old')"),
                             {"p": str(old)})
    await db_session.commit()
    assert (await index_music_dir())["inserted"] == 1
    assert (await LibraryTagBackfill().tick())["refused"] == 0
    rows = (await db_session.execute(text(
        "SELECT title, artist, album, genre, genre_keys FROM library_tracks ORDER BY id"))).all()
    assert [tuple(r) for r in rows] == [
        ("Old", None, None, "Rock", ["rock"]),
        ("Title", "Artist", "Album", "Rock;evil", ["rock", "evil"]),
    ]


# ─── The year fetch's writes ──────────────────────────────────────────────


@requires_db
async def test_the_year_fetch_store(db_session) -> None:
    from domovoi.workers.library_year_fetch import DbYearFetchStore

    await db_session.execute(text(
        "INSERT INTO library_tracks (file_path, title, musicbrainz_recording_id, year, year_source, tags_version) VALUES "
        "('/m/1.mp3', '1', :a, NULL, NULL, :v), "
        "('/m/2.mp3', '2', :a, NULL, NULL, :v), "        # the same recording twice
        "('/m/3.mp3', '3', :b, NULL, NULL, 0), "         # tags not read yet: not due
        "('/m/4.mp3', '4', :b, 1988, 'tag', :v)"         # has a year: not due
    ), {"a": MBID_A, "b": MBID_B, "v": TAGS_VERSION})
    await db_session.commit()
    store = DbYearFetchStore()
    assert await store.due(TAGS_VERSION, 25) == [MBID_A, MBID_B]   # 4 has no genre (V052)
    await store.record(MBID_A, MbRecordingDetails(year=1971))
    await store.record(MBID_B, MbRecordingDetails(year=2001))  # every track of B is marked asked
    rows = (await db_session.execute(text(
        "SELECT title, year, year_source, year_checked_at IS NOT NULL FROM library_tracks ORDER BY title"
    ))).all()
    assert [tuple(r) for r in rows] == [
        ("1", 1971, "musicbrainz", True),
        ("2", 1971, "musicbrainz", True),
        ("3", 2001, "musicbrainz", True),
        ("4", 1988, "tag", True),                         # a tag year is never replaced
    ]
    assert await store.due(TAGS_VERSION, 25) == []


# ─── A provider download ──────────────────────────────────────────────────


@requires_db
async def test_ingest_track_keeps_a_year_and_reads_tags_again(db_session, monkeypatch, tmp_path) -> None:
    from domovoi.clients import mpd as mpd_mod
    from domovoi.sdk.library import LibraryAPI

    monkeypatch.setattr(settings, "music_dir", str(tmp_path))
    mpd_mod._clients.clear()
    raw = tmp_path / "dl" / "x.mp3"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"ID3fake")
    api = LibraryAPI("testplug")
    track = await api.ingest_track(db_session, file_path=raw, title="Song", artist="Band",
                                   source="testplug", source_id="v1", added_via="voice",
                                   metadata={"year": 1983})
    await db_session.execute(text("UPDATE library_tracks SET tags_version = :v WHERE id = :id"),
                             {"v": TAGS_VERSION, "id": track.id})
    row = (await db_session.execute(text(
        "SELECT year, year_source FROM library_tracks WHERE id = :id"), {"id": track.id})).one()
    assert tuple(row) == (1983, "musicbrainz")
    # A new download of the same item: read again, the year kept.
    await api.ingest_track(db_session, file_path=Path(track.file_path), title="Song", artist="Band",
                           source="testplug", source_id="v1", added_via="voice",
                           metadata={"year": "nonsense"})
    row = (await db_session.execute(text(
        "SELECT year, year_source, tags_version FROM library_tracks WHERE id = :id"), {"id": track.id})).one()
    assert tuple(row) == (1983, "musicbrainz", 0)
    mpd_mod._clients.clear()


# ─── The web API ──────────────────────────────────────────────────────────

ROWS = [
    # (title, artist, genre_keys, tags_version)
    ("S1", "Aretha", ["soul"], 1),
    ("S2", "Otis", ["soul", "r&b"], 1),
    ("S3", "Bill", ["soul"], 1),
    ("H1", "Nas", ["hip hop"], 1),
    ("N1", "Nobody", None, 1),             # read, no genre
    ("N2", "Nobody", [], 1),               # read, an empty list counts the same
    ("U1", "Unread", None, 0),             # not read yet
]


async def _seed() -> None:
    from web.backend.db import session_scope

    async with session_scope() as s:
        await s.execute(text("TRUNCATE library_tracks RESTART IDENTITY CASCADE"))
        for i, (title, artist, keys, tv) in enumerate(ROWS):
            await s.execute(text(
                "INSERT INTO library_tracks (file_path, title, artist, genre_keys, tags_version) "
                "VALUES (:fp, :t, :a, CAST(:k AS text[]), :tv)"
            ), {"fp": f"/srv/music/g/{i:02d}.mp3", "t": title, "a": artist, "k": keys, "tv": tv})
        await s.commit()


async def _clear() -> None:
    from web.backend.db import session_scope

    async with session_scope() as s:
        await s.execute(text("TRUNCATE library_tracks RESTART IDENTITY CASCADE"))
        await s.commit()


@pytest.fixture
def client():
    from web.backend.main import app

    asyncio.run(_seed())
    try:
        with TestClient(app) as c:
            yield c
    finally:
        asyncio.run(_clear())


@requires_db
def test_genres_list_counts_progress_and_untagged(client) -> None:
    r = client.get("/api/music/genres")
    assert r.status_code == 200, r.text
    d = r.json()
    assert [(g["key"], g["name"], g["track_count"]) for g in d["items"]] == [
        ("hip hop", "Hip Hop", 1), ("r&b", "R&B", 1), ("soul", "Soul", 3),
    ]
    assert d["total"] == 3
    assert (d["tags_read"], d["tags_total"], d["reading"], d["untagged_count"]) == (6, 7, True, 2)
    assert all(isinstance(g["cover_track_id"], int) for g in d["items"])


@requires_db
def test_genres_sorted_by_tracks_searched_and_paged(client) -> None:
    d = client.get("/api/music/genres", params={"sort": "tracks"}).json()
    assert [g["key"] for g in d["items"]] == ["soul", "hip hop", "r&b"]
    d = client.get("/api/music/genres", params={"q": "hip"}).json()
    assert [g["key"] for g in d["items"]] == ["hip hop"]
    first = client.get("/api/music/genres", params={"limit": 2}).json()
    assert first["has_more"] and first["next_cursor"]
    rest = client.get("/api/music/genres", params={"limit": 2, "cursor": first["next_cursor"]}).json()
    assert [g["key"] for g in first["items"] + rest["items"]] == ["hip hop", "r&b", "soul"]
    assert not rest["has_more"]


@requires_db
def test_one_genre_and_its_tracks(client) -> None:
    d = client.get("/api/music/genre", params={"key": "soul"}).json()
    assert (d["name"], d["track_count"], len(d["track_ids"])) == ("Soul", 3, 3)
    page = client.get("/api/music/library", params={"genre": "soul", "sort": "artist"}).json()
    assert [t["artist"] for t in page["items"]] == ["Aretha", "Bill", "Otis"]
    assert page["total"] == 3
    # A track with two genres is under both.
    assert [t["title"] for t in client.get("/api/music/library", params={"genre": "r&b"}).json()["items"]] == ["S2"]


@requires_db
def test_the_no_genre_bucket_is_read_tracks_only(client) -> None:
    d = client.get("/api/music/genre", params={"key": ""}).json()
    assert (d["name"], d["track_count"]) == ("no genre", 2)
    titles = sorted(t["title"] for t in client.get("/api/music/library", params={"genre": ""}).json()["items"])
    assert titles == ["N1", "N2"]                       # U1 is not read yet: not "no genre"


@requires_db
def test_an_unknown_genre_is_a_404(client) -> None:
    assert client.get("/api/music/genre", params={"key": "polka"}).status_code == 404
    assert client.get("/api/music/library", params={"genre": "polka"}).json()["total"] == 0
    assert client.get("/api/music/genre", params={"key": "x" * 101}).status_code == 422


@requires_db
def test_reading_is_done_when_every_track_is_read(client) -> None:
    async def read_all() -> None:
        from web.backend.db import session_scope

        async with session_scope() as s:
            await s.execute(text("UPDATE library_tracks SET tags_version = :v"), {"v": TAGS_VERSION})
            await s.commit()

    asyncio.run(read_all())
    d = client.get("/api/music/genres").json()
    assert (d["tags_read"], d["tags_total"], d["reading"], d["untagged_count"]) == (7, 7, False, 3)


# ─── V052: a genre from MusicBrainz ───────────────────────────────────────

MBID_C = "33333333-3333-4333-8333-333333333333"


@requires_db
async def test_v052_columns_check_and_index(db_session) -> None:
    await db_session.execute(text(
        "INSERT INTO library_tracks (file_path, title) VALUES ('/m/a.mp3', 'A')"))
    row = (await db_session.execute(text(
        "SELECT genre_source, genre_checked_at FROM library_tracks"))).one()
    assert tuple(row) == (None, None)
    names = {r[0] for r in (await db_session.execute(text(
        "SELECT indexname FROM pg_indexes WHERE tablename = 'library_tracks'"))).all()}
    assert "idx_library_tracks_genre_pending" in names
    with pytest.raises(DBAPIError):
        await db_session.execute(text("UPDATE library_tracks SET genre_source = 'guess'"))


@requires_db
async def test_the_fetch_stores_a_musicbrainz_genre_only_where_the_file_has_none(db_session) -> None:
    from domovoi.workers.library_year_fetch import DbYearFetchStore

    await db_session.execute(text(
        "INSERT INTO library_tracks (file_path, title, musicbrainz_recording_id, genre, genre_keys, "
        "genre_source, year, year_source, year_checked_at, tags_version) VALUES "
        "('/m/1.mp3', '1', :a, NULL, NULL, NULL, NULL, NULL, NULL, :v), "
        "('/m/2.mp3', '2', :a, 'Jazz', ARRAY['jazz'], 'tag', NULL, NULL, NULL, :v), "   # a tag genre
        # Already year-checked by the V050 fetch, no genre: asked once more.
        "('/m/3.mp3', '3', :b, NULL, NULL, NULL, 1990, 'musicbrainz', now(), :v), "
        # A year and a genre: never asked.
        "('/m/4.mp3', '4', :c, 'Pop', ARRAY['pop'], 'tag', 2000, 'tag', NULL, :v)"
    ), {"a": MBID_A, "b": MBID_B, "c": MBID_C, "v": TAGS_VERSION})
    await db_session.commit()
    store = DbYearFetchStore()
    assert await store.due(TAGS_VERSION, 25) == [MBID_A, MBID_B]
    await store.record(MBID_A, MbRecordingDetails(year=1959, genres=("hip hop", "r&b"),
                                                  genre_from="recording"))
    await store.record(MBID_B, MbRecordingDetails(year=None, genres=()))
    rows = (await db_session.execute(text(
        "SELECT title, genre, genre_keys, genre_source, year, year_source, "
        "genre_checked_at IS NOT NULL FROM library_tracks ORDER BY title"))).all()
    assert [tuple(r) for r in rows] == [
        ("1", "Hip Hop; R&B", ["hip hop", "r&b"], "musicbrainz", 1959, "musicbrainz", True),
        # The file's genre wins; the year was empty, so it is filled.
        ("2", "Jazz", ["jazz"], "tag", 1959, "musicbrainz", False),
        # Asked: no genre, so marked asked and "no genre" from now on.
        ("3", None, None, None, 1990, "musicbrainz", True),
        ("4", "Pop", ["pop"], "tag", 2000, "tag", False),
    ]
    assert await store.due(TAGS_VERSION, 25) == []
    # Already-asked rows are never asked again; a refusal marks asked too.
    await db_session.execute(text(
        "INSERT INTO library_tracks (file_path, title, musicbrainz_recording_id, tags_version) "
        "VALUES ('/m/5.mp3', '5', :c, :v)"), {"c": MBID_C, "v": TAGS_VERSION})
    await db_session.commit()
    assert await store.due(TAGS_VERSION, 25) == [MBID_C]
    await store.record_refused(MBID_C)
    assert await store.due(TAGS_VERSION, 25) == []


@requires_db
async def test_a_file_genre_replaces_a_musicbrainz_one_and_none_keeps_it(db_session, monkeypatch, tmp_path) -> None:
    """Tag wins: the backfill re-reading a file that now names a genre
    replaces the MusicBrainz genre; a file that still names none keeps it;
    a tag genre the file dropped is gone (and may be asked for)."""
    from domovoi.workers.library_tag_backfill import LibraryTagBackfill

    music = tmp_path / "music"
    tagged = mw.write_mp3(music / "a.mp3", [mw.text_frame("TCON", "Blues")])
    bare = mw.write_mp3(music / "b.mp3", [mw.text_frame("TIT2", "B")])
    dropped = mw.write_mp3(music / "c.mp3", [mw.text_frame("TIT2", "C")])
    monkeypatch.setattr(settings, "music_dir", str(music))
    await db_session.execute(text(
        "INSERT INTO library_tracks (file_path, title, genre, genre_keys, genre_source, "
        "genre_checked_at, musicbrainz_recording_id) VALUES "
        "(:a, 'A', 'Soul', ARRAY['soul'], 'musicbrainz', now(), :m), "
        "(:b, 'B', 'Soul', ARRAY['soul'], 'musicbrainz', now(), :m), "
        "(:c, 'C', 'Rock', ARRAY['rock'], 'tag', NULL, :m)"
    ), {"a": str(tagged), "b": str(bare), "c": str(dropped), "m": MBID_A})
    await db_session.commit()
    assert (await LibraryTagBackfill().tick())["read"] == 3
    rows = (await db_session.execute(text(
        "SELECT title, genre, genre_keys, genre_source FROM library_tracks ORDER BY title"))).all()
    assert [tuple(r) for r in rows] == [
        ("A", "Blues", ["blues"], "tag"),
        ("B", "Soul", ["soul"], "musicbrainz"),
        ("C", None, None, None),
    ]


@requires_db
async def test_the_indexer_marks_a_file_genre_as_tag(db_session, monkeypatch, tmp_path) -> None:
    from domovoi.workers.library_indexer import index_music_dir

    music = tmp_path / "music"
    mw.write_mp3(music / "a.mp3", [mw.text_frame("TIT2", "A"), mw.text_frame("TCON", "Soul")])
    mw.write_mp3(music / "b.mp3", [mw.text_frame("TIT2", "B")])
    monkeypatch.setattr("domovoi.workers.library_indexer.settings.music_dir", str(music))
    assert (await index_music_dir())["inserted"] == 2
    rows = (await db_session.execute(text(
        "SELECT title, genre_source FROM library_tracks ORDER BY title"))).all()
    assert [tuple(r) for r in rows] == [("A", "tag"), ("B", None)]


async def _seed_pending() -> None:
    from web.backend.db import session_scope

    async with session_scope() as s:
        # N1 / N2 (no genre) and U1 (unread) are in ROWS; add two matched
        # songs with no genre: one waiting, one already asked.
        await s.execute(text(
            "INSERT INTO library_tracks (file_path, title, musicbrainz_recording_id, tags_version, "
            "genre_checked_at) VALUES "
            "('/srv/music/g/m1.mp3', 'M1', :a, :v, NULL), "
            "('/srv/music/g/m2.mp3', 'M2', :b, :v, now()), "
            "('/srv/music/g/m3.mp3', 'M3', :c, 0, NULL)"            # not read yet: not counted
        ), {"a": MBID_A, "b": MBID_B, "c": MBID_C, "v": TAGS_VERSION})
        await s.commit()


@requires_db
def test_the_genres_page_counts_songs_waiting_for_musicbrainz(client, monkeypatch) -> None:
    """A matched song with no genre is in the "no genre" row (it has none
    yet) and counted as waiting for MusicBrainz until it has been asked;
    the count is 0 whenever the lookup can't run (song recognition off,
    Internet "never"), so a wait that would never end is never shown."""
    from domovoi import egress

    asyncio.run(_seed_pending())
    monkeypatch.setattr(settings, "library_enricher_enabled", True)
    with egress.override_policy("always"):
        d = client.get("/api/music/genres").json()
        assert (d["untagged_count"], d["musicbrainz_pending"]) == (4, 1)
        monkeypatch.setattr(settings, "library_enricher_enabled", False)
        assert client.get("/api/music/genres").json()["musicbrainz_pending"] == 0
    monkeypatch.setattr(settings, "library_enricher_enabled", True)
    with egress.override_policy("never"):
        d = client.get("/api/music/genres").json()
        assert (d["untagged_count"], d["musicbrainz_pending"]) == (4, 0)
