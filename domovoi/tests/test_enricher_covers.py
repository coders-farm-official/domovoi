"""The enricher refuses covers and other recordings of a song (2026-11-14,
second pass).

After the upload-name fix, a requeue of 241 rows brought the upload-style
names back right, but a title-only agreement took covers and alternate
recordings: when the row named a real artist, an answer by a different
artist (or an instrumental, remix or mix) was taken anyway. Now:

* when our artist is a real performer, the answer's artist must be ours;
  the title alone is enough only with no usable artist of ours;
* an answer whose title adds "instrumental", "karaoke", "cover", "remix",
  "mix" or "tribute" that our names and file name don't have is refused;
* the repair (no body) puts the file's names back on the rows the requeue
  matched that way, and leaves the good re-matches alone.

Every example is a live row: (what it is stored as now, what the file and
its tags said).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from domovoi.tests.conftest import requires_db
from domovoi.tests.enrich_outcome_testkit import apply_v020
from domovoi.workers.library_enricher import (
    EnrichmentResult,
    Lookup,
    Verdict,
    artist_usable,
    enrich_library,
    names_agree,
    repair_disagreeing,
)

ENRICHER = "domovoi.workers.library_enricher"

# (file name, the file's title, the file's artist, stored title, stored
# artist, what the repair puts back: (title, artist)). An untagged file
# (its names read from the file name) has the file's title None.
WRONG = [
    ("The Real Slim Shady [rkqMbsmLrtA].mp3", "The Real Slim Shady", "Eminem",
     "Real Slim Shady", "Caveman", None),
    ("Rat Race [aaaaaaaaaaa].mp3", "Rat Race", "Skindred",
     "Skindred - Rat Race - Lyrics", "Faded Swade", None),
    ("Jumper [bbbbbbbbbbb].mp3", "Jumper", "Third Eye Blind", "Jumper", "Tweaker", None),
    ("NSYNC - Bye Bye Bye (Official Video).mp3", None, None,
     "Bye Bye Bye", "Charles Jérôme", ("Bye Bye Bye", "NSYNC")),
    ("Your Love [ccccccccccc].mp3", "Your Love", "The Outfield", "Your Love", "Lime", None),
    ("What About Love [ddddddddddd].mp3", "What About Love", "Heart",
     "What About Love?", "Deborah Coleman", None),
    ("Just to See You Smile [eeeeeeeeeee].mp3", "Just to See You Smile", "Tim McGraw",
     "Just to See You Smile", "Phoenix", None),
    ("Steve Aoki - Selfie.mp3", None, None,
     "#SELFIE (Will Sparks remix)", "The Chainsmokers", ("Selfie", "Steve Aoki")),
    ("SHOUSE - Love Tonight (Official Radio Edit).mp3", "Love Tonight (Official Radio Edit)",
     "SHOUSE", "Love Tonight (Radio Edit)", "The Disco Boys", ("Love Tonight", "SHOUSE")),
    ("You Make Loving Fun [fffffffffff].mp3", "You Make Loving Fun", "Fleetwood Mac",
     "You Make Loving Fun", "Dougal Reed", None),
    ("Cherry Bomb [ggggggggggg].mp3", "Cherry Bomb", "The Runaways",
     "Cherry Bomb", "Joan Jett and the Blackhearts", None),
    ("Forgot About Dre [hhhhhhhhhhh].mp3", "Forgot About Dre", "Dr. Dre",
     "Forgot About Dre (instrumental)", "Dr. Dre", None),
    ("Heat Waves [iiiiiiiiiii].mp3", "Heat Waves", "Glass Animals",
     "Heat Waves (instrumental)", "Glass Animals", None),
    ("American Woman [jjjjjjjjjjj].mp3", 'American Woman - 7" Single Version', "The Guess Who",
     "American Woman (4.0 mix)", "The Guess Who", ("American Woman", "The Guess Who")),
]

# The good re-matches of the requeue, and the 2026-11-13 refusals.
GOOD = [
    ("That’s what I like.mp3", "Bruno Mars - That’s what I like (Lyrics)", "Pizza Music",
     "That's What I Like", "Bruno Mars"),
    ("Longview - Green Day   Lyrics.mp3", None, None, "Longview", "Green Day"),
    ("Hand In My Pocket.mp3", "Alanis Morissette - Hand In My Pocket (Lyrics)", "Lyrics 90's",
     "Hand in My Pocket", "Alanis Morissette"),
    ("Better Off Alone.mp3", "Alice Deejay - Better Off Alone (Lyrics)", "Mixed Lyrics",
     "Better Off Alone", "Alice Deejay"),
    ("Stan [S-smUALTlFM].mp3", "Stan", "Eminem, Dido", "Stan", "Eminem feat. Dido"),
]
OBSERVED = [
    ("In the End", "Linkin Park", "Linkin Park", "Boomin"),
    ("Inconsolable", "Backstreet Boys", "Backstreet Boys", "Totally Insane"),
    ("Secrets", "The Weeknd", "The Weeknd", "Witch House"),
    ("Bad Company", "Five Finger Death Punch", "Five Finger Death Punch", "Norin & Rad"),
    ("Abortion", "Lil Wayne", "Lil Wayne", "Caotico"),
]


def _file_names(fn: str, t: str | None, a: str | None) -> tuple[str, str]:
    if t is not None:
        return t, a
    from domovoi.workers.library_indexer import _fallback_filename_parse

    artist, title = _fallback_filename_parse(Path(fn))
    return title, artist


# ═══ The rule (pure) ══════════════════════════════════════════════════════


@pytest.mark.parametrize(("fn", "ft", "fa", "st", "sa", "_back"), WRONG)
def test_a_cover_or_other_recording_is_refused(fn, ft, fa, st, sa, _back) -> None:
    t, a = _file_names(fn, ft, fa)
    assert names_agree(t, a, st, sa, our_extra=Path(fn).stem) is False


@pytest.mark.parametrize(("fn", "ft", "fa", "st", "sa"), GOOD)
def test_the_good_rematches_still_agree(fn, ft, fa, st, sa) -> None:
    t, a = _file_names(fn, ft, fa)
    assert names_agree(t, a, st, sa, our_extra=Path(fn).stem) is True


@pytest.mark.parametrize(("ot", "oa", "tt", "ta"), OBSERVED)
def test_the_five_observed_answers_are_still_refused(ot, oa, tt, ta) -> None:
    assert names_agree(ot, oa, tt, ta) is False


def test_the_title_alone_is_enough_only_without_a_usable_artist() -> None:
    assert names_agree("Jumper", None, "Jumper", "Third Eye Blind") is True
    assert names_agree("Jumper", "Unknown Artist", "Jumper", "Third Eye Blind") is True
    assert names_agree("Jumper", "SomeChannelVEVO", "Jumper", "Third Eye Blind") is True
    assert names_agree("Jumper", "Third Eye Blind", "Jumper", "Tweaker") is False
    # an answer naming no artist leaves ours in place: its title is enough
    assert names_agree("Jumper", "Third Eye Blind", "Jumper", None) is True


def test_a_variant_word_we_have_is_not_refused() -> None:
    assert names_agree("Heat Waves (Instrumental)", "Glass Animals",
                       "Heat Waves (instrumental)", "Glass Animals") is True
    assert names_agree("Selfie", "Steve Aoki", "Selfie (remix)", "Steve Aoki",
                       our_extra="Steve Aoki - Selfie remix") is True
    assert names_agree("Mix Tape", "Jay Brannan", "Mix Tape", "Jay Brannan") is True


@pytest.mark.parametrize(
    ("artist", "usable"),
    [("Eminem", True), ("Eminem, Dido", True), ("Pizza Music", True),
     ("LaBoucheVEVO", False), ("Mixed Lyrics", False), ("Lyrics 90's", False),
     ("Audioandlyrics", False), ("rostrumrecords", False), ("Unknown Artist", False),
     ("", False), (None, False)],
)
def test_artist_usable(artist, usable) -> None:
    assert artist_usable(artist) is usable


# ═══ The repair and the sweep (database) ══════════════════════════════════


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
        text("SELECT title, artist, musicbrainz_recording_id, enrich_outcome "
             "FROM library_tracks WHERE id = :id"),
        {"id": track_id},
    )).first()


@requires_db
@pytest.mark.asyncio
async def test_repair_restores_the_covers_and_keeps_the_good_rematches(
    lane, monkeypatch, tmp_path,
) -> None:
    from domovoi.workers import library_indexer

    tags: dict[str, tuple[str, str]] = {}
    wrong: dict[int, tuple[str, str]] = {}
    good: dict[int, tuple[str, str]] = {}
    for i, (fn, ft, fa, st, sa, back) in enumerate(WRONG):
        f = tmp_path / fn
        f.write_bytes(b"")
        if ft is not None:
            tags[f.name] = (ft, fa)
        wrong[await _insert(lane, str(f), st, sa, musicbrainz_recording_id=f"mb-w{i}",
                            enrich_outcome="matched")] = back or (ft, fa)
    for i, (fn, ft, fa, st, sa) in enumerate(GOOD):
        f = tmp_path / fn
        f.write_bytes(b"")
        if ft is not None:
            tags[f.name] = (ft, fa)
        good[await _insert(lane, str(f), st, sa, musicbrainz_recording_id=f"mb-g{i}",
                           enrich_outcome="matched")] = (st, sa)
    await lane.execute(text("UPDATE library_tracks SET enriched_at = NOW()"))
    await lane.commit()

    real = library_indexer._resolve_metadata_for

    async def read(path: Path, known_artists=None):
        if path.name in tags:
            t, a = tags[path.name]
            return {"title": t, "artist": a, "album": None}
        return real(path, known_artists)

    monkeypatch.setattr("domovoi.workers.library_indexer.read_metadata", read)

    counts = await repair_disagreeing()

    assert (counts["restored"], counts["unsure"]) == (len(WRONG), 0), counts
    for track_id, names in wrong.items():
        row = await _get(lane, track_id)
        assert (row.title, row.artist) == names
        assert row.enrich_outcome == "disagreed" and row.musicbrainz_recording_id is None
    for track_id, names in good.items():
        row = await _get(lane, track_id)
        assert (row.title, row.artist) == names
        assert row.enrich_outcome == "matched" and row.musicbrainz_recording_id


@requires_db
@pytest.mark.asyncio
async def test_the_sweep_takes_the_right_recording_over_a_cover(lane, monkeypatch, tmp_path) -> None:
    f = tmp_path / "Jumper.mp3"
    g = tmp_path / "Rat Race.mp3"
    for p in (f, g):
        p.write_bytes(b"")
    jumper = await _insert(lane, str(f), "Jumper", "Third Eye Blind")
    rat = await _insert(lane, str(g), "Rat Race", "Skindred")

    async def ac(p):
        if p.name == f.name:
            cands = (
                EnrichmentResult(title="Jumper", artist="Tweaker", musicbrainz_recording_id="mb-cover",
                                 source="acoustid"),
                EnrichmentResult(title="Jumper (instrumental)", artist="Third Eye Blind",
                                 musicbrainz_recording_id="mb-inst", source="acoustid"),
                EnrichmentResult(title="Jumper", artist="Third Eye Blind",
                                 musicbrainz_recording_id="mb-jumper", source="acoustid"),
            )
            return Lookup(Verdict.MATCH, result=cands[0], candidates=cands)
        return Lookup(Verdict.MATCH, result=EnrichmentResult(
            title="Rat Race - Lyrics", artist="Skindred", musicbrainz_recording_id="mb-rat",
            source="acoustid"))

    async def no_shazam(_p):
        return Lookup(Verdict.NOT_ASKED, detail="shazamio not installed")

    monkeypatch.setattr(f"{ENRICHER}._enrich_via_acoustid", ac)
    monkeypatch.setattr(f"{ENRICHER}._enrich_via_shazam", no_shazam)
    counts = await enrich_library()
    assert counts["matched"] == 2

    row = await _get(lane, jumper)
    assert (row.title, row.artist, row.musicbrainz_recording_id) == ("Jumper", "Third Eye Blind", "mb-jumper")
    row = await _get(lane, rat)
    assert (row.title, row.artist) == ("Rat Race", "Skindred")   # no upload noise written
