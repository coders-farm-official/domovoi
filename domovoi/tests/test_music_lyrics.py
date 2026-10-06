"""Asking for a song by its words, as MusicHandler hears it
(lyrics-build contract §10, tests §15.2).

* the two fast paths ([V1]–[V3]) on the REAL handler registry: what they
  take and the words they capture, and what they must leave alone;
* every cell of the replies table ([V10]) with the lyric resolver faked;
  "what's the song that goes …" offering to play it through the existing
  "did you mean" question ([V12]);
* the tool schema's ``lyrics`` and its precedence ([V4]);
* on Postgres, through the real router: identify → "yes" plays, explicit
  play and ask, and the last resort ([V20]–[V26]) with a music player
  that has nothing by those names — a play says "…, the song with those
  words.", a near phrase asks, anything else falls through to the old
  cascade unchanged, ``allow_choice=False`` and ``avoid`` are respected,
  and "play x from y" is read as said;
* the words are never said back and never logged ([V11], [Q50], [Q51]).

Every lyric, title and name here is invented.
"""

from __future__ import annotations

import logging

import pytest
from sqlalchemy import text

from domovoi import confirmations
from domovoi.clients import mpd as mpd_module
from domovoi.clients.mpd import MPDStubClient
from domovoi.config import settings
from domovoi.db.repositories import SessionRepository
from domovoi.handlers import HANDLER_BY_NAME
from domovoi.handlers import music_choice
from domovoi.handlers.music import (
    _LYRIC_FIND_RE,
    _LYRIC_PLAY_RE,
    MusicHandler,
    _clean_capture,
    _lyric_readings,
)
from domovoi.handlers.music_choice import MUSIC_CHOICE_KIND
from domovoi.handlers.shared import library_match
from domovoi.handlers.shared.library_match import Candidate, EntityRef, Resolution
from domovoi.handlers.shared.spoken_names import lyric_words
from domovoi.models import Context, Intent, Response
from domovoi.router import first_fast_path, normalize_transcript, route, strip_leading_filler
from domovoi.tests.conftest import requires_db
from domovoi.tests.test_lyric_search_db import (  # noqa: F401 - fixtures
    CHORUS_1,
    HERON,
    LIBRARY,
    SAILED,
    SIX_WORDS,
    VERSE_1,
    lyric_library,
    lyric_settings,
)

ROOM = "kitchen"


def _music() -> MusicHandler:
    return HANDLER_BY_NAME["music"]  # type: ignore[return-value]


# ─── [V3]: the fast paths on the real registry ───────────────────────────

MATCHES = [
    ("play the song that goes the lantern hums beside the river door", "play",
     "the lantern hums beside the river door"),
    ("Can you play the song that goes, “the lantern hums beside the river door.”", "play",
     "the lantern hums beside the river door"),
    ("play the one with the lyrics copper kettles sing at dawn", "play", "copper kettles sing at dawn"),
    ("put on that track where she sings paper boats along the hall", "play", "paper boats along the hall"),
    ("i want to hear the song that goes something like every copper kettle sings", "play",
     "every copper kettle sings"),
    ("play me the tune with the words we carried paper boats", "play", "we carried paper boats"),
    ("play the song that goes stand by the river door", "play", "stand by the river door"),
    ("what's the song that goes the lantern hums beside the river door", "find",
     "the lantern hums beside the river door"),
    ("who sings the song that goes copper kettles sing at dawn", "find", "copper kettles sing at dawn"),
    ("what song goes we carried paper boats along the hall", "find", "we carried paper boats along the hall"),
    ("what's the name of the song that goes the river door is open", "find", "the river door is open"),
    ("do i have the song with the words copper kettle at dawn", "find", "copper kettle at dawn"),
    ("which song has the line paper boats are sailing", "find", "paper boats are sailing"),
]

NOT_LYRICS = [
    "play a song that goes with rain", "play the song", "what's the song", "what song is this",
    "play the one by glass harbor", "play something that goes well with dinner",
    "play the one with the guitar solo", "play the velvet kites", "what's playing",
    "play the latest episode of the daily",
]


def _hit(said: str):
    return first_fast_path(strip_leading_filler(normalize_transcript(said)))


