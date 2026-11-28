"""The strict title check (2026-11-28): an answer must name the file's SONG,
not just its artist.

A library sweep took AcoustID's first recording with only a score check,
and the agreement rule then let any title through once the artist agreed.
An audit of one library found 163 rows clearly renamed to the wrong
recording, most by the right artist: "Mo'murda" by Bone Thugs-N-Harmony
became "Ghetto Cowboy" (another file in the same folder, 20 s longer),
"Perfect" by Ed Sheeran became "Beyond the Pale", two Beatles songs both
became "Ask Me Why". These tests hold:

* those answers are refused, by title, whoever the artist;
* the right recording is used when the provider lists it after a wrong
  one, and the length check still applies;
* a title is compared after normalising case, Unicode hyphens and quotes,
  "feat." credits, "&"/"and", version suffixes, a video id and upload noise;
* a file name is held to as a title when the file has no artist tag;
* the repair's fit check refuses the same rows.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from domovoi.tests.conftest import requires_db
from domovoi.tests.enrich_outcome_testkit import apply_v020
from domovoi.workers import library_enricher as le
from domovoi.workers.library_enricher import (
    EnrichmentResult,
    Lookup,
    Verdict,
    file_names,
    names_agree,
    real_title,
    title_readings,
    titles_agree,
)

ENRICHER = "domovoi.workers.library_enricher"
BONE = "Bone Thugs-N-Harmony"
BONE_MB = "Bone Thugs‐n‐Harmony"     # MusicBrainz's form, U+2010 hyphens
MO_STEM = "Mo'murda [aaaaaaaaaaa]"


# ═══ The rule (pure) ══════════════════════════════════════════════════════

# (the file's title, its artist, the wrong answer's title, its artist): what
# the 2026-11-12 sweep wrote over the file's names (from the audit).
WRONG_TITLE = [
    ("Mo'murda", BONE, "Ghetto Cowboy", BONE_MB),
    ("Perfect", "Ed Sheeran", "Beyond the Pale", "Ed Sheeran"),
    ("Drive My Car - Remastered 2009", "The Beatles", "Ask Me Why", "The Beatles"),
    ("Twist And Shout - 2023 Mix", "The Beatles", "Ask Me Why", "The Beatles"),
    ("Paperback Writer - 2022 Mix", "The Beatles", "Here, There and Everywhere", "The Beatles"),
    ("Empathy", "Crystal Castles", "Air War", "Crystal Castles"),
    ("Everybody (Backstreet's Back) (Official HD Video)", "Backstreet Boys",
     "I’ll Never Break Your Heart", "Backstreet Boys"),
    ("Believer (Lyrics)", "Imagine Dragons", "Thunder", "Imagine Dragons"),
    ("I've Been Everywhere", "Johnny Cash", "San Quentin (live)", "Johnny Cash"),
    ("Bounce ft. Kelis", "Calvin Harris", "You Used to Hold Me", "Calvin Harris"),
    ("Two-Way Mirror", "Sam Example", "A Sad Cartoon", "Sam Example"),
]


@pytest.mark.parametrize(("ot", "oa", "tt", "ta"), WRONG_TITLE)
def test_the_right_artist_with_the_wrong_title_is_refused(ot, oa, tt, ta) -> None:
    assert names_agree(ot, oa, tt, ta) is False
    # ...and the file's name doesn't let it through either.
    assert names_agree(ot, oa, tt, ta, our_extra=f"{oa} - {ot} [bbbbbbbbbbb]") is False


# (our title, our artist, their title, their artist): the same song, written
# another way.
SAME_SONG = [
    ("Mo'murda", BONE, "Mo’ Murda", BONE_MB),
    ("Drive My Car - Remastered 2009", "The Beatles", "Drive My Car", "The Beatles"),
    ("Here, There And Everywhere - 2022 Mix", "The Beatles", "Here, There and Everywhere",
     "The Beatles"),
    ("Don't Stop 'Til You Get Enough", "Michael Jackson",
     "Don’t Stop ’Til You Get Enough", "Michael Jackson"),
    ("Bounce ft. Kelis", "Calvin Harris", "Bounce", "Calvin Harris feat. Kelis"),
    ("Rock & Roll", "Alex Example", "Rock and Roll", "Alex Example"),
    ("Free Fallin'", "Tom Petty", "Free Fallin’ (2004 Remaster)", "Tom Petty"),
    ("Interlude", "Sam Example", "True Dat (interlude)", "Sam Example"),
    ("Hood Nigga", "Gorilla Zoe", "Hood N***a", "Gorilla Zoe"),
    ("4AM", "Sam Example", "4 AM", "Sam Example"),
    ("Smack my b!tch up", "The Prodigy", "Smack My Bitch Up", "The Prodigy"),
    ("1999", "Prince", "1999", "Prince & The Revolution"),
]


@pytest.mark.parametrize(("ot", "oa", "tt", "ta"), SAME_SONG)
def test_the_same_song_written_another_way_agrees(ot, oa, tt, ta) -> None:
    assert names_agree(ot, oa, tt, ta) is True


def test_a_title_is_read_without_its_video_id_artist_and_version() -> None:
    assert title_readings(f"{BONE} - {MO_STEM}", artist=BONE) == [
        f"{BONE} - Mo'murda", "Mo'murda",
    ]
    assert "Drive My Car" in title_readings("Drive My Car - Remastered 2009")
    assert "Believer" in title_readings("Imagine Dragons- Believer (Lyrics)", artist="Imagine Dragons")
    assert "RUN!" in title_readings("4.RUN!")
    # A self-titled song keeps its one part.
    assert "Weezer" in title_readings("Weezer - Weezer", artist="Weezer")


def test_titles_agree_ignores_the_artist_part_of_the_file_name() -> None:
    stem = "Imagine Dragons- Believer (Lyrics) [cccccccccc1]"
    assert titles_agree(None, "Believer", our_extra=stem, our_artist="Imagine Dragons")
    assert not titles_agree(None, "Thunder", our_extra=stem, our_artist="Imagine Dragons")


@pytest.mark.parametrize("title", ["Track 3", "track01", "01 track", "03", "Untitled",
                                   "Audio 12", "AUD-20190101-WA0003", "[ddddddddddd]", "", None])
def test_placeholders_are_no_title(title) -> None:
    assert real_title(title) is False


@pytest.mark.parametrize("title", ["Mo'murda [aaaaaaaaaaa]", "1999", "22", "4AM", "Up", "ギミチョコ"])
def test_song_names_are_titles(title) -> None:
    assert real_title(title) is True


def test_a_placeholder_title_leaves_the_artist_to_decide() -> None:
    # "Track 3" names no song: the catalog's own title is fine when the
    # artist is ours.
    assert names_agree("Track 3", "Lil Wayne", "Abortion", "Lil Wayne") is True
    assert names_agree("Track 3", "Lil Wayne", "Abortion", "Someone Else") is False


# ═══ File names (pure) ════════════════════════════════════════════════════


def test_a_file_name_is_a_title_when_the_file_has_no_artist() -> None:
    path = f"/music/Ultimate/{MO_STEM}.mp3"
    names = file_names(path, MO_STEM, None)
    assert names == (MO_STEM, None)
    assert names_agree(*names, "Ghetto Cowboy", BONE_MB, our_extra=MO_STEM) is False
    assert names_agree(*names, "Mo’ Murda", BONE_MB, our_extra=MO_STEM) is True


def test_a_placeholder_file_name_is_still_no_names() -> None:
    assert file_names("/m/01 track.mp3", "01 track", None) == (None, None)
    assert file_names("/m/AUD-20190101-WA0003.mp3", "AUD-20190101-WA0003", None) == (None, None)


def test_a_file_named_for_its_artist_says_nothing_about_the_song() -> None:
    assert names_agree("Ed Sheeran", None, "Supermarket Flowers", "Ed Sheeran") is True
    # A name that holds more than the artist is a title.
    assert names_agree("Audioslave - Show Me How to Live (Official Video)", None,
                       "Doesn't Remind Me (live)", "Audioslave") is False


# ═══ Candidates (pure) ════════════════════════════════════════════════════


def _cand(title, artist, rid, score, length):
    return EnrichmentResult(title=title, artist=artist, musicbrainz_recording_id=rid,
                            source="acoustid", score=score, sources=3, length_sec=length)


def test_the_right_sibling_is_picked_when_the_provider_lists_it() -> None:
    wrong = _cand("Ghetto Cowboy", BONE_MB, "r-ghetto", 0.97, 325)
    right = _cand("Mo’ Murda", BONE_MB, "r-mo", 0.91, 344)
    look = Lookup(Verdict.MATCH, result=wrong, candidates=(wrong, right))
    got = le._agreeing(look, ("Mo'murda", BONE), 345, MO_STEM)
    assert got.verdict is Verdict.MATCH and got.result.musicbrainz_recording_id == "r-mo"
    # Without the right one, the row is disagreed, carrying the wrong one
    # for "Needs review".
    alone = le._agreeing(Lookup(Verdict.MATCH, result=wrong, candidates=(wrong,)),
                         ("Mo'murda", BONE), 345, MO_STEM)
    assert alone.verdict is Verdict.DISAGREED
    assert [c.musicbrainz_recording_id for c in alone.candidates] == ["r-ghetto"]


def test_a_sibling_within_the_length_tolerance_is_refused_by_its_title() -> None:
    # 20 s apart: inside LENGTH_TOLERANCE_SEC, so only the title tells them apart.
    assert le.length_plausible(345, 325)
    wrong = _cand("Ghetto Cowboy", BONE_MB, "r-ghetto", 0.97, 325)
    got = le._agreeing(Lookup(Verdict.MATCH, result=wrong, candidates=(wrong,)),
                       file_names(f"/m/{MO_STEM}.mp3", MO_STEM, None), 345, MO_STEM)
    assert got.verdict is Verdict.DISAGREED


def test_the_length_check_still_applies_to_the_right_title() -> None:
    long_take = _cand("Mo’ Murda", BONE_MB, "r-live", 0.97, 520)
    got = le._agreeing(Lookup(Verdict.MATCH, result=long_take, candidates=(long_take,)),
                       ("Mo'murda", BONE), 345, MO_STEM)
    assert got.verdict is Verdict.DISAGREED


# ═══ The repair's fit check (pure) ════════════════════════════════════════


def test_the_repair_no_longer_keeps_a_wrong_title_by_the_right_artist() -> None:
    assert le._row_fits_file(("Mo'murda", BONE), MO_STEM, "Ghetto Cowboy", BONE_MB) is False
    assert le._row_fits_file(("Perfect", "Ed Sheeran"), "Ed Sheeran - Perfect",
                             "Beyond the Pale", "Ed Sheeran") is False
    assert le._row_fits_file(("Mo'murda", BONE), MO_STEM, "Mo’ Murda", BONE_MB) is True


# ═══ The sweep (DB) ═══════════════════════════════════════════════════════


@pytest.fixture
async def lane(db_session, monkeypatch):
    await apply_v020()
    monkeypatch.setattr(f"{ENRICHER}.settings.library_enricher_enabled", True)
    monkeypatch.setattr(f"{ENRICHER}.settings.library_enricher_delay_sec", 0)
    monkeypatch.setattr(f"{ENRICHER}.provider_available", lambda: True)
    monkeypatch.setattr(f"{ENRICHER}.connectivity.current_probe", lambda: None)
    yield db_session


async def _no_shazam(_p):
    return Lookup(Verdict.NOT_ASKED, detail="shazamio not installed")


@requires_db
@pytest.mark.asyncio
async def test_the_sweep_matches_the_right_sibling_and_refuses_the_wrong_one(
    lane, monkeypatch, tmp_path,
) -> None:
    from domovoi import egress

    folder = tmp_path / "Ultimate"
    folder.mkdir()
    mo, ghetto = folder / f"{MO_STEM}.mp3", folder / "Ghetto Cowboy [eeeeeeeeeee].mp3"
    lonely = folder / "Perfect.mp3"
    for p in (mo, ghetto, lonely):
        p.write_bytes(b"")
    rows = {}
    for p, title, artist, dur in ((mo, "Mo'murda", BONE, 345), (ghetto, "Ghetto Cowboy", BONE, 325),
                                  (lonely, "Perfect", "Ed Sheeran", 263)):
        rows[p.name] = (await lane.execute(text(
            "INSERT INTO library_tracks (file_path, title, artist, duration_sec, added_via) "
            "VALUES (:p, :t, :a, :d, 'manual') RETURNING id"),
            {"p": str(p), "t": title, "a": artist, "d": dur})).scalar_one()
    await lane.commit()

    ghetto_rec = _cand("Ghetto Cowboy", BONE_MB, "r-ghetto", 0.97, 325)
    mo_rec = _cand("Mo’ Murda", BONE_MB, "r-mo", 0.91, 344)
    pale = _cand("Beyond the Pale", "Ed Sheeran", "r-pale", 0.95, 270)

    async def ac(p):
        if p.name == mo.name:   # the fingerprint names the sibling first
            return Lookup(Verdict.MATCH, result=ghetto_rec, candidates=(ghetto_rec, mo_rec))
        if p.name == ghetto.name:
            return Lookup(Verdict.MATCH, result=ghetto_rec, candidates=(ghetto_rec,))
        return Lookup(Verdict.MATCH, result=pale, candidates=(pale,))

    monkeypatch.setattr(f"{ENRICHER}._enrich_via_acoustid", ac)
    monkeypatch.setattr(f"{ENRICHER}._enrich_via_shazam", _no_shazam)
    with egress.override_policy("always"):
        counts = await le.enrich_library()
    assert (counts["matched"], counts["disagreed"]) == (2, 1)

    got = {r.id: r for r in (await lane.execute(text(
        "SELECT id, title, artist, musicbrainz_recording_id, enrich_outcome FROM library_tracks"
    ))).all()}
    assert (got[rows[mo.name]].title, got[rows[mo.name]].musicbrainz_recording_id) == (
        "Mo’ Murda", "r-mo")
    assert got[rows[ghetto.name]].musicbrainz_recording_id == "r-ghetto"
    kept = got[rows[lonely.name]]
    assert (kept.title, kept.musicbrainz_recording_id, kept.enrich_outcome) == (
        "Perfect", None, "disagreed")
    # The refused one waits in "Needs review" with what was refused.
    cands = (await lane.execute(text(
        "SELECT candidates FROM library_match_candidates WHERE track_id = :t"),
        {"t": rows[lonely.name]})).scalar_one()
    assert cands[0]["title"] == "Beyond the Pale"


@requires_db
@pytest.mark.asyncio
async def test_an_untagged_file_is_held_to_its_file_name(lane, monkeypatch, tmp_path) -> None:
    from domovoi import egress

    f = tmp_path / f"{MO_STEM}.mp3"
    f.write_bytes(b"")
    tid = (await lane.execute(text(
        "INSERT INTO library_tracks (file_path, title, artist, duration_sec, added_via) "
        "VALUES (:p, :t, NULL, 345, 'manual') RETURNING id"), {"p": str(f), "t": MO_STEM})).scalar_one()
    await lane.commit()
    wrong = _cand("Ghetto Cowboy", BONE_MB, "r-ghetto", 0.97, 325)

    async def ac(_p):
        return Lookup(Verdict.MATCH, result=wrong, candidates=(wrong,))

    monkeypatch.setattr(f"{ENRICHER}._enrich_via_acoustid", ac)
    monkeypatch.setattr(f"{ENRICHER}._enrich_via_shazam", _no_shazam)
    with egress.override_policy("always"):
        counts = await le.enrich_library()
    assert counts["disagreed"] == 1
    row = (await lane.execute(text(
        "SELECT title, musicbrainz_recording_id, enrich_outcome FROM library_tracks WHERE id = :i"),
        {"i": tid})).one()
    assert tuple(row) == (MO_STEM, None, "disagreed")


@requires_db
@pytest.mark.asyncio
async def test_the_repair_restores_a_wrong_title_by_the_right_artist(lane, tmp_path) -> None:
    from domovoi.tests import media_writer as mw

    f = mw.write_mp3(tmp_path / f"{MO_STEM}.mp3", [
        mw.TIT2("Mo'murda"), mw.text_frame("TPE1", BONE), mw.text_frame("TALB", "E. 1999 Eternal"),
    ])
    tid = (await lane.execute(text(
        "INSERT INTO library_tracks (file_path, title, artist, album, musicbrainz_recording_id, "
        "enrich_outcome, enriched_at, added_via) VALUES (:p, 'Ghetto Cowboy', :a, "
        "'E. 1999 Eternal', 'r-ghetto', 'matched', now(), 'manual') RETURNING id"),
        {"p": str(f), "a": BONE_MB})).scalar_one()
    await lane.commit()

    counts = await le.repair_disagreeing()
    assert counts["restored"] == 1
    row = (await lane.execute(text(
        "SELECT title, artist, album, musicbrainz_recording_id, enrich_outcome "
        "FROM library_tracks WHERE id = :i"), {"i": tid})).one()
    assert tuple(row) == ("Mo'murda", BONE, "E. 1999 Eternal", None, "disagreed")


def test_the_module_still_reads_file_names_as_paths() -> None:
    # file_names takes str or Path, as the sweep and the repair pass both.
    assert file_names(Path("/m") / "Perfect.mp3", "Perfect", None) == ("Perfect", None)
