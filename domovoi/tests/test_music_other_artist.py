"""A song the library has only by ANOTHER artist (owner, 2026-11-11).

"Play Let E.M. Know by T.I." played Bryson Tiller's "Let Em' Know": the
whole phrase sounded close enough to his title. A request that names an
artist never plays a different artist's song silently: it is a song the
library doesn't have, so it streams when a media provider can, and with
none the other version is offered ("I have one by Bryson Tiller. Want
that instead?"). The artist is matched however it is written or said
("T.I.", "TI", "T I", "tee eye", a household "also called" name).

The pure half runs on a small invented library; the DB half drives
``MusicHandler._play`` with the MPD stubs of test_library_match.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from domovoi.clients import mpd as mpd_module
from domovoi.clients.mpd import MPDStubClient
from domovoi.handlers.music import MusicHandler, _artist_named_on
from domovoi.handlers.shared.library_match import build_index, resolve
from domovoi.handlers.shared.spoken_names import names_performer, spelled_letters, spoken_forms
from domovoi.tests.conftest import requires_db
from domovoi.tests.test_library_match import (  # noqa: F401 — fixtures
    _ctx,
    _MissThenFilesMPD,
    _NothingFoundMPD,
    _OneStreamProvider,
    _parked,
    _provider,
    _say,
    _seed,
    music_dir,
    no_podcast,
)

PLAY, ASK = 0.90, 0.75

LIB: list[dict] = [
    {"id": 1, "title": "Let Em' Know", "artist": "Bryson Tiller", "album": "Night Garden",
     "file_path": "bryson/let-em-know.mp3"},
    {"id": 2, "title": "Exchange", "artist": "Bryson Tiller", "album": "Night Garden",
     "file_path": "bryson/exchange.mp3"},
    {"id": 3, "title": "Whatever You Like", "artist": "T.I.", "album": "Paper Route",
     "file_path": "ti/whatever.mp3"},
    {"id": 4, "title": "Go Crazy", "artist": "Jeezy, T.I.", "album": "Late Hours",
     "file_path": "jeezy/go-crazy.mp3"},
    {"id": 5, "title": "Stand By Me", "artist": "Ben E. King", "album": "Spanish Harlem",
     "file_path": "ben/stand.mp3"},
    {"id": 6, "title": "Creep", "artist": "Radiohead", "album": "Pablo Honey",
     "file_path": "rh/creep.mp3"},
    {"id": 7, "title": "Creep", "artist": "TLC", "album": "Crazy Sexy",
     "file_path": "tlc/creep.mp3"},
]

TIP = {"id": 1, "alias": "Tip", "target_type": "artist", "target_key": "ti",
       "target_track_id": None, "target_artist_key": None, "source": "manual",
       "suppressed": False}


def _res(query: dict, aliases=()):
    return resolve(build_index(LIB, list(aliases)), query, kinds=None,
                   play_threshold=PLAY, ask_threshold=ASK)


# ─── Normalization ────────────────────────────────────────────────────────


def test_the_titles_punctuation_reads_the_same() -> None:
    assert spoken_forms("Let E.M. Know")[0] == spoken_forms("Let Em' Know")[0] == "let em know"


@pytest.mark.parametrize("said", ["T.I.", "TI", "T I", "t.i", "ti"])
def test_every_way_of_writing_ti_is_one_name(said: str) -> None:
    assert names_performer(said, "T.I.")
    assert not names_performer(said, "Bryson Tiller")


def test_letter_names_read_as_letters() -> None:
    assert spelled_letters("tee eye") == "t i"
    assert spelled_letters("dee jay kay") == "d j k"
    # Not a spelled name: ordinary words, a single word, plain letters.
    assert spelled_letters("let em know") is None
    assert spelled_letters("eye") is None
    assert spelled_letters("t i") is None


def test_a_surname_names_the_performer_a_word_fragment_does_not() -> None:
    assert names_performer("tiller", "Bryson Tiller")
    assert names_performer("the beatles", "Beatles")
    assert names_performer("Jeezy", "Jeezy, T.I.")
    assert names_performer("T.I.", "Jeezy, T.I.")
    assert not names_performer("ti", "Bryson Tiller")
    # Nothing named, or nothing tagged, contradicts nothing.
    assert names_performer("", "Bryson Tiller") and names_performer("T.I.", "")


# ─── The resolver ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("artist", ["T.I.", "TI", "T I", "ti", "tee eye"])
@pytest.mark.parametrize("title", ["Let E.M. Know", "let em know", "Let Em' Know"])
def test_a_title_by_the_wrong_artist_is_never_played(title: str, artist: str) -> None:
    res = _res({"title": title, "artist": artist})
    assert res.decision == "none" and res.reason == "other_artist"
    assert [(c.ref.label, c.ref.artist_label) for c in res.candidates] == [
        ("Let Em' Know", "Bryson Tiller")
    ]


def test_a_household_alias_names_the_artist_too() -> None:
    res = _res({"title": "let em know", "artist": "tip"}, [TIP])
    assert res.reason == "other_artist"
    res = _res({"title": "go crazy", "artist": "tip"}, [TIP])
    assert res.decision == "play" and res.best.ref.label == "Go Crazy"


@pytest.mark.parametrize("artist", ["T.I.", "T I", "tee eye", "Jeezy"])
def test_the_right_artist_plays(artist: str) -> None:
    title = "go crazy" if artist == "Jeezy" else "whatever you like"
    res = _res({"title": title, "artist": artist})
    assert res.decision == "play"
    assert res.best.ref.type == "title"


def test_the_right_artist_of_a_shared_title_plays_and_a_third_is_other() -> None:
    assert _res({"title": "creep", "artist": "TLC"}).best.ref.artist_label == "TLC"
    assert _res({"title": "creep", "artist": "radiohead"}).best.ref.artist_label == "Radiohead"
    res = _res({"title": "creep", "artist": "nirvana"})
    assert res.reason == "other_artist"
    assert {c.ref.artist_label for c in res.candidates} == {"Radiohead", "TLC"}


def test_by_inside_a_title_still_reaches_the_title() -> None:
    res = _res({"title": "stand", "artist": "me"})
    assert res.decision == "play" and res.best.ref.label == "Stand By Me"


def test_a_partial_artist_name_is_no_contradiction() -> None:
    res = _res({"title": "exchange", "artist": "tiller"})
    assert res.reason != "other_artist"
    assert res.best is not None and res.best.ref.artist_label == "Bryson Tiller"


def test_an_artist_spelled_in_letter_names_plays_the_artist() -> None:
    res = _res({"artist": "tee eye"})
    assert res.decision == "play" and res.best.ref.label == "T.I."


# ─── The search hits MPD and the phone find ──────────────────────────────


def test_a_search_hit_by_another_artist_is_no_hit() -> None:
    assert not _artist_named_on({"title": "exchange", "artist": "ti"}, {"artist": "Bryson Tiller"})
    assert _artist_named_on({"title": "exchange", "artist": "tiller"}, {"artist": "Bryson Tiller"})
    assert _artist_named_on({"title": "exchange"}, {"artist": "Bryson Tiller"})
    assert _artist_named_on({"title": "x", "artist": "T.I."}, {"artist": ["Jeezy", "T.I."]})
    assert _artist_named_on({"title": "x", "artist": "T.I."}, {"title": "x"})


# ─── The handler ──────────────────────────────────────────────────────────


class _SubstringMPD(MPDStubClient):
    """MPD's own tag search: words inside words, so "ti" finds Tiller."""

    def __init__(self) -> None:
        super().__init__()
        self.files_calls: list[list[str]] = []

    async def prepare_search(self, query):
        for t in LIB:
            if all(
                str(v).lower() in str(t.get(k if k != "any" else "title") or "").lower()
                for k, v in query.items()
            ):
                return {"title": t["title"], "artist": t["artist"], "file": t["file_path"]}
        return None

    async def prepare_filename(self, *substrings):
        return None

    async def prepare_files(self, uris, *, start_sec: float = 0.0):
        self.files_calls.append(list(uris))
        return await super().prepare_files(uris, start_sec=start_sec)