@pytest.mark.parametrize(("said", "path", "words"), MATCHES, ids=[m[0] for m in MATCHES])
def test_the_lyric_fast_paths_take_these(said: str, path: str, words: str) -> None:
    hit = _hit(said)
    assert hit is not None, said
    handler, fp, m = hit
    assert handler.name == "music"
    want = MusicHandler._lyrics_play_from_match if path == "play" else MusicHandler._lyrics_find_from_match
    assert fp.method is want
    assert _clean_capture(m.group("lyrics")) == words


@pytest.mark.parametrize("said", NOT_LYRICS)
def test_and_leave_these_alone(said: str) -> None:
    hit = _hit(said)
    assert hit is None or hit[1].method not in (
        MusicHandler._lyrics_play_from_match, MusicHandler._lyrics_find_from_match)


def test_they_come_first_with_an_open_slot() -> None:
    first, second = _music().fast_paths[:2]
    assert (first.pattern, second.pattern) == (_LYRIC_PLAY_RE, _LYRIC_FIND_RE)
    assert first.early_commit is None and second.early_commit is None


# ─── [V4]: the tool schema ────────────────────────────────────────────────


def test_the_tool_schema_has_the_lyrics_field_after_artist() -> None:
    schema = MusicHandler.tool_schema
    assert schema["description"] == (
        "Control local music playback: play (by title, artist, or words from the song's "
        "lyrics), pause, stop, skip, volume, and what's-playing."
    )
    props = list(schema["parameters"]["properties"])
    assert props.index("lyrics") == props.index("artist") + 1
    assert schema["parameters"]["properties"]["lyrics"] == {
        "type": "string",
        # Shorter than the contract's [V4] wording: the routing A/B kept it
        # (the longer one misrouted a music_alias case on qwen3:8b).
        "description": "Words remembered from the song's lyrics, instead of its name",
    }


@pytest.fixture
def recorded(monkeypatch):
    calls: list[tuple] = []

    async def play_lyrics(self, phrase, ctx, session, *, mode):
        calls.append(("lyrics", phrase, mode))
        return Response(text="lyrics")

    async def play(self, query, ctx, session, **kw):
        calls.append(("play", dict(query)))
        return Response(text="play")

    monkeypatch.setattr(MusicHandler, "_play_lyrics", play_lyrics)
    monkeypatch.setattr(MusicHandler, "_play", play)
    return calls


async def test_lyrics_win_over_a_title_in_a_tool_call(recorded) -> None:
    ctx = Context(room_id=ROOM)
    await _music().execute_from_tool(
        {"action": "play", "lyrics": " paper boats sailing down the hall ", "title": "boats"}, ctx, None)
    await _music().execute_from_tool({"action": "play", "lyrics": "  ", "title": "glass harbor"}, ctx, None)
    await _music().execute_from_tool({"action": "play", "lyrics": ["not", "words"], "artist": "glass harbor"},
                                     ctx, None)
    assert recorded == [
        ("lyrics", "paper boats sailing down the hall", "explicit"),
        ("play", {"any": "glass harbor"}),
        ("play", {"artist": "glass harbor"}),
    ]


# ─── [V21]: the last resort's readings ────────────────────────────────────


def test_the_last_resort_reads_a_request_these_ways() -> None:
    assert _lyric_readings({"any": "the song we carried paper boats"}) == [("we carried paper boats", None)]
    assert _lyric_readings({"any": "the one the river door is open"}) == [("the river door is open", None)]
    assert _lyric_readings({"any": "the song"}) == [("the song", None)]
    assert _lyric_readings({"title": "we sailed", "artist": "the harbor at noon"},
                           "we sailed from the harbor at noon") == [
        ("we sailed from the harbor at noon", None), ("we sailed", "the harbor at noon")]
    assert _lyric_readings({"title": "we sailed", "artist": "the harbor"}) == [
        ("we sailed by the harbor", None), ("we sailed", "the harbor")]
    assert _lyric_readings({"title": "copper kettle"}) == [("copper kettle", None)]
    assert _lyric_readings({"artist": "glass harbor"}) == []


# ─── [V10]: every reply, the resolver faked ───────────────────────────────

LANTERN = EntityRef(type="title", key="lanternsong|theexampleband", label="Lantern Song",
                    artist_label="The Example Band")
COPPER = EntityRef(type="title", key="coppermorning|glassharbor", label="Copper Morning",
                   artist_label="Glass Harbor")
