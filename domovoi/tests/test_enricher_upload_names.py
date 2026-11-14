"""The enricher's agreement check against upload-style names, the repair
that only acts on a real disagreement, and the undo for a repair that
marked good matches ``disagreed`` (2026-11-14).

On a real 6,302-track library the 2026-11-13 repair rewrote 184 rows. Many
files carry a video upload's names: the tags say "Bruno Mars - That's what
I like (Lyrics)" by "Pizza Music" (a channel), or an untagged file name was
read the wrong way round ("Longview - Green Day   Lyrics.mp3" as the
artist "Longview"). The right enriched names ("That's What I Like" by
Bruno Mars) didn't agree with those by title or artist alone, so the
repair put the upload's names back and threw away the MusicBrainz id, year
and genre. Every example below is one of those rows.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from domovoi.tests.conftest import requires_db
from domovoi.tests.enrich_outcome_testkit import apply_v020
from domovoi.workers import library_enricher as le
from domovoi.workers.library_enricher import (
    EnrichmentResult,
    Lookup,
    Verdict,
    enrich_library,
    looks_like_upload_names,
    names_agree,
    repair_disagreeing,
    requeue_disagreed,
    strip_upload_noise,
)

ENRICHER = "domovoi.workers.library_enricher"

# (the file's title, the file's artist, the right song's title, its artist):
# the file's names are what the faulty repair restored, the right ones what
# song recognition had written before it.
UPLOADS = [
    ("Bruno Mars - That’s what I like (Lyrics)", "Pizza Music", "That's What I Like", "Bruno Mars"),
    ("Alanis Morissette - Hand In My Pocket (Lyrics)", "Lyrics 90's",
     "Hand in My Pocket", "Alanis Morissette"),
    ("Alice Deejay - Better Off Alone (Lyrics)", "Mixed Lyrics", "Better Off Alone", "Alice Deejay"),
    ("Corona - The Rhythm Of The Night (Lyric Video)", "Audioandlyrics",
     "The Rhythm of the Night", "Corona"),
    ("Lauren Sanderson feat. Fred Durst - Come Say Sum (Lyrics)", "eX Monstars",
     "Come Say Sum", "Lauren Sanderson feat. Fred Durst"),
    ("La Bouche - Be My Lover (Official Video)", "LaBoucheVEVO", "Be My Lover", "La Bouche"),
    ("Lucky Ass Bitch - Mac Miller ft. Juicy J (Official Audio)", "rostrumrecords",
     "Lucky Ass Bitch", "Mac Miller feat. Juicy J"),
    ("Supermarket Flowers Lyrics - Ed Sheeran", "Dear Tunes", "Supermarket Flowers", "Ed Sheeran"),
    ("There Goes My Life  Lyrics- Kenny Chesney", "Alyssa M. Hughes",
     "There Goes My Life", "Kenny Chesney"),
]
# Untagged files whose name was read "Artist - Title" the wrong way round:
# (file name, title read, artist read, right title, right artist).
REVERSED = [
    ("Longview - Green Day   Lyrics.mp3", "Green Day   Lyrics", "Longview", "Longview", "Green Day"),
    ("Last Night - Morgan Wallen  Lyric Video.mp3", "Morgan Wallen  Lyric Video", "Last Night",
     "Last Night", "Morgan Wallen"),
    ("Tokyo Drift - Six Days - Mos Def ft. DJ Shadow HD Video.mp3",
     "Six Days - Mos Def ft. DJ Shadow HD Video", "Tokyo Drift",
     "Six Days", "DJ Shadow feat. Mos Def"),
    ("OFFICIAL Somewhere over the Rainbow - Israel _IZ_ Kamakawiwoʻole.mp3",
     "Israel _IZ_ Kamakawiwoʻole", "OFFICIAL Somewhere over the Rainbow",
     "Somewhere Over the Rainbow", "Israel Kamakawiwoʻole"),
    ("Tiptoe through the tulips- Tiny Tim LYRICS ON SCREEN.mp3", "Tiny Tim LYRICS ON SCREEN",
     "Tiptoe through the tulips", "Tiptoe Through the Tulips", "Tiny Tim"),
    ("Long Live A$AP - ASAP Rocky (Official Music Video) - Dirty.mp3",
     "ASAP Rocky (Official Music Video) - Dirty", "Long Live A$AP", "Long Live A$AP", "A$AP Rocky"),
]
# Rows the repair rewrote although file and song plainly agree: (file
# name, the file's title, the file's artist, the song's title, its artist).
PLAIN = [
    ("Stan [S-smUALTlFM].mp3", "Stan", "Eminem, Dido", "Stan", "Eminem feat. Dido"),
    ("The Real Slim Shady [rkqMbsmLrtA].mp3", "The Real Slim Shady", "Eminem",
     "The Real Slim Shady", "Eminem"),
    ("Party Up [cNYUnKMCa7Y].mp3", "Party Up", "DMX", "Party Up (Up in Here)", "DMX"),
    ("Free Fallin' [1lWJXDG2i0A].mp3", "Free Fallin'", "Tom Petty",
     "Free Fallin’ (2004 Remaster)", "Tom Petty"),
    ("Radiohead - Creep.mp3", "Creep", "Radiohead", "Creep", "Radiohead"),
    ("Ed Sheeran - Shivers [Official Video].mp3", "Shivers", "Ed Sheeran", "Shivers", "Ed Sheeran"),
]
# The five answers the 2026-11-13 rule was written to refuse.
OBSERVED = [
    ("In the End", "Linkin Park", "Linkin Park", "Boomin"),
    ("Inconsolable", "Backstreet Boys", "Backstreet Boys", "Totally Insane"),
    ("Secrets", "The Weeknd", "The Weeknd", "Witch House"),
    ("Bad Company", "Five Finger Death Punch", "Five Finger Death Punch", "Norin & Rad"),
    ("Abortion", "Lil Wayne", "Lil Wayne", "Caotico"),
]


# ═══ The rule (pure) ══════════════════════════════════════════════════════


@pytest.mark.parametrize(("ot", "oa", "tt", "ta"), UPLOADS)
def test_an_uploads_names_agree_with_the_song_they_carry(ot, oa, tt, ta) -> None:
    assert names_agree(ot, oa, tt, ta) is True


@pytest.mark.parametrize(("_fn", "ot", "oa", "tt", "ta"), REVERSED)
def test_a_file_name_read_the_wrong_way_round_agrees(_fn, ot, oa, tt, ta) -> None:
    assert names_agree(ot, oa, tt, ta) is True


@pytest.mark.parametrize(("_fn", "ot", "oa", "tt", "ta"), PLAIN)
def test_plainly_agreeing_names_agree_both_ways(_fn, ot, oa, tt, ta) -> None:
    assert names_agree(ot, oa, tt, ta) is True
    assert names_agree(tt, ta, ot, oa) is True


@pytest.mark.parametrize(("ot", "oa", "tt", "ta"), OBSERVED)
def test_the_five_observed_answers_are_still_refused(ot, oa, tt, ta) -> None:
    assert names_agree(ot, oa, tt, ta) is False
    # Upload noise and the file's name don't let them through either.
    assert names_agree(f"{ot} (Official Video)", oa, tt, ta) is False
    assert names_agree(ot, oa, tt, ta, our_extra=f"{oa} - {ot} [abcdefghijk]") is False


def test_a_title_with_the_right_words_by_a_stranger_is_refused() -> None:
    # Both names must be in ours: the right title by someone else isn't.
    assert names_agree(
        "Bruno Mars - That’s what I like (Lyrics)", "Pizza Music",
        "That's What I Like", "Some Karaoke Band",
    ) is False
    assert names_agree(
        "Corona - The Rhythm Of The Night (Lyric Video)", "Audioandlyrics",
        "Corona", "DJ Somebody",
    ) is False


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("Green Day   Lyrics", "Green Day"),
        ("Supermarket Flowers Lyrics - Ed Sheeran", "Supermarket Flowers - Ed Sheeran"),
        ("Tiny Tim LYRICS ON SCREEN", "Tiny Tim"),
        ("Six Days - Mos Def ft. DJ Shadow HD Video", "Six Days - Mos Def ft. DJ Shadow"),
        ("OFFICIAL Somewhere over the Rainbow", "Somewhere over the Rainbow"),
        ("Free Fallin' (2009 Remaster)", "Free Fallin'"),
        ("Stan [S-smUALTlFM]", "Stan"),
        ("Hey Ya! (Radio Edit) [Explicit]", "Hey Ya!"),
        # a real title that merely contains "video" keeps it
        ("Video Killed the Radio Star", "Video Killed the Radio Star"),
        ("Lyrics", "Lyrics"),
    ],
)
def test_upload_noise_is_stripped(raw, clean) -> None:
    assert strip_upload_noise(raw) == clean


@pytest.mark.parametrize(("_fn", "ot", "oa", "_tt", "_ta"), REVERSED)
def test_reversed_upload_file_names_look_like_uploads(_fn, ot, oa, _tt, _ta) -> None:
    assert looks_like_upload_names(ot, oa) is True


@pytest.mark.parametrize(("ot", "oa", "_tt", "_ta"), UPLOADS)
def test_upload_tags_look_like_uploads(ot, oa, _tt, _ta) -> None:
    assert looks_like_upload_names(ot, oa) is True


@pytest.mark.parametrize(
    ("title", "artist"),
    [("In the End", "Linkin Park"), ("Creep", "Radiohead"), ("Stan", "Eminem, Dido"),
     ("Video Killed the Radio Star", "The Buggles"), ("Since You Been Gone", "Rainbow")],
)
def test_song_names_dont_look_like_uploads(title, artist) -> None:
    assert looks_like_upload_names(title, artist) is False


# ═══ The repair and the undo (database) ═══════════════════════════════════


@pytest.fixture
async def lane(db_session, monkeypatch):
    await apply_v020()
    monkeypatch.setattr(f"{ENRICHER}.settings.library_enricher_enabled", True)
    monkeypatch.setattr(f"{ENRICHER}.settings.library_enricher_delay_sec", 0)
    monkeypatch.setattr(f"{ENRICHER}.provider_available", lambda: True)
    yield db_session


async def _insert(session, fp: str, title, artist, **cols) -> int:
    names = ["file_path", "title", "artist", *cols]
    params = {"file_path": fp, "title": title, "artist": artist, **cols}
    row = (await session.execute(
        text(
            f"INSERT INTO library_tracks ({', '.join(names)}) "
            f"VALUES ({', '.join(':' + n for n in names)}) RETURNING id"
        ),
        params,
    )).first()
    await session.commit()
    return int(row[0])


async def _get(session, track_id: int):
    return (await session.execute(
        text(
            "SELECT title, artist, musicbrainz_recording_id, enrich_outcome, enriched_at, "
            "year, year_source, year_checked_at, genre_checked_at "
            "FROM library_tracks WHERE id = :id"
        ),
        {"id": track_id},
    )).first()


def _tags(by_name: dict[str, tuple[str, str]]):
    """A read_metadata that gives each file the tags in ``by_name`` and
    reads any other file's name the way the indexer does."""
    from domovoi.workers import library_indexer

    real = library_indexer._resolve_metadata_for

    async def read(path: Path, known_artists=None):
        if path.name in by_name:
            t, a = by_name[path.name]
            return {"title": t, "artist": a, "album": None}
        return real(path, known_artists)

    return read