@requires_db
async def test_the_wrong_artist_streams_when_a_provider_is_installed(
    db_session, music_dir: Path, no_podcast
) -> None:
    await _seed(db_session, music_dir, LIB)
    mpd = _MissThenFilesMPD()
    mpd_module._clients = {"kitchen": mpd}
    ctx = await _ctx(db_session, online=True)
    with _provider(_OneStreamProvider()) as provider:
        r = await _say(MusicHandler(), "play let e.m. know by t.i.", ctx, db_session)
    assert provider.calls == ["let e.m. know by t.i"]
    assert r.text.startswith("Streaming") and mpd.files_calls == []


@requires_db
async def test_the_wrong_artist_with_no_provider_offers_the_other_version(
    db_session, music_dir: Path, no_podcast
) -> None:
    await _seed(db_session, music_dir, LIB)
    mpd = _MissThenFilesMPD()
    mpd_module._clients = {"kitchen": mpd}
    handler = MusicHandler()
    ctx = await _ctx(db_session, online=True)
    r = await _say(handler, "play let e.m. know by t.i.", ctx, db_session)
    assert r.text == (
        "I don't have Let Em' Know by t.i. I have one by Bryson Tiller. Want that instead?"
    )
    assert r.expect_followup is True and r.music_action is None and mpd.files_calls == []
    parked = await _parked(db_session, ctx)
    assert [c["ref"]["label"] for c in parked["candidates"]] == ["Let Em' Know"]

    # "yes" plays Bryson Tiller's, by its exact path.
    r = await handler.handle_choice_reply(parked["kind"], parked, "yes", ctx, db_session)
    assert r.music_action == "start"
    assert mpd.files_calls[-1] == ["bryson/let-em-know.mp3"]

    # "no" ends it, and the same request then just says so.
    await _say(handler, "play let e.m. know by t.i.", ctx, db_session)
    parked = await _parked(db_session, ctx)
    r = await handler.handle_choice_reply(parked["kind"], parked, "no", ctx, db_session)
    assert r.text == "OK." and r.music_action is None
    r = await _say(handler, "play let e.m. know by t.i.", ctx, db_session)
    assert "I have one by Bryson Tiller." in r.text and not r.expect_followup


