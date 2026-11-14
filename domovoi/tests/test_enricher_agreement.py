"""The enricher's agreement check, its repair, and the indexer's
"Title - Artist" file-name reading (2026-11-13).

A library sweep once renamed songs to fingerprint answers that shared
nothing with what the file said: "In the End" by Linkin Park became the
title "Linkin Park" by "Boomin". These tests hold:

* the five observed answers are refused, an agreeing one is used, and
  AcoustID's later candidates are looked at, not just the first;
* a refused answer keeps the row's names and stamps ``disagreed``;
* :func:`repair_disagreeing` puts the file's names back on an already
  damaged row, and never touches a row a person edited (``manual``);
* an untagged "A - B" file name is read "Title - Artist" when B is an
  artist the library has and A isn't, and "Artist - Title" otherwise.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

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
    file_names,
    names_agree,
    repair_disagreeing,
)
from domovoi.workers.library_indexer import (
    _fallback_filename_parse,
    known_artist_keys_of,
)

ENRICHER = "domovoi.workers.library_enricher"

# (our title, our artist, their title, their artist): what the file said,
# and what the 2026-11-12 sweep wrote over it.
OBSERVED = [
    ("In the End", "Linkin Park", "Linkin Park", "Boomin"),
    ("Inconsolable", "Backstreet Boys", "Backstreet Boys", "Totally Insane"),
    ("Secrets (Official Video)", "The Weeknd", "The Weeknd", "Witch House"),
    ("Bad Company", "Five Finger Death Punch", "Five Finger Death Punch", "Norin & Rad"),
    ("Abortion", "Lil Wayne", "Lil Wayne", "Caotico"),
]


# ═══ The rule (pure) ══════════════════════════════════════════════════════


@pytest.mark.parametrize(("ot", "oa", "tt", "ta"), OBSERVED)
def test_the_five_observed_answers_are_refused(ot, oa, tt, ta) -> None:
    assert names_agree(ot, oa, tt, ta) is False


@pytest.mark.parametrize(
    ("ot", "oa", "tt", "ta"),
    [
        ("In the End", "Linkin Park", "In The End", "Linkin Park"),
        # decoration, case and a feature on either side
        ("Secrets (Official Video)", "The Weeknd", "Secrets", "The Weeknd"),
        ("In the End (Live)", "LINKIN PARK", "In the End", "Linkin Park feat. Someone"),
        # a small spelling difference in the artist, same title
        ("Bad Company", "Five Finger Death Punch", "Bad Company", "Five Fingers Death Punch"),
        # the artist agrees, the title is the catalog's own
        ("Track 3", "Lil Wayne", "Abortion", "Lil Wayne"),
        # the title agrees and the file named no artist
        ("Inconsolable", None, "Inconsolable", "Backstreet Boys"),
        # one performer of a multi-artist credit
        ("I against I", "Massive Attack", "I Against I", "Massive Attack & Mos Def"),
    ],
)
def test_an_agreeing_answer_is_used(ot, oa, tt, ta) -> None:
    assert names_agree(ot, oa, tt, ta) is True


def test_our_names_the_other_way_round_agree() -> None:
    # "I against I - Massive Attack.mp3" read Artist - Title before the
    # indexer knew better: the answer is the same two names swapped.
    assert names_agree("Massive Attack", "I against I", "I Against I", "Massive Attack") is True


def test_their_title_being_our_artist_is_refused_even_when_titles_match() -> None:
    # A self-titled song: the title alone agrees, but the answer's title is
    # our ARTIST and its artist is someone else, the observed failure.
    assert names_agree("Weezer", "Weezer", "Weezer", "Witch House") is False
    assert names_agree("Weezer", "Weezer", "Weezer", "Weezer") is True


def test_a_wrong_song_is_refused() -> None:
    assert names_agree("In the End", "Linkin Park", "Toxic", "Britney Spears") is False


def test_no_names_means_no_check() -> None:
    assert names_agree(None, None, "Anything", "Anyone") is True
    assert names_agree("", "  ", "Anything", "Anyone") is True


def test_file_names_treats_a_bare_file_name_as_no_names() -> None:
    assert file_names("/m/01 track.mp3", "01 track", None) == (None, None)
    assert file_names("/m/01 track.mp3", None, None) == (None, None)
    assert file_names("/m/01 track.mp3", "In the End", None) == ("In the End", None)
    assert file_names("/m/x.mp3", "x", "Linkin Park") == ("x", "Linkin Park")


# ═══ Providers (fake answers) ═════════════════════════════════════════════


def _acoustid_answer(*recordings: tuple[float, str, str, str]) -> dict:
    """An AcoustID lookup answer (parse=False) with one result per
    (score, recording id, title, artist)."""
    return {
        "status": "ok",
        "results": [
            {"score": score, "id": f"ac-{rid}",
             "recordings": [{"id": rid, "title": title, "artists": [{"name": artist}]}]}
            for score, rid, title, artist in recordings
        ],
    }


@pytest.fixture
def acoustid_key(monkeypatch):
    monkeypatch.setattr(f"{ENRICHER}.settings.acoustid_api_key", "app-key")
    monkeypatch.setattr(f"{ENRICHER}.settings.library_enricher_acoustid_min_score", 0.7)


async def _no_shazam(_p):
    return Lookup(Verdict.NOT_ASKED, detail="shazamio not installed")


@pytest.mark.asyncio
async def test_acoustid_candidates_beyond_the_first_are_chosen(acoustid_key, monkeypatch, tmp_path) -> None:
    answer = _acoustid_answer(
        (0.98, "r-wrong", "Linkin Park", "Boomin"),
        (0.95, "r-right", "In the End", "Linkin Park"),
        (0.50, "r-low", "In the End", "Linkin Park"),
    )
    monkeypatch.setattr(f"{ENRICHER}._enrich_via_shazam", _no_shazam)
    with patch("acoustid.match", lambda *_a, **_kw: answer):
        raw = await le._enrich_via_acoustid(tmp_path / "x.mp3")
        out = await le._enrich_one(tmp_path / "x.mp3", le._Providers(), ("In the End", "Linkin Park"))
    # Every candidate at or above the score, best first; the low one is out.
    assert [c.musicbrainz_recording_id for c in raw.candidates] == ["r-wrong", "r-right"]
    assert out.verdict is Verdict.MATCH
    assert (out.result.title, out.result.artist, out.result.musicbrainz_recording_id) == (
        "In the End", "Linkin Park", "r-right",
    )


@pytest.mark.asyncio
async def test_without_names_the_best_scoring_candidate_wins_as_before(acoustid_key, monkeypatch, tmp_path) -> None:
    answer = _acoustid_answer((0.9, "r-b", "B", "Y"), (0.97, "r-a", "A", "X"))
    monkeypatch.setattr(f"{ENRICHER}._enrich_via_shazam", _no_shazam)
    with patch("acoustid.match", lambda *_a, **_kw: answer):
        out = await le._enrich_one(tmp_path / "x.mp3", le._Providers(), None)
    assert out.verdict is Verdict.MATCH and out.result.musicbrainz_recording_id == "r-a"


@pytest.mark.asyncio
async def test_no_agreeing_candidate_is_disagreed(acoustid_key, monkeypatch, tmp_path) -> None:
    answer = _acoustid_answer((0.98, "r-wrong", "Linkin Park", "Boomin"))
    monkeypatch.setattr(f"{ENRICHER}._enrich_via_shazam", _no_shazam)
    with patch("acoustid.match", lambda *_a, **_kw: answer):
        out = await le._enrich_one(tmp_path / "x.mp3", le._Providers(), ("In the End", "Linkin Park"))
    assert out.verdict is Verdict.DISAGREED


@pytest.mark.asyncio
async def test_shazam_is_asked_after_acoustid_disagrees(monkeypatch) -> None:
    async def ac(_p):
        return Lookup(Verdict.MATCH, result=EnrichmentResult(
            title="Linkin Park", artist="Boomin", musicbrainz_recording_id="mb-x", source="acoustid"))

    async def sh(_p):
        return Lookup(Verdict.MATCH, result=EnrichmentResult(
            title="In the End", artist="Linkin Park", album="Hybrid Theory", source="shazam"))

    monkeypatch.setattr(f"{ENRICHER}._enrich_via_acoustid", ac)
    monkeypatch.setattr(f"{ENRICHER}._enrich_via_shazam", sh)
    out = await le._enrich_one(Path("x.mp3"), le._Providers(), ("In the End", "Linkin Park"))
    assert out.verdict is Verdict.MATCH and out.result.source == "shazam"


@pytest.mark.asyncio
async def test_a_disagreeing_shazam_answer_is_disagreed(monkeypatch) -> None:
    async def sh(_p):
        return Lookup(Verdict.MATCH, result=EnrichmentResult(
            title="The Weeknd", artist="Witch House", source="shazam"))

    monkeypatch.setattr(f"{ENRICHER}._enrich_via_acoustid", _no_shazam)
    monkeypatch.setattr(f"{ENRICHER}._enrich_via_shazam", sh)
    out = await le._enrich_one(Path("x.mp3"), le._Providers(), ("Secrets", "The Weeknd"))
    assert out.verdict is Verdict.DISAGREED


@pytest.mark.asyncio
async def test_disagreed_with_an_error_is_an_error_not_a_stop(monkeypatch) -> None:
    async def ac(_p):
        return Lookup(Verdict.MATCH, result=EnrichmentResult(title="Lil Wayne", artist="Caotico"))

    async def sh(_p):
        return Lookup(Verdict.ERROR, detail="down", transient=True)

    monkeypatch.setattr(f"{ENRICHER}._enrich_via_acoustid", ac)
    monkeypatch.setattr(f"{ENRICHER}._enrich_via_shazam", sh)
    out = await le._enrich_one(Path("x.mp3"), le._Providers(), ("Abortion", "Lil Wayne"))
    # Not decided (retried), but a provider DID answer: not a line failure.
    assert out.verdict is Verdict.ERROR and out.transient is False


# ═══ The sweep and the repair (DB) ════════════════════════════════════════


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
            "SELECT title, artist, album, musicbrainz_recording_id, enrich_outcome, "
            "year, year_source FROM library_tracks WHERE id = :id"
        ),
        {"id": track_id},
    )).first()


@requires_db
@pytest.mark.asyncio
async def test_the_sweep_keeps_names_a_provider_disagrees_with(lane, monkeypatch, tmp_path) -> None:
    f = tmp_path / "In the End [abc].mp3"
    f.write_bytes(b"")
    g = tmp_path / "Toxic.mp3"
    g.write_bytes(b"")
    wrong = await _insert(lane, str(f), "In the End", "Linkin Park", album="Hybrid Theory")
    right = await _insert(lane, str(g), "Toxic", "Britney Spears")

    async def ac(p):
        if p.name == f.name:
            return Lookup(Verdict.MATCH, result=EnrichmentResult(
                title="Linkin Park", artist="Boomin", musicbrainz_recording_id="mb-wrong",
                source="acoustid"))
        return Lookup(Verdict.MATCH, result=EnrichmentResult(
            title="Toxic", artist="Britney Spears", musicbrainz_recording_id="mb-toxic",
            source="acoustid"))

    monkeypatch.setattr(f"{ENRICHER}._enrich_via_acoustid", ac)
    monkeypatch.setattr(f"{ENRICHER}._enrich_via_shazam", _no_shazam)
    counts = await enrich_library()
    assert (counts["matched"], counts["disagreed"]) == (1, 1)

    kept = await _get(lane, wrong)
    assert (kept.title, kept.artist, kept.album) == ("In the End", "Linkin Park", "Hybrid Theory")
    assert kept.musicbrainz_recording_id is None and kept.enrich_outcome == "disagreed"
    matched = await _get(lane, right)
    assert (matched.musicbrainz_recording_id, matched.enrich_outcome) == ("mb-toxic", "matched")
    # Stamped: the next sweep doesn't ask about it again.
    again = await enrich_library()
    assert again["scanned"] == 0


@requires_db
@pytest.mark.asyncio
async def test_repair_restores_the_files_names_and_leaves_manual_rows(lane, tmp_path) -> None:
    # Untagged files: their names come from the file name, as the indexer
    # reads it.
    damaged_f = tmp_path / "Linkin Park - In the End.mp3"
    manual_f = tmp_path / "Lil Wayne - Abortion.mp3"
    fine_f = tmp_path / "Britney Spears - Toxic.mp3"
    bare_f = tmp_path / "track01.mp3"
    for p in (damaged_f, manual_f, fine_f, bare_f):
        p.write_bytes(b"")

    damaged = await _insert(
        lane, str(damaged_f), "Linkin Park", "Boomin", album="Hybrid Theory",
        musicbrainz_recording_id="1b276e1a", enrich_outcome="matched",
        year=2019, year_source="musicbrainz",
    )
    manual = await _insert(
        lane, str(manual_f), "Lil Wayne", "Caotico", enrich_outcome="manual",
    )
    fine = await _insert(
        lane, str(fine_f), "Toxic", "Britney Spears", musicbrainz_recording_id="mb-toxic",
        enrich_outcome="matched",
    )
    bare = await _insert(
        lane, str(bare_f), "Some Catalog Title", "Someone", enrich_outcome="matched",
    )
    gone = await _insert(
        lane, str(tmp_path / "gone.mp3"), "X", "Y", enrich_outcome="matched",
    )
    # Every judged row was stamped by the sweep that judged it.
    await lane.execute(text("UPDATE library_tracks SET enriched_at = NOW()"))
    await lane.commit()

    counts = await repair_disagreeing()

    assert counts["skipped"] == ""
    assert (counts["checked"], counts["restored"], counts["kept"], counts["unjudged"],
            counts["missing_file"]) == (4, 1, 1, 1, 1)

    row = await _get(lane, damaged)
    assert (row.title, row.artist, row.album) == ("In the End", "Linkin Park", "Hybrid Theory")
    assert row.musicbrainz_recording_id is None and row.enrich_outcome == "disagreed"
    assert row.year is None and row.year_source is None   # fetched by the wrong id

    hand = await _get(lane, manual)
    assert (hand.title, hand.artist, hand.enrich_outcome) == ("Lil Wayne", "Caotico", "manual")
    ok = await _get(lane, fine)
    assert (ok.title, ok.musicbrainz_recording_id, ok.enrich_outcome) == ("Toxic", "mb-toxic", "matched")
    unjudged = await _get(lane, bare)
    assert (unjudged.title, unjudged.enrich_outcome) == ("Some Catalog Title", "matched")
    assert (await _get(lane, gone)).enrich_outcome == "matched"

    # A second run finds nothing more to do: the restored row is no longer
    # a match, and no sweep picks it up again.
    again = await repair_disagreeing()
    assert again["restored"] == 0
    assert (await enrich_library())["scanned"] == 0


@pytest.mark.asyncio
async def test_repair_waits_for_a_running_sweep(monkeypatch) -> None:
    monkeypatch.setattr(le, "_sweep_running", True)
    out = await repair_disagreeing()
    assert out["skipped"] == "running" and out["checked"] == 0


@requires_db
@pytest.mark.asyncio
async def test_admin_repair_route_queues_the_repair(monkeypatch) -> None:
    from httpx import ASGITransport, AsyncClient

    from domovoi.main import app

    repair = AsyncMock(return_value={})
    monkeypatch.setattr(le, "repair_disagreeing", repair)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/v1/admin/library/enrich/repair")
            assert r.status_code == 200
            assert r.json() == {"queued": True, "worker": "library_enricher_repair"}
            monkeypatch.setattr(le, "_sweep_running", True)
            busy = await client.post("/v1/admin/library/enrich/repair")
            assert busy.json() == {"queued": False, "reason": "running"}


# ═══ File-name order (pure) ═══════════════════════════════════════════════


KNOWN = known_artist_keys_of(["Massive Attack", "Linkin Park", "Five Finger Death Punch"])


def test_title_then_known_artist_is_read_title_artist() -> None:
    artist, title = _fallback_filename_parse(
        Path("I against I - Massive Attack & Mos Def (Blade II version).mp3"), KNOWN,
    )
    assert artist == "Massive Attack & Mos Def"
    assert title == "I against I (Blade II version)"


def test_known_artist_then_title_is_still_artist_title() -> None:
    assert _fallback_filename_parse(Path("Linkin Park - In the End.mp3"), KNOWN) == (
        "Linkin Park", "In the End",
    )
    assert _fallback_filename_parse(Path("Five Finger Death Punch- Bad Company.mp3"), KNOWN) == (
        "Five Finger Death Punch", "Bad Company",
    )


def test_unknown_artists_keep_artist_title() -> None:
    assert _fallback_filename_parse(Path("Akon - Sunny Day.mp3"), KNOWN) == ("Akon", "Sunny Day")
    # Without the library's artists the reading is as it always was.
    assert _fallback_filename_parse(
        Path("I against I - Massive Attack & Mos Def (Blade II version).mp3"),
    ) == ("I against I", "Massive Attack & Mos Def")


def test_upload_decoration_is_not_kept_on_a_swapped_title() -> None:
    assert _fallback_filename_parse(
        Path("In the End - Linkin Park (Official Video).mp3"), KNOWN,
    ) == ("Linkin Park", "In the End")


def test_both_sides_known_stays_artist_title() -> None:
    known = known_artist_keys_of(["Massive Attack", "Linkin Park"])
    assert _fallback_filename_parse(Path("Linkin Park - Massive Attack.mp3"), known) == (
        "Linkin Park", "Massive Attack",
    )


def test_no_artist_names_are_not_known_artists() -> None:
    assert known_artist_keys_of(["Unknown Artist", "Various Artists", None, ""]) == frozenset()


@requires_db
@pytest.mark.asyncio
async def test_the_indexer_sweep_reads_title_artist_by_the_librarys_artists(
    db_session, monkeypatch, tmp_path,
) -> None:
    from domovoi.workers.library_indexer import index_music_dir

    music_dir = tmp_path / "music"
    music_dir.mkdir()
    (music_dir / "Mezzanine - Massive Attack.mp3").write_bytes(b"")   # Title - Artist
    (music_dir / "Akon - Sunny Day.mp3").write_bytes(b"")             # Artist - Title
    await db_session.execute(text(
        "INSERT INTO library_tracks (file_path, title, artist) "
        "VALUES ('/elsewhere/teardrop.mp3', 'Teardrop', 'Massive Attack')"
    ))
    await db_session.commit()
    monkeypatch.setattr("domovoi.workers.library_indexer.settings.music_dir", str(music_dir))

    counts = await index_music_dir()
    assert counts["inserted"] == 2
    rows = dict((await db_session.execute(text(
        "SELECT title, artist FROM library_tracks WHERE file_path <> '/elsewhere/teardrop.mp3'"
    ))).all())
    assert rows == {"Mezzanine": "Massive Attack", "Sunny Day": "Akon"}