@requires_db
@pytest.mark.asyncio
async def test_repair_keeps_every_real_example(lane, monkeypatch, tmp_path) -> None:
    tags: dict[str, tuple[str, str]] = {}
    expected: dict[int, tuple[str, str]] = {}
    for i, (ot, oa, tt, ta) in enumerate(UPLOADS):
        f = tmp_path / f"upload{i}.mp3"
        f.write_bytes(b"")
        tags[f.name] = (ot, oa)
        expected[await _insert(
            lane, str(f), tt, ta, musicbrainz_recording_id=f"mb-u{i}",
            enrich_outcome="matched", year=2000, year_source="musicbrainz",
        )] = (tt, ta)
    for i, (fn, _ot, _oa, tt, ta) in enumerate(REVERSED):
        f = tmp_path / fn
        f.write_bytes(b"")   # untagged: the file name is read
        expected[await _insert(
            lane, str(f), tt, ta, musicbrainz_recording_id=f"mb-r{i}", enrich_outcome="matched",
        )] = (tt, ta)
    for i, (fn, ot, oa, tt, ta) in enumerate(PLAIN):
        f = tmp_path / fn
        f.write_bytes(b"")
        if "[" in fn:
            tags[f.name] = (ot, oa)
        expected[await _insert(
            lane, str(f), tt, ta, musicbrainz_recording_id=f"mb-p{i}", enrich_outcome="matched",
        )] = (tt, ta)
    await lane.execute(text("UPDATE library_tracks SET enriched_at = NOW()"))
    await lane.commit()
    monkeypatch.setattr("domovoi.workers.library_indexer.read_metadata", _tags(tags))

    counts = await repair_disagreeing()

    assert counts["restored"] == 0, counts
    for track_id, names in expected.items():
        row = await _get(lane, track_id)
        assert (row.title, row.artist) == names
        assert row.enrich_outcome == "matched" and row.musicbrainz_recording_id