ATTIC = EntityRef(type="title", key="atticlight|thevelvetkites", label="Attic Light",
                  artist_label="The Velvet Kites")
PHRASE = "the lantern hums beside the river door"


def _cand(ref: EntityRef, score: float) -> Candidate:
    return Candidate(ref=ref, score=score, via="lyrics", speak=ref.label, track_ids=(1,))


class _Parks:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, session, session_id, **kw) -> None:
        self.calls.append({"session_id": session_id, **kw})


@pytest.fixture
def faked(monkeypatch):
    """resolve_lyrics answers what the test sets; play_candidate and the
    parking of a question are recorded."""
    state: dict = {"res": Resolution.none("below_ask"), "asked": [], "played": [], "parks": _Parks()}

    async def resolve_lyrics(session, phrase, *, mode, **kw):
        state["asked"].append((phrase, mode, kw))
        return state["res"]

    async def play_candidate(self, candidate, ctx, session, *, heard="", lyrics_note=False):
        state["played"].append((candidate.ref, heard, lyrics_note))
        return Response(text=f"Playing {candidate.speak}.", music_action="start")

    monkeypatch.setattr(library_match, "resolve_lyrics", resolve_lyrics)
    monkeypatch.setattr(MusicHandler, "play_candidate", play_candidate)
    monkeypatch.setattr(music_choice, "request_confirmation", state["parks"])
    monkeypatch.setattr(settings, "music_choice_enabled", True)
    music_choice.forget_declined()
    yield state
    music_choice.forget_declined()


def _ctx(**kw) -> Context:
    from uuid import uuid4

    return Context(room_id=ROOM, session_id=kw.pop("session_id", uuid4()), **kw)


NONE_TEXTS = [
    ("disabled", "Finding songs by their words is turned off."),
    ("no_lyrics", "I don't have the words to your songs yet."),
    ("too_short", "Tell me a few more of the words and I'll look."),
    ("too_common", "A lot of your songs have those words. Tell me a few more of them."),
    ("below_ask", "I couldn't find a song in your library with those words."),
    ("below_play", "I couldn't find a song in your library with those words."),
    ("declined", "I couldn't find a song in your library with those words."),
    ("error", "I couldn't find a song in your library with those words."),
]


@pytest.mark.parametrize("mode", ["explicit", "identify"])
@pytest.mark.parametrize(("reason", "said"), NONE_TEXTS)
async def test_what_it_says_when_it_finds_nothing(faked, mode, reason, said) -> None:
    faked["res"] = Resolution.none(reason, heard=PHRASE)
    resp = await _music()._play_lyrics(PHRASE, _ctx(), None, mode=mode)
    assert resp.text == said
    assert resp.music_action is None and resp.expect_followup is False
    assert faked["asked"] == [(PHRASE, mode, {})]
    assert faked["played"] == [] and faked["parks"].calls == []


async def test_explicit_play_plays_it_without_echoing_the_words(faked) -> None:
    faked["res"] = Resolution("play", (_cand(LANTERN, 1.0),), heard=PHRASE, reason="lyrics")
    resp = await _music()._play_lyrics(PHRASE, _ctx(), None, mode="explicit")
    assert resp.text == "Playing Lantern Song."
    assert faked["played"] == [(LANTERN, "", False)]


async def test_explicit_ask_asks_did_you_mean_and_parks_the_words(faked) -> None:
    sid = _ctx().session_id
    faked["res"] = Resolution("ask", (_cand(LANTERN, 0.83), _cand(COPPER, 0.8), _cand(ATTIC, 0.79)),
                              heard=PHRASE, reason="lyrics")
    resp = await _music()._play_lyrics(PHRASE, _ctx(session_id=sid), None, mode="explicit")
    assert resp.text == "Did you mean Lantern Song by The Example Band, or Copper Morning by Glass Harbor?"
    assert resp.expect_followup is True and resp.music_action is None
    (park,) = faked["parks"].calls
    assert park["kind"] == MUSIC_CHOICE_KIND and park["session_id"] == sid
    data = park["data"]
    assert data["query"] == {"lyrics": PHRASE} and data["heard"] == PHRASE
    assert [c["ref"]["label"] for c in data["candidates"]] == ["Lantern Song", "Copper Morning", "Attic Light"]
    faked["res"] = Resolution("ask", (_cand(LANTERN, 0.83),), heard=PHRASE, reason="lyrics")
    resp = await _music()._play_lyrics(PHRASE, _ctx(), None, mode="explicit")
    assert resp.text == "Did you mean Lantern Song by The Example Band?"


