"""Lyric search end to end on Postgres: invented songs inserted straight
into V021's ``track_lyrics`` (as LRCLIB-found rows, one as the song's own
tags) → ``lyrics_index.catch_up()`` → ``library_match.resolve_lyrics``
(lyrics-build contract §15.2).

Pinned: an exact chorus phrase plays, and so does an exact verse phrase
(0.97 ≥ 0.90); a dropped word asks; two songs sharing a line are both
asked about, more than three are "too common"; the last resort plays an
exact phrase of six words, asks about an exact five-word one and never
looks at three; the artist filter; two copies of one song are one title
candidate; a row with no title entity is a track candidate; "no_lyrics"
and "disabled"; a LYRIC_NORM_VERSION bump re-indexes, lyrics going away
clear their lines, a line indexed from text that changed meanwhile is not
stamped current; the retrieval query goes through the GIN index; the line
index's status. ``requires_db``; V021 applied by ``lyrics_testkit``.

Every lyric, title and name here is invented.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from domovoi.config import settings
from domovoi.handlers.shared import library_match as lm
from domovoi.handlers.shared import lyric_search as ls
from domovoi.handlers.shared import spoken_names
from domovoi.tests.conftest import requires_db
from domovoi.workers import lyrics_index

pytestmark = requires_db

# ─── The invented library ─────────────────────────────────────────────────

CHORUS_1 = "the lantern hums beside the river door"
CHORUS_2 = "and every copper kettle sings at dawn"
VERSE_1 = "we carried paper boats along the hall"
SHARED = "the mailbox keeps a secret for the moon"
COMMON = "we folded maps into a paper crown"
SIX_WORDS = "the orchard fence is painted marmalade"
HERON = "a sleepy heron counts the passing trains"
SAILED = "we sailed from the harbor at noon"

LANTERN_SONG = "\n".join([
    VERSE_1,
    CHORUS_1, CHORUS_2,
    CHORUS_1, CHORUS_2,
    "a teapot whistles in the attic light",
    CHORUS_1, CHORUS_2,
])
COPPER_MORNING = "\n".join([
    "seven pebbles rolling down the garden stair",
    SHARED, SIX_WORDS,
    SHARED, SIX_WORDS,
    "my bicycle remembers every hill in town",
    SAILED,
])
ATTIC_LIGHT = "\n".join([
    HERON,
    "the kitchen clock is running half past blue",
    SHARED,
    "the kitchen clock is running half past blue",
    SHARED,
])
TRACK_09 = "\n".join([
    "the lighthouse knits a scarf of silver rain",
    "a pocketful of buttons and a borrowed tune",
    "the lighthouse knits a scarf of silver rain",
])


def _common(own: str) -> str:
    return "\n".join([COMMON, own, COMMON])


# (id, title, artist, album, path under MUSIC_DIR, lyrics or None, source)
LIBRARY = [
    (1, "Lantern Song", "The Example Band", "Paper Hall",
     "The Example Band/Paper Hall/01 Lantern Song.mp3", LANTERN_SONG, "lrclib"),
    (2, "Lantern Song", "The Example Band", "Live at the Hall",
     "The Example Band/Live at the Hall/04 Lantern Song.mp3", LANTERN_SONG, "lrclib"),
    (3, "Copper Morning", "Glass Harbor", "Tidewater",
     "Glass Harbor/Tidewater/02 Copper Morning.mp3", COPPER_MORNING, "lrclib"),
    (4, "Attic Light", "The Velvet Kites", "Rooftops",
     "The Velvet Kites/Rooftops/05 Attic Light.mp3", ATTIC_LIGHT, "embedded"),
    (5, "Paper Crown", "Juniper Lane", "Maps", "Juniper Lane/Maps/01 Paper Crown.mp3",
     _common("the hallway smells of cinnamon and glue"), "lrclib"),
    (6, "Folded Maps", "The Quiet Engines", "Atlas", "The Quiet Engines/Atlas/03 Folded Maps.mp3",
     _common("a postcard drifting over chimney smoke"), "lrclib"),
    (7, "Crown of Maps", "Marigold Static", "Static", "Marigold Static/Static/07 Crown of Maps.mp3",
     _common("our shadows trade their hats beneath the bridge"), "lrclib"),
    (8, "Map Song", "The Tin Lanterns", "Tin", "The Tin Lanterns/Tin/02 Map Song.mp3",
     _common("the radiator hums a lullaby for socks"), "lrclib"),
    (9, None, "The Velvet Kites", "Rooftops", "The Velvet Kites/Rooftops/Track 09.mp3",
     TRACK_09, "lrclib"),
    (10, "Quiet Engine", "Glass Harbor", "Tidewater",
     "Glass Harbor/Tidewater/05 Quiet Engine.mp3", None, None),
]


@pytest.fixture
def lyric_settings(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "music"
    monkeypatch.setattr(settings, "music_dir", str(root))
    monkeypatch.setattr(settings, "music_match_enabled", True)
    monkeypatch.setattr(settings, "music_match_play_threshold", 0.90)
    monkeypatch.setattr(settings, "music_match_ask_threshold", 0.75)
    monkeypatch.setattr(settings, "music_choice_enabled", True)
    monkeypatch.setattr(settings, "lyrics_search_enabled", True)
    lm.reset_for_tests()
    ls.reset_for_tests()
    lyrics_index.reset_status_for_tests()
    yield root
    lm.reset_for_tests()
    ls.reset_for_tests()
    lyrics_index.reset_status_for_tests()


async def seed_library(session, root: Path, library=LIBRARY) -> None:
    """The invented library and its lyrics, committed (the index worker
    reads with sessions of its own). Nothing indexed yet."""
    from domovoi.tests.library_aliases_testkit import apply_v019
    from domovoi.tests.lyrics_testkit import apply_v021

    await apply_v019()
    await apply_v021()
    for tid, title, artist, album, rel, lyrics, source in library:
        await session.execute(
            text("INSERT INTO library_tracks (id, file_path, title, artist, album, duration_sec)"
                 " VALUES (:id, :fp, :title, :artist, :album, 200)"),
            {"id": tid, "fp": str(root / rel), "title": title, "artist": artist, "album": album},
        )
        if lyrics is None:
            continue
        if source == "embedded":
            await session.execute(
                text("INSERT INTO track_lyrics (track_id, local_checked_at, local_source,"
                     " local_detail, local_plain) VALUES (:id, now(), 'embedded', 'USLT', :plain)"),
                {"id": tid, "plain": lyrics},
            )
        else:
            await session.execute(
                text("INSERT INTO track_lyrics (track_id, local_checked_at, lrclib_status,"
                     " lrclib_id, lrclib_match, lrclib_plain, lrclib_checked_at)"
                     " VALUES (:id, now(), 'found', :lid, 'get', :plain, now())"),
                {"id": tid, "lid": 9000 + tid, "plain": lyrics},
            )
    await session.execute(text(
        "SELECT setval(pg_get_serial_sequence('library_tracks', 'id'), (SELECT max(id) FROM library_tracks))"
    ))
    await session.commit()


@pytest.fixture
async def lyric_library(db_session, lyric_settings) -> Path:
    """The invented library, seeded and indexed."""
    await seed_library(db_session, lyric_settings)
    out = await lyrics_index.catch_up()
    assert out == {"indexed": 9, "cleared": 0, "pending": 0}
    return lyric_settings


async def _resolve(session, phrase: str, mode: str = "explicit", **kw):
    res = await lm.resolve_lyrics(session, phrase, mode=mode, **kw)
    await session.commit()
    return res


def _labels(res) -> list[str | None]:
    return [c.ref.label for c in res.candidates]


# ─── Explicit and identify ────────────────────────────────────────────────


@pytest.mark.parametrize("mode", ["explicit", "identify"])
async def test_an_exact_chorus_phrase_plays_the_song_once_for_both_copies(db_session, lyric_library, mode) -> None:
    res = await _resolve(db_session, CHORUS_1, mode)
    assert (res.decision, res.reason) == ("play", "lyrics")
    (best,) = res.candidates
    assert (best.ref.type, best.ref.label, best.ref.artist_label) == ("title", "Lantern Song", "The Example Band")
    assert set(best.track_ids) == {1, 2}          # two copies, one title candidate
    assert best.via == "lyrics" and best.score == 1.0
    assert res.heard == CHORUS_1


async def test_an_exact_verse_phrase_plays_too(db_session, lyric_library) -> None:
    res = await _resolve(db_session, VERSE_1)
    assert res.decision == "play"
    assert res.best.ref.label == "Lantern Song"
    assert res.best.score == pytest.approx(ls.VERSE_FACTOR)


async def test_a_phrase_across_two_lines_plays(db_session, lyric_library) -> None:
    res = await _resolve(db_session, "beside the river door and every copper kettle")
    assert res.decision == "play" and res.best.ref.label == "Lantern Song"


async def test_a_dropped_word_asks(db_session, lyric_library) -> None:
    res = await _resolve(db_session, "the lantern hums beside river door")
    assert (res.decision, res.reason) == ("ask", "lyrics")
    assert _labels(res) == ["Lantern Song"]
    assert 0.75 <= res.best.score < 0.90


async def test_two_songs_sharing_a_line_are_both_asked_about(db_session, lyric_library) -> None:
    res = await _resolve(db_session, SHARED)
    assert (res.decision, res.reason) == ("ask", "tie")
    assert sorted(_labels(res)) == ["Attic Light", "Copper Morning"]


async def test_more_than_three_songs_with_the_words_are_too_common(db_session, lyric_library) -> None:
    res = await _resolve(db_session, COMMON)
    assert (res.decision, res.reason) == ("none", "too_common")


async def test_words_no_song_has(db_session, lyric_library) -> None:
    res = await _resolve(db_session, "purple elephants juggle the stock market")
    assert res.decision == "none" and res.reason in ("below_ask", "below_play")


async def test_three_words_never_play(db_session, lyric_library) -> None:
    res = await _resolve(db_session, "painted marmalade fence")
    assert res.decision in ("ask", "none")
    res = await _resolve(db_session, "the orchard")
    assert (res.decision, res.reason) == ("none", "too_short")


# ─── The last resort ──────────────────────────────────────────────────────


async def test_the_last_resort_plays_an_exact_phrase_of_six_words(db_session, lyric_library) -> None:
    res = await _resolve(db_session, SIX_WORDS, "fallback")
    assert (res.decision, res.reason) == ("play", "lyrics_exact")
    assert res.best.ref.label == "Copper Morning"


async def test_the_last_resort_asks_about_an_exact_phrase_of_five_words(db_session, lyric_library) -> None:
    res = await _resolve(db_session, "a sleepy heron counts the", "fallback")
    assert res.decision == "ask"
    assert _labels(res) == ["Attic Light"]


async def test_the_last_resort_never_looks_at_three_words(db_session, lyric_library) -> None:
    res = await _resolve(db_session, "painted marmalade fence", "fallback")
    assert (res.decision, res.reason) == ("none", "too_short")


async def test_the_artist_filter(db_session, lyric_library) -> None:
    res = await _resolve(db_session, SHARED, "fallback", artist="glass harbour")
    assert (res.decision, _labels(res)) == ("play", ["Copper Morning"])
    res = await _resolve(db_session, SHARED, "explicit", artist="the velvet kites")
    assert (res.decision, _labels(res)) == ("play", ["Attic Light"])
    res = await _resolve(db_session, SHARED, "explicit", artist="the example band")
    assert res.decision == "none"


async def test_avoid_and_declined(db_session, lyric_library) -> None:
    first = await _resolve(db_session, SIX_WORDS, "fallback")
    res = await _resolve(db_session, SIX_WORDS, "fallback", avoid=(first.best.ref,))
    assert (res.decision, res.reason) == ("none", "declined")


# ─── Candidates ───────────────────────────────────────────────────────────


async def test_a_song_with_no_title_is_a_track_candidate(db_session, lyric_library) -> None:
    res = await _resolve(db_session, "the lighthouse knits a scarf of silver rain")
    assert res.decision == "play"
    ref = res.best.ref
    assert (ref.type, ref.key, ref.label, ref.artist_label) == ("track", "9", "Track 09", "The Velvet Kites")
    assert res.best.track_ids == (9,)
    # It comes back as itself on "yes" (did-you-mean parks the ref).
    again = await lm.candidate_from_ref(db_session, ref)
    assert again is not None and again.track_ids == (9,)


# ─── Reasons ──────────────────────────────────────────────────────────────


async def test_no_lyrics_yet(db_session, lyric_settings) -> None:
    await seed_library(db_session, lyric_settings)             # seeded, not indexed
    res = await _resolve(db_session, CHORUS_1)
    assert (res.decision, res.reason) == ("none", "no_lyrics")


async def test_switched_off(db_session, lyric_library, monkeypatch) -> None:
    monkeypatch.setattr(settings, "lyrics_search_enabled", False)
    res = await _resolve(db_session, CHORUS_1)
    assert (res.decision, res.reason) == ("none", "disabled")


async def test_thresholds_can_be_passed_in(db_session, lyric_library) -> None:
    """The evaluation passes the Settings defaults explicitly."""
    res = await _resolve(db_session, "the lantern hums beside river door", play_threshold=0.80, ask_threshold=0.75)
    assert res.decision == "play"


async def test_a_failure_answers_error_and_leaves_the_transaction_usable(db_session, lyric_library, monkeypatch) -> None:
    async def broken(session, query):
        await session.execute(text("SELECT no_such_column FROM track_lyric_lines"))

    monkeypatch.setattr(ls, "fetch_rows", broken)
    res = await lm.resolve_lyrics(db_session, CHORUS_1, mode="explicit")
    assert (res.decision, res.reason) == ("none", "error")
    # The caller's transaction survived (a SAVEPOINT took the failure).
    assert (await db_session.execute(text("SELECT count(*) FROM library_tracks"))).scalar() == 10
    await db_session.commit()


# ─── The line index ───────────────────────────────────────────────────────


async def _lines(session, track_id: int) -> list[tuple]:
    return [tuple(r) for r in (await session.execute(text(
        "SELECT span, line_no, repeats, text FROM track_lyric_lines WHERE track_id = :id"
        " ORDER BY span, line_no"), {"id": track_id})).all()]


async def _stamp(session, track_id: int) -> tuple:
    return tuple((await session.execute(text(
        "SELECT lines_md5, lines_version, plain_md5 FROM track_lyrics WHERE track_id = :id"
    ), {"id": track_id})).one())


async def test_the_index_holds_the_built_lines_and_is_current(db_session, lyric_library) -> None:
    rows = await _lines(db_session, 1)
    assert rows == [(r.span, r.line_no, r.repeats, r.text) for r in ls.build_lines(LANTERN_SONG)]
    md5, version, plain_md5 = await _stamp(db_session, 1)
    assert md5 == plain_md5 and version == spoken_names.LYRIC_NORM_VERSION
    # The tags' lyrics (track 4) are indexed like LRCLIB's.
    assert (CHORUS_1,) not in [(r[3],) for r in await _lines(db_session, 4)]
    assert any(r[3] == SHARED and r[2] == 2 for r in await _lines(db_session, 4))
    # A track with no lyrics has no row and nothing to index.
    assert await _lines(db_session, 10) == []
    st = lyrics_index.lyrics_index_status()
    assert (st["pending"], st["indexed"]) == (0, 9)
    assert st["search_enabled"] is True
    assert set(st) == {"state", "pending", "indexed", "last_tick_at", "last_error", "search_enabled"}


async def test_a_norm_version_bump_reindexes_everything(db_session, lyric_library, monkeypatch) -> None:
    monkeypatch.setattr(spoken_names, "LYRIC_NORM_VERSION", 2)
    out = await lyrics_index.catch_up()
    assert out == {"indexed": 9, "cleared": 0, "pending": 0}
    assert (await _stamp(db_session, 3))[1] == 2
    assert (await lyrics_index.catch_up()) == {"indexed": 0, "cleared": 0, "pending": 0}


async def test_lyrics_that_change_or_go_away(db_session, lyric_library) -> None:
    # New text: only that song is redone.
    await db_session.execute(text(
        "UPDATE track_lyrics SET lrclib_plain = :p, updated_at = now() WHERE track_id = 5"
    ), {"p": "the hallway smells of cinnamon and glue\nthe hallway smells of cinnamon and glue"})
    # Gone: LRCLIB later had nothing for it (the row's text cleared).
    await db_session.execute(text(
        "UPDATE track_lyrics SET lrclib_plain = NULL, lrclib_status = 'not_found', updated_at = now()"
        " WHERE track_id = 6"
    ))
    await db_session.commit()
    out = await lyrics_index.catch_up()
    assert out == {"indexed": 1, "cleared": 1, "pending": 0}
    assert [r[3] for r in await _lines(db_session, 5)] == [
        "the hallway smells of cinnamon and glue",
        "the hallway smells of cinnamon and glue the hallway smells of cinnamon and glue",
    ]
    assert await _lines(db_session, 6) == []
    assert await _stamp(db_session, 6) == (None, None, None)
    # Two songs still share the common line: asked about, no longer too common.
    res = await _resolve(db_session, COMMON)
    assert (res.decision, sorted(_labels(res))) == ("ask", ["Crown of Maps", "Map Song"])


async def test_lines_built_from_text_that_changed_meanwhile_are_not_stamped(db_session, lyric_library) -> None:
    stale_md5 = (await _stamp(db_session, 3))[2]
    # The lyrics change, and this song's lines are not current any more.
    await db_session.execute(text(
        "UPDATE track_lyrics SET lrclib_plain = 'a brand new kettle line for the test',"
        " lines_md5 = NULL, lines_version = NULL WHERE track_id = 3"
    ))
    await db_session.commit()
    # Lines built from the text read BEFORE the change are written…
    async with lyrics_index_session() as s:
        await lyrics_index._index_one(s, 3, COPPER_MORNING, stale_md5, spoken_names.LYRIC_NORM_VERSION)
        await s.commit()
    # …but not stamped current: the next call redoes the song.
    md5, version, plain_md5 = await _stamp(db_session, 3)
    assert (md5, version) == (None, None) and plain_md5 != stale_md5
    out = await lyrics_index.catch_up()
    assert (out["indexed"], out["pending"]) == (1, 0)
    assert [r[3] for r in await _lines(db_session, 3)] == ["a brand new kettle line for the test"]


def lyrics_index_session():
    from domovoi.db.session import SessionLocal

    return SessionLocal()


async def test_one_song_that_cannot_be_indexed_never_stops_the_rest(db_session, lyric_settings, monkeypatch) -> None:
    await seed_library(db_session, lyric_settings)
    real = ls.build_lines

    def picky(plain):
        if plain == ATTIC_LIGHT:
            raise ValueError("boom")
        return real(plain)

    monkeypatch.setattr(ls, "build_lines", picky)
    out = await lyrics_index.catch_up()
    assert out == {"indexed": 8, "cleared": 0, "pending": 1}
    assert lyrics_index.lyrics_index_status()["last_error"] == "ValueError"
    # Tracks after it were done in the same call.
    assert await _lines(db_session, 9)


async def test_the_budget_stops_a_call(db_session, lyric_settings) -> None:
    await seed_library(db_session, lyric_settings)
    out = await lyrics_index.catch_up(budget_sec=0.0)
    assert out["indexed"] == 0 and out["pending"] == 9
    out = await lyrics_index.catch_up(batch=2)
    assert out == {"indexed": 9, "cleared": 0, "pending": 0}


async def test_the_worker_tick_and_its_status(db_session, lyric_settings) -> None:
    await seed_library(db_session, lyric_settings)
    worker = lyrics_index.LyricsIndexer()
    assert (worker.name, worker.enabled_setting, worker.interval_setting) == (
        "lyrics_index", None, "lyrics_index_interval_sec")
    assert worker.stub_suppressed is True and worker.requires_online is False
    assert lyrics_index.lyrics_index_status()["state"] == "idle"
    out = await worker.tick()
    assert out["indexed"] == 9
    st = lyrics_index.lyrics_index_status()
    assert (st["state"], st["pending"], st["indexed"], st["last_error"]) == ("done", 0, 9, None)
    assert st["last_tick_at"] is not None


async def test_a_tick_that_fails_reports_error(db_session, lyric_settings, monkeypatch) -> None:
    async def down(**kw):
        raise ConnectionRefusedError("database is down")

    monkeypatch.setattr(lyrics_index, "catch_up", down)
    assert await lyrics_index.LyricsIndexer().tick() is None
    st = lyrics_index.lyrics_index_status()
    assert (st["state"], st["last_error"]) == ("error", "ConnectionRefusedError")

    async def missing(**kw):
        raise lyrics_index.LyricsTablesMissing("no V021")

    monkeypatch.setattr(lyrics_index, "catch_up", missing)
    await lyrics_index.LyricsIndexer().tick()
    assert lyrics_index.lyrics_index_status()["last_error"] == "no_lyrics_tables"


# ─── Retrieval uses the GIN index ─────────────────────────────────────────


async def test_retrieval_goes_through_the_trigram_index(db_session, lyric_library) -> None:
    await db_session.execute(text("SET LOCAL enable_seqscan = off"))
    await db_session.execute(text("SELECT set_config('pg_trgm.word_similarity_threshold', '0.45', true)"))
    plan = "\n".join(r[0] for r in (await db_session.execute(text(
        "EXPLAIN SELECT l.track_id FROM track_lyric_lines l"
        " WHERE 'the lantern hums beside the river door' <% l.text"
    ))).all())
    assert "idx_track_lyric_lines_trgm" in plan, plan
    rows = await ls.fetch_rows(db_session, ls.lyric_query(CHORUS_1))
    await db_session.rollback()
    assert rows and rows[0].text == CHORUS_1 and rows[0].ws == pytest.approx(1.0)
    assert len(rows) <= ls.CANDIDATE_ROWS


# ─── Two looks, one answer ([Q24]; the 2026-10-06 review's latency fix) ───

# An invented vocabulary: lines of these words share trigrams the way lines
# of common words do, which is what makes the wide look slow.
_VOCAB = (
    "the a and of in on my your we i you is to at by for with it "
    "lantern river door copper kettle paper boats hall dawn orchard fence "
    "marmalade heron harbor attic light mailbox secret moon teapot"
).split()


def _synthetic_library(n_songs: int = 150, seed: int = 20261006) -> list[tuple]:
    import random

    rng = random.Random(seed)
    out = []
    for i in range(n_songs):
        lines = [" ".join(rng.choice(_VOCAB) for _ in range(rng.randint(5, 9))) for _ in range(10)]
        chorus = lines[:2]
        lyrics = "\n".join(lines[2:6] + chorus + lines[6:] + chorus + chorus)
        out.append((2000 + i, f"Synthetic {i:03d}", f"Band {i % 23:02d}", "Atlas",
                    f"Band {i % 23:02d}/Synthetic {i:03d}.mp3", lyrics, "lrclib"))
    return out


async def test_the_strict_first_look_gives_the_wide_looks_answer(db_session, lyric_settings, monkeypatch) -> None:
    """``fetch_rows`` (a strict look first, the wide one only when that finds
    fewer than SCORED_ROWS lines) against the single wide look of [Q10], on
    an invented library of lines that share their words: every decision,
    every candidate and every score the same — and the strict look alone
    served a good share of the phrases (else this proves nothing)."""
    import random

    library = _synthetic_library()
    await seed_library(db_session, lyric_settings, library=library)
    out = await lyrics_index.catch_up(budget_sec=120.0)
    assert out["pending"] == 0 and out["indexed"] == len(library)
    rng = random.Random(7)
    texts = [lyrics for *_x, lyrics, _s in library]
    phrases: list[str] = []
    for k in range(90):
        words = rng.choice(rng.choice(texts).split("\n")).split()
        n = rng.randint(4, min(6, len(words)))      # mostly short: the strict look's own
        start = rng.randint(0, len(words) - n)
        phrase = words[start:start + n]
        if k % 3 == 1 and len(phrase) > 4:
            del phrase[rng.randrange(len(phrase))]                 # a word left out
        elif k % 3 == 2:
            phrase = [rng.choice(_VOCAB) for _ in range(rng.randint(4, 6))]   # any words
        phrases.append(" ".join(phrase))

    looks: list[float] = []
    real_fetch_at = ls._fetch_at

    async def counting(session, query, threshold):
        looks.append(threshold)
        return await real_fetch_at(session, query, threshold)

    monkeypatch.setattr(ls, "_fetch_at", counting)

    def key(res):
        return (res.decision, res.reason,
                [(c.ref.type, c.ref.key, round(c.score, 12), c.track_ids) for c in res.candidates])

    # (The mode only changes the deciding, which sees the same lines.)
    strict_only = 0
    for phrase in phrases:
        looks.clear()
        two, _n = await ls.search(db_session, phrase, mode="explicit", play=0.90, ask=0.75)
        await db_session.commit()
        strict_only += looks == [ls.STRICT_TRGM_THRESHOLD]
        monkeypatch.setattr(ls, "fetch_rows", ls.fetch_rows_wide)
        one, _n = await ls.search(db_session, phrase, mode="explicit", play=0.90, ask=0.75)
        await db_session.commit()
        monkeypatch.setattr(ls, "fetch_rows", _two_looks)
        assert key(two) == key(one), len(phrase.split())
    assert strict_only >= 10, strict_only        # else the strict look was never tried alone


_two_looks = ls.fetch_rows