@requires_db
@pytest.mark.asyncio
async def test_repair_never_puts_upload_names_over_a_row(lane, monkeypatch, tmp_path) -> None:
    # A real disagreement, but the file's names are an upload's: unsure,
    # left as it is.
    up = tmp_path / "upload.mp3"
    clean = tmp_path / "Linkin Park - In the End.mp3"
    for p in (up, clean):
        p.write_bytes(b"")
    unsure = await _insert(lane, str(up), "Toxic", "Britney Spears",
                           musicbrainz_recording_id="mb-x", enrich_outcome="matched")
    wrong = await _insert(lane, str(clean), "Linkin Park", "Boomin",
                          musicbrainz_recording_id="mb-wrong", enrich_outcome="matched")
    await lane.execute(text("UPDATE library_tracks SET enriched_at = NOW()"))
    await lane.commit()
    monkeypatch.setattr(
        "domovoi.workers.library_indexer.read_metadata",
        _tags({up.name: ("Linkin Park - In the End (Official Video)", "Linkin Park")}),
    )

    counts = await repair_disagreeing()

    assert (counts["restored"], counts["unsure"]) == (1, 1)
    kept = await _get(lane, unsure)
    assert (kept.title, kept.artist, kept.enrich_outcome) == ("Toxic", "Britney Spears", "matched")
    fixed = await _get(lane, wrong)
    assert (fixed.title, fixed.artist, fixed.enrich_outcome) == ("In the End", "Linkin Park", "disagreed")