@pytest.mark.parametrize("why", ["not_answerable", "no_session", "setting_off", "turned_down"])
async def test_explicit_ask_where_nobody_can_answer_says_it_found_nothing(faked, monkeypatch, why) -> None:
    ctx = _ctx()
    faked["res"] = Resolution("ask", (_cand(LANTERN, 0.83),), heard=PHRASE, reason="lyrics")
    if why == "not_answerable":
        ctx = _ctx(answerable=False)
    elif why == "no_session":
        ctx = Context(room_id=ROOM)
    elif why == "setting_off":
        monkeypatch.setattr(settings, "music_choice_enabled", False)
    else:
        music_choice.remember_declined(ctx.session_id, PHRASE, [LANTERN])
    resp = await _music()._play_lyrics(PHRASE, ctx, None, mode="explicit")
    assert resp.text == "I couldn't find a song in your library with those words."
    assert faked["parks"].calls == []


async def test_identify_names_the_song_and_offers_to_play_it(faked) -> None:
    sid = _ctx().session_id
    faked["res"] = Resolution("play", (_cand(LANTERN, 1.0),), heard=PHRASE, reason="lyrics")
    resp = await _music()._play_lyrics(PHRASE, _ctx(session_id=sid), None, mode="identify")
    assert resp.text == "That's Lantern Song by The Example Band. Want me to play it?"
    assert resp.expect_followup is True and resp.music_action is None
    assert [c["ref"]["label"] for c in resp.data["choice"]] == ["Lantern Song"]
    (park,) = faked["parks"].calls
    assert park["kind"] == MUSIC_CHOICE_KIND and park["prompt"] == resp.text
    assert park["data"]["query"] == {"lyrics": PHRASE} and park["data"]["heard"] == PHRASE
    assert len(park["data"]["candidates"]) == 1
    assert faked["played"] == []                     # naming it plays nothing


async def test_identify_in_the_ask_band_says_it_might_be(faked) -> None:
    faked["res"] = Resolution("ask", (_cand(LANTERN, 0.83),), heard=PHRASE, reason="lyrics")
    resp = await _music()._play_lyrics(PHRASE, _ctx(), None, mode="identify")
    assert resp.text == "It might be Lantern Song by The Example Band. Want me to play it?"
    faked["res"] = Resolution("ask", (_cand(LANTERN, 0.83), _cand(COPPER, 0.8), _cand(ATTIC, 0.79)),
                              heard=PHRASE, reason="lyrics")
    resp = await _music()._play_lyrics(PHRASE, _ctx(), None, mode="identify")
    assert resp.text == ("It might be Lantern Song by The Example Band, or Copper Morning by "
                         "Glass Harbor. Want me to play one?")
    assert len(faked["parks"].calls[-1]["data"]["candidates"]) == 3


@pytest.mark.parametrize("why", ["not_answerable", "no_session", "setting_off", "cannot_park"])
async def test_identify_without_an_offer_answers_the_sentence_alone(faked, monkeypatch, why) -> None:
    ctx = _ctx()
    if why == "not_answerable":
        ctx = _ctx(answerable=False)
    elif why == "no_session":
        ctx = Context(room_id=ROOM)
    elif why == "setting_off":
        monkeypatch.setattr(settings, "music_choice_enabled", False)
    else:
        async def broken(*a, **kw):
            raise RuntimeError("database went away")

        monkeypatch.setattr(music_choice, "request_confirmation", broken)
    faked["res"] = Resolution("play", (_cand(LANTERN, 1.0),), heard=PHRASE, reason="lyrics")
    resp = await _music()._play_lyrics(PHRASE, ctx, None, mode="identify")
    assert resp.text == "That's Lantern Song by The Example Band."
    assert resp.expect_followup is False
    faked["res"] = Resolution("ask", (_cand(LANTERN, 0.83), _cand(COPPER, 0.8)), heard=PHRASE, reason="lyrics")
    resp = await _music()._play_lyrics(PHRASE, ctx, None, mode="identify")
    assert resp.text == "It might be Lantern Song by The Example Band, or Copper Morning by Glass Harbor."