@requires_db
async def test_the_wrong_artist_offline_says_it_cant_look_it_up(
    db_session, music_dir: Path, no_podcast
) -> None:
    await _seed(db_session, music_dir, LIB)
    mpd_module._clients = {"kitchen": _NothingFoundMPD()}
    ctx = await _ctx(db_session, online=False)
    with _provider(_OneStreamProvider()) as provider:
        r = await _say(MusicHandler(), "play let em know by ti", ctx, db_session)
    assert provider.calls == []
    assert r.text == (
        "I don't have Let Em' Know by ti, and I can't look it up while Domovoi is offline. "
        "I have one by Bryson Tiller. Want that instead?"
    )


@requires_db
async def test_the_right_artist_still_plays_by_exact_path(
    db_session, music_dir: Path, no_podcast
) -> None:
    await _seed(db_session, music_dir, LIB)
    mpd = _MissThenFilesMPD()
    mpd_module._clients = {"kitchen": mpd}
    ctx = await _ctx(db_session, online=True)
    r = await _say(MusicHandler(), "play let em know by bryson tiller", ctx, db_session)
    assert r.music_action == "start"
    assert mpd.files_calls[-1] == ["bryson/let-em-know.mp3"]


@requires_db
async def test_mpd_finding_ti_inside_tiller_is_not_played(
    db_session, music_dir: Path, no_podcast, monkeypatch
) -> None:
    from domovoi.config import settings

    # The resolver switched off: today's MPD search alone, which matches
    # "ti" inside "Bryson Tiller".
    monkeypatch.setattr(settings, "music_match_enabled", False)
    await _seed(db_session, music_dir, LIB)
    mpd_module._clients = {"kitchen": _SubstringMPD()}
    ctx = await _ctx(db_session, online=True)
    with _provider(_OneStreamProvider()) as provider:
        r = await _say(MusicHandler(), "play exchange by ti", ctx, db_session)
    assert provider.calls == ["exchange by ti"]


@requires_db
async def test_a_phone_never_plays_the_wrong_artist(
    db_session, music_dir: Path, no_podcast, monkeypatch
) -> None:
    from domovoi.config import settings

    await _seed(db_session, music_dir, LIB)
    ctx = await _ctx(db_session, online=True)
    ctx.device_kind = "phone"
    r = await _say(MusicHandler(), "play let e.m. know by t.i.", ctx, db_session)
    assert r.text == "I couldn't find let e.m. know by t.i in your library."
    assert not r.device_actions
    # Today's table search alone ("ti" is inside "tiller") finds nothing either.
    monkeypatch.setattr(settings, "music_match_enabled", False)
    r = await _say(MusicHandler(), "play exchange by ti", ctx, db_session)
    assert r.text == "I couldn't find exchange by ti in your library."