WINDOW = (datetime(2026, 11, 14, 5, 26, tzinfo=timezone.utc),
          datetime(2026, 11, 14, 5, 30, tzinfo=timezone.utc))


@requires_db
@pytest.mark.asyncio
async def test_requeue_puts_back_the_window_and_a_sweep_rematches(lane, monkeypatch, tmp_path) -> None:
    f = tmp_path / "That’s what I like.mp3"
    f.write_bytes(b"")
    inside = WINDOW[0].replace(minute=27)
    before = WINDOW[0].replace(minute=10)
    damaged = await _insert(
        lane, str(f), "Bruno Mars - That’s what I like (Lyrics)", "Pizza Music",
        enrich_outcome="disagreed", enriched_at=inside,
        year_checked_at=inside, genre_checked_at=inside,
    )
    earlier = await _insert(lane, str(tmp_path / "a.mp3"), "A", "B",
                            enrich_outcome="disagreed", enriched_at=before)
    manual = await _insert(lane, str(tmp_path / "b.mp3"), "C", "D",
                           enrich_outcome="manual", enriched_at=inside)
    matched = await _insert(lane, str(tmp_path / "c.mp3"), "E", "F", musicbrainz_recording_id="mb-e",
                            enrich_outcome="matched", enriched_at=inside)
    at_until = await _insert(lane, str(tmp_path / "d.mp3"), "G", "H",
                             enrich_outcome="disagreed", enriched_at=WINDOW[1])

    out = await requeue_disagreed(*WINDOW)
    assert out == {"requeued": 1, "skipped": ""}

    row = await _get(lane, damaged)
    assert row.enriched_at is None and row.enrich_outcome is None
    assert row.year_checked_at is None and row.genre_checked_at is None   # asked again
    for other, outcome in ((earlier, "disagreed"), (manual, "manual"), (matched, "matched"),
                           (at_until, "disagreed")):
        r = await _get(lane, other)
        assert r.enrich_outcome == outcome and r.enriched_at is not None

    async def ac(p):
        return Lookup(Verdict.MATCH, result=EnrichmentResult(
            title="That's What I Like", artist="Bruno Mars",
            musicbrainz_recording_id="mb-bruno", source="acoustid"))

    async def no_shazam(_p):
        return Lookup(Verdict.NOT_ASKED, detail="shazamio not installed")

    monkeypatch.setattr(f"{ENRICHER}._enrich_via_acoustid", ac)
    monkeypatch.setattr(f"{ENRICHER}._enrich_via_shazam", no_shazam)
    counts = await enrich_library()
    assert (counts["scanned"], counts["matched"]) == (1, 1)
    row = await _get(lane, damaged)
    assert (row.title, row.artist, row.musicbrainz_recording_id, row.enrich_outcome) == (
        "That's What I Like", "Bruno Mars", "mb-bruno", "matched")