def test_a_lyric_question_is_logged_by_its_word_count() -> None:
    res = Resolution("ask", (_cand(LANTERN, 0.83),), heard=PHRASE, reason="lyrics")
    assert music_choice._heard_for_log(res, {"lyrics": PHRASE}) == "<lyrics: 7 words>"
    assert music_choice._heard_for_log(res, {"any": PHRASE}) == "<lyrics: 7 words>"   # the last resort
    name = Resolution("ask", (Candidate(ref=COPPER, score=0.8, via="fuzzy", speak="Copper Morning"),),
                      heard="copper mourning")
    assert music_choice._heard_for_log(name, {"any": "copper mourning"}) == "'copper mourning'"


# ─── Through the real router, on Postgres ─────────────────────────────────


class _NamesNotFoundMPD(MPDStubClient):
    """A music player with nothing by the names asked for: MPD's tag and
    file-name searches miss; exact-path plays (the resolvers') work."""

    def __init__(self) -> None:
        super().__init__()
        self.files_calls: list[list[str]] = []

    async def prepare_search(self, query):
        return None

    async def prepare_filename(self, *substrings):
        return None

    async def prepare_files(self, uris, *, start_sec: float = 0.0):
        self.files_calls.append(list(uris))
        return await super().prepare_files(uris, start_sec=start_sec)


def _path_of(track_id: int) -> str:
    return next(rel for tid, _t, _a, _al, rel, _l, _s in LIBRARY if tid == track_id)


ALL_LINES = sorted({
    " ".join(lyric_words(line))
    for *_x, lyrics, _s in LIBRARY if lyrics
    for line in lyrics.split("\n") if len(line.split()) >= 5
})


def _no_lyrics_in_logs(caplog, *, said: str = "", name_request: bool = False) -> None:
    """No log line holds a lyric line of any song, nor the words said.
    The one exception, for a "play <words>" that went the whole way to the
    last resort: the spoken-name resolver's own request line ("spoken-name
    match '<request>' → none"), written before anything knew the words were
    a lyric — the household's request as a name, logged as every "play X"
    always was (contract [C8]); it holds no song's other lines."""
    said_words = " ".join(lyric_words(said))
    # Not vacuous: the lyric search's own line was captured.
    assert any(r.getMessage().startswith("lyric match (") for r in caplog.records)
    for rec in caplog.records:
        msg = rec.getMessage()
        norm = f" {' '.join(lyric_words(msg))} "
        allowed = name_request and msg.startswith("spoken-name match")
        for line in ALL_LINES:
            if f" {line} " in norm and not (allowed and line in said_words):
                pytest.fail(f"a lyric line reached the log ({rec.name}): {msg[:60]}…")
        if said_words and f" {said_words} " in norm and not allowed:
            pytest.fail(f"the words said reached the log ({rec.name}): {msg[:60]}…")


def test_the_log_check_catches_a_line_and_the_words() -> None:
    class _Caplog:
        def __init__(self, *messages: str) -> None:
            self.records = [logging.LogRecord("domovoi.x", logging.INFO, __file__, 1, m, None, None)
                            for m in ("lyric match (explicit, 7 words) → none (below_ask, 3 ms)", *messages)]

    _no_lyrics_in_logs(_Caplog("music: fine"), said=CHORUS_1)
    for leaked in (f"debug: {CHORUS_1.upper()}!", f"heard {VERSE_1!r}"):
        with pytest.raises(pytest.fail.Exception):
            _no_lyrics_in_logs(_Caplog(leaked), said="")
    with pytest.raises(pytest.fail.Exception):
        _no_lyrics_in_logs(_Caplog("asked about the river door is open tonight"),
                           said="the river door is open tonight")
    # The name resolver's request line is the one place the words said may be.
    _no_lyrics_in_logs(_Caplog(f"spoken-name match {SIX_WORDS!r} → none (below_ask, 2 ms)"),
                       said=SIX_WORDS, name_request=True)
    with pytest.raises(pytest.fail.Exception):
        _no_lyrics_in_logs(_Caplog(f"spoken-name match {SIX_WORDS!r} → none (below_ask, 2 ms)"),
                           said=SIX_WORDS)


@pytest.fixture
def player(monkeypatch):
    stub = _NamesNotFoundMPD()
    monkeypatch.setitem(mpd_module._clients, ROOM, stub)
    from domovoi.tests.music_choice_testkit import clear_choice_parked

    clear_choice_parked()
    yield stub
    clear_choice_parked()


async def _session(db_session):
    sid = await SessionRepository(db_session).get_or_create(None, ROOM)
    await db_session.commit()
    return sid


async def _say(db_session, sid, said: str, **ctx_kw):
    resp = await route(
        Intent(transcript=said, room_id=ROOM, session_id=sid),
        Context(session_id=sid, room_id=ROOM, online=False, **ctx_kw),
        db_session,
    )
    await db_session.commit()
    return resp


async def _pending(db_session, sid):
    return (await SessionRepository(db_session).get_context(sid) or {}).get("pending_confirmation")


@requires_db
async def test_whats_the_song_that_goes_then_yes_plays_it(db_session, lyric_library, player, caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="domovoi")
    sid = await _session(db_session)
    resp = await _say(db_session, sid, f"What's the song that goes {CHORUS_1}?")
    assert resp.text == "That's Lantern Song by The Example Band. Want me to play it?"
    assert resp.expect_followup is True and resp.music_action is None
    assert resp.matched_handler == "music" and resp.matched_path == "fast"
    pending = await _pending(db_session, sid)
    assert pending["kind"] == MUSIC_CHOICE_KIND and pending["query"] == {"lyrics": CHORUS_1}
    assert str(sid) in confirmations.CHOICE_PARKED
    assert player.files_calls == []                         # naming it played nothing

    resp = await _say(db_session, sid, "Yes.")
    assert resp.text == "Playing Lantern Song by The Example Band."
    assert resp.music_action == "start" and resp.matched_path == "confirmation"
    assert player.files_calls == [[_path_of(1)]]
    assert await _pending(db_session, sid) is None
    # What Domovoi said, as the conversation log keeps it: never the words.
    said_back = [r[0] for r in (await db_session.execute(text(
        "SELECT assistant_text FROM conversation_log ORDER BY id"))).all()]
    assert said_back and not any("hums beside" in t for t in said_back)
    _no_lyrics_in_logs(caplog, said=CHORUS_1)


@requires_db
async def test_identify_then_an_ordinal_or_a_no(db_session, lyric_library, player) -> None:
    sid = await _session(db_session)
    # Two songs share the line equally (score, repeats, tracks): the key
    # orders them.
    resp = await _say(db_session, sid, "who sings the song that goes the mailbox keeps a secret for the moon")
    assert resp.text == (
        "It might be Attic Light by The Velvet Kites, or Copper Morning by Glass Harbor. Want me to play one?")
    resp = await _say(db_session, sid, "the second one")
    assert resp.text == "Playing Copper Morning by Glass Harbor."
    assert resp.music_action == "start" and player.files_calls == [[_path_of(3)]]
    resp = await _say(db_session, sid, f"what song goes {HERON}")
    assert resp.text == "That's Attic Light by The Velvet Kites. Want me to play it?"
    resp = await _say(db_session, sid, "No.")
    assert resp.text == "OK." and len(player.files_calls) == 1


@requires_db
async def test_play_the_song_that_goes_plays_and_a_near_phrase_asks(db_session, lyric_library, player, caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="domovoi")
    sid = await _session(db_session)
    resp = await _say(db_session, sid, f"Play the song that goes, {VERSE_1}.")
    assert resp.text == "Playing Lantern Song by The Example Band."
    assert resp.music_action == "start" and player.files_calls == [[_path_of(1)]]

    near = "the lantern hums beside river door"
    resp = await _say(db_session, sid, f"play the one with the words {near}")
    assert resp.text == "Did you mean Lantern Song by The Example Band?"
    assert resp.expect_followup is True
    assert (await _pending(db_session, sid))["query"] == {"lyrics": near}
    resp = await _say(db_session, sid, "yes")
    assert resp.music_action == "start" and len(player.files_calls) == 2
    _no_lyrics_in_logs(caplog, said=VERSE_1)
    _no_lyrics_in_logs(caplog, said=near)


@requires_db
async def test_the_last_resort_plays_an_exact_line_and_says_so(db_session, lyric_library, player, caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="domovoi")
    sid = await _session(db_session)
    resp = await _say(db_session, sid, f"Play {SIX_WORDS}.")
    assert resp.text == "Playing Copper Morning by Glass Harbor, the song with those words."
    assert resp.music_action == "start" and player.files_calls == [[_path_of(3)]]
    assert resp.data["resolved"]["via"] == "lyrics"
    _no_lyrics_in_logs(caplog, said=SIX_WORDS, name_request=True)