@pytest.mark.asyncio
async def test_requeue_waits_for_a_running_sweep(monkeypatch) -> None:
    monkeypatch.setattr(le, "_sweep_running", True)
    assert (await requeue_disagreed(*WINDOW)) == {"requeued": 0, "skipped": "running"}


@requires_db
@pytest.mark.asyncio
async def test_admin_repair_route_requeues_a_window(monkeypatch) -> None:
    from httpx import ASGITransport, AsyncClient

    from domovoi import main as core

    requeue = AsyncMock(return_value={"requeued": 184, "skipped": ""})
    repair = AsyncMock(return_value={})
    monkeypatch.setattr(le, "requeue_disagreed", requeue)
    monkeypatch.setattr(le, "repair_disagreeing", repair)
    monkeypatch.setattr(core, "admin_library_enrich", AsyncMock(return_value={"queued": True}))
    async with core.app.router.lifespan_context(core.app):
        async with AsyncClient(transport=ASGITransport(app=core.app), base_url="http://test") as client:
            r = await client.post("/v1/admin/library/enrich/repair", json={"requeue_disagreed": {
                "since": "2026-11-14T05:26:00Z", "until": "2026-11-14T05:30:00Z"}})
            assert r.status_code == 200, r.text
            assert r.json() == {"requeued": 184, "sweep": "queued"}
            since, until = requeue.await_args.args
            assert (since, until) == WINDOW
            repair.assert_not_awaited()

            for bad in (
                {"requeue_disagreed": {"since": "2026-11-14T05:30:00Z",
                                       "until": "2026-11-14T05:26:00Z"}},
                {"requeue_disagreed": {"since": "2026-11-14T05:26:00",
                                       "until": "2026-11-14T05:30:00"}},   # no timezone
                {"requeue_disagreed": {"since": "2026-11-14T05:26:00Z"}},
                {"something_else": True},
            ):
                assert (await client.post("/v1/admin/library/enrich/repair", json=bad)).status_code == 422
            assert requeue.await_count == 1

            # No body (or {}): the repair, as before.
            r = await client.post("/v1/admin/library/enrich/repair", json={})
            assert r.json() == {"queued": True, "worker": "library_enricher_repair"}