@requires_db
async def test_the_last_resort_asks_about_a_near_or_short_phrase(db_session, lyric_library, player, caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="domovoi")
    sid = await _session(db_session)
    resp = await _say(db_session, sid, "play a sleepy heron counts the")     # exact, five words
    assert resp.text == "Did you mean Attic Light by The Velvet Kites?"
    assert resp.expect_followup is True and player.files_calls == []
    pending = await _pending(db_session, sid)
    assert pending["kind"] == MUSIC_CHOICE_KIND and pending["query"] == {"any": "a sleepy heron counts the"}
    resp = await _say(db_session, sid, "yes please")
    assert resp.music_action == "start" and player.files_calls == [[_path_of(4)]]
    _no_lyrics_in_logs(caplog, said="a sleepy heron counts the", name_request=True)


@requires_db
async def test_play_x_from_y_is_read_as_it_was_said(db_session, lyric_library, player) -> None:
    sid = await _session(db_session)
    # "play we sailed from the harbor at noon" splits as title / artist;
    # the words as said are an exact line.
    resp = await _say(db_session, sid, f"play {SAILED}")
    assert resp.text == "Playing Copper Morning by Glass Harbor, the song with those words."
    # Rebuilt as "<title> by <artist>" (no `said`), it is only near: asked.
    ctx = Context(session_id=sid, room_id=ROOM, online=False)
    resp = await _music()._play({"title": "we sailed", "artist": "the harbor at noon"}, ctx, db_session)
    await db_session.commit()
    assert resp.text == "Did you mean Copper Morning by Glass Harbor?"


@requires_db
async def test_anything_else_falls_through_to_the_old_cascade(db_session, lyric_library, player) -> None:
    sid = await _session(db_session)
    resp = await _say(db_session, sid, "play quartz meridian lantern dusk")
    assert resp.text == "I couldn't find anything called quartz meridian lantern dusk."
    resp = await _say(db_session, sid, "play the river")                     # too short to look
    assert resp.text == "I couldn't find anything called the river."
    resp = await _say(db_session, sid, "play glass harbor")                  # a name still wins
    assert resp.text.startswith("Playing ") and "the song with those words" not in resp.text
    assert player.files_calls


@requires_db
async def test_allow_choice_and_avoid_are_respected(db_session, lyric_library, player) -> None:
    sid = await _session(db_session)
    ctx = Context(session_id=sid, room_id=ROOM, online=False)
    handler = _music()
    resp = await handler._play({"any": "a sleepy heron counts the"}, ctx, db_session, allow_choice=False)
    assert resp.text == "I couldn't find anything called a sleepy heron counts the."
    first = await library_match.resolve_lyrics(db_session, SIX_WORDS, mode="fallback")
    resp = await handler._play({"any": SIX_WORDS}, ctx, db_session, avoid=(first.best.ref,))
    await db_session.commit()
    assert resp.text == f"I couldn't find anything called {SIX_WORDS}."
    assert player.files_calls == []


@requires_db
async def test_switched_off(db_session, lyric_library, player, monkeypatch) -> None:
    monkeypatch.setattr(settings, "lyrics_search_enabled", False)
    sid = await _session(db_session)
    resp = await _say(db_session, sid, f"play {SIX_WORDS}")
    assert resp.text == f"I couldn't find anything called {SIX_WORDS}."      # never looks in lyrics
    resp = await _say(db_session, sid, f"play the song that goes {CHORUS_1}")
    assert resp.text == "Finding songs by their words is turned off."
    assert player.files_calls == []


@requires_db
async def test_the_dashboard_play_box_never_asks(db_session, lyric_library, player) -> None:
    sid = await _session(db_session)
    resp = await _say(db_session, sid, "play the song that goes the lantern hums beside river door",
                      answerable=False)
    assert resp.text == "I couldn't find a song in your library with those words."
    resp = await _say(db_session, sid, "what's the song that goes the lantern hums beside the river door",
                      answerable=False)
    assert resp.text == "That's Lantern Song by The Example Band."
    assert await _pending(db_session, sid) is None
