"""MusicAliasHandler — "also called" names by voice (band 295).

"when I say X I mean Y" teaches one name at a time (an artist, album or
song can have any number), "forget that name" / "forget the name X"
removes one, "what else is Y called" lists them. A name already meaning
something else is a "Replace it?" question (``core.alias_replace``); a
target the resolver is only fairly sure of is "Do you mean …?"
(``core.alias_target``).

The resolver is the resolver builder's: here it is FAKED (a table of
synthetic names → candidates), as the contract says — this handler codes
against ``library_match``'s API only. Turns go through the real
``router.route()`` so the fast paths, the parked questions and the yes/no
pre-empt are the production ones. The first block is DB-free.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text

from domovoi.clients import mpd as mpd_module
from domovoi.clients.mpd import MPDStubClient
from domovoi.config import settings
from domovoi.db import library_aliases as repo
from domovoi.db.repositories import SessionRepository
from domovoi.handlers import HANDLER_BY_NAME
from domovoi.handlers.music_alias import (
    _ADD_RE,
    _FORGET_LAST_RE,
    _FORGET_RE,
    _LIST_NAMES_RE,
    _LIST_RE,
    MusicAliasHandler,
)
from domovoi.handlers.shared import library_match
from domovoi.handlers.shared.library_match import Candidate, EntityRef, Resolution
from domovoi.handlers.shared.spoken_names import speakable
from domovoi.models import Context, Intent
from domovoi.router import route
from domovoi.tests.conftest import fast_path_winner, requires_db

# ─── DB-free ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("utterance", "alias", "target"),
    [
        ("when i say gramps, i mean hearth ensemble", "gramps", "hearth ensemble"),
        ("when i say the kindlers i mean kindling", "the kindlers", "kindling"),
        ("when i say lullaby i'm talking about this song", "lullaby", "this song"),
        ("when i say lullaby i’m talking about this song", "lullaby", "this song"),
        ("when i say warmies play warm stones", "warmies", "warm stones"),
        ("when i say gramps that means hearth ensemble", "gramps", "hearth ensemble"),
        ("when i say gramps i want the album by the hearth", "gramps", "the album by the hearth"),
    ],
)
def test_the_add_phrase_splits_name_and_target(utterance: str, alias: str, target: str) -> None:
    m = _ADD_RE.match(utterance)
    assert m and (m.group(1), m.group(2)) == (alias, target)


def test_the_other_phrases() -> None:
    for u in ("forget that name", "remove that alias", "delete the last nickname", "forget this name"):
        assert _FORGET_LAST_RE.match(u), u
    assert _FORGET_RE.match("forget the name gramps").group(1) == "gramps"
    assert _FORGET_RE.match("remove the nickname the kindlers").group(1) == "the kindlers"
    assert _LIST_RE.match("what else is hearth ensemble called").group(1) == "hearth ensemble"
    assert _LIST_NAMES_RE.match("what other names does hearth ensemble have").group(1) == "hearth ensemble"
    assert _LIST_NAMES_RE.match("what names does hearth ensemble go by").group(1) == "hearth ensemble"


@pytest.mark.parametrize(
    ("utterance", "winner"),
    [
        ("When I say gramps, I mean Hearth Ensemble.", "music_alias"),
        ("forget that name", "music_alias"),
        ("Please forget that name.", "music_alias"),
        ("forget the name gramps", "music_alias"),
        ("what else is Hearth Ensemble called?", "music_alias"),
        ("What other names does Hearth Ensemble have?", "music_alias"),
        # nobody else's phrase moves
        ("forget that i like jazz", "memory"),
        ("forget that name of my dog", "memory"),
        ("forget that", "dismiss"),
        ("play gramps", "music"),
        ("play the album by the hearth", "music"),
        ("what is this song called", None),
        # an article-led subject is a question about the world, not a name list
        ("what else is a baby goat called", None),
        ("what other names does an owl have", None),
    ],
)
def test_no_poaching_either_way(utterance: str, winner: str | None) -> None:
    assert fast_path_winner(utterance) == winner


def test_the_tool_is_offered_only_on_its_words() -> None:
    h = HANDLER_BY_NAME["music_alias"]
    assert isinstance(h, MusicAliasHandler)
    assert not h.offers_tool("")                   # never on the default offer (prompt-prefix cache)
    assert not h.offers_tool("play some jazz in the kitchen")
    for u in ("kindling is also called the kindlers", "give hearth ensemble the nickname gramps",
              "what other names does it have", "add an alias for this band", "when i say gramps play this"):
        assert h.offers_tool(u), u
    # A request to hear music is music's, whatever name it uses (2026-10-03
    # review: the tool model listed names instead of playing).
    for u in ("i want to hear the band also called the kindlers", "play the one with the nickname gramps",
              "put on that band with the other names"):
        assert not h.offers_tool(u), u
    assert h.chat_exposed is False and h.requires_network == "no"
    assert set(h.tool_schema["parameters"]["properties"]["action"]["enum"]) == {"add", "forget", "list"}


# ─── Through the router, on the lane's database ────────────────────────────

MUSIC = Path(settings.music_dir).expanduser()
LIBRARY = [
    (1, "Hearth Ensemble/By The Hearth/01 Warm Stones.mp3", "Warm Stones", "Hearth Ensemble", "By The Hearth"),
    (2, "Hearth Ensemble/By The Hearth/02 Kindling.mp3", "Kindling", "Hearth Ensemble", "By The Hearth"),
    (3, "Ember Choir/Greatest Hits/01 Glow Street.mp3", "Glow Street", "Ember Choir", "Greatest Hits"),
]


def _cand(kind, key, label, artist=None, ids=(), score=1.0):
    return Candidate(ref=EntityRef(type=kind, key=key, label=label, artist_label=artist),
                     score=score, via="exact", speak=speakable(label), track_ids=tuple(ids))


RESOLVER = {
    "hearth ensemble": ("play", _cand("artist", "hearthensemble", "Hearth Ensemble", ids=(1, 2))),
    "ember choir": ("play", _cand("artist", "emberchoir", "Ember Choir", ids=(3,))),
    "ember quire": ("ask", _cand("artist", "emberchoir", "Ember Choir", score=0.8)),
    "warm stones": ("play", _cand("title", "warmstones|hearthensemble", "Warm Stones", "Hearth Ensemble", ids=(1,))),
    "the album by the hearth": ("play", _cand("album", "bythehearth|hearth ensemble/by the hearth",
                                              "By The Hearth", "Hearth Ensemble", ids=(1, 2))),
}


@pytest_asyncio.fixture
async def voice(db_session, monkeypatch):
    """The synthetic library, V019 applied, a faked resolver, and a
    router-driven ``say(text, room=...)``."""
    from domovoi.tests.library_aliases_testkit import apply_v019

    await apply_v019()
    for tid, rel, title, artist, album in LIBRARY:
        await db_session.execute(
            text("INSERT INTO library_tracks (id, file_path, title, artist, album) VALUES (:i, :f, :t, :a, :al)"),
            {"i": tid, "f": str(MUSIC / rel), "t": title, "a": artist, "al": album},
        )
    await db_session.commit()

    asked: list[dict] = []
    invalidated: list[int] = []

    async def fake_resolve(session, query, *, kinds=None):
        asked.append(dict(query))
        q = (query.get("any") or "").strip().lower()
        hit = RESOLVER.get(q)
        if hit is None:
            return Resolution.none("below_ask", heard=q)
        return Resolution(decision=hit[0], candidates=(hit[1],), heard=q, reason="exact")

    monkeypatch.setattr(library_match, "resolve_request", fake_resolve)
    monkeypatch.setattr(library_match, "invalidate", lambda: invalidated.append(1))

    sessions: dict[str, object] = {}

    async def say(utterance: str, room: str = "kitchen", person: int | None = None):
        ctx = Context(room_id=room, session_id=sessions.get(room), person_id=person, online=True)
        resp = await route(Intent(transcript=utterance, room_id=room), ctx, db_session)
        sessions[room] = resp.session_id
        return resp

    say.asked = asked
    say.invalidated = invalidated
    say.sessions = sessions
    say.db = db_session
    return say


async def _pending(say, room="kitchen"):
    data = await SessionRepository(say.db).get_context(say.sessions[room]) or {}
    return data.get("pending_confirmation")


@requires_db
@pytest.mark.asyncio
async def test_teach_a_name_then_hear_it_back(voice) -> None:
    r = await voice("When I say gramps, I mean Hearth Ensemble.")
    assert (r.matched_handler, r.matched_path) == ("music_alias", "fast")
    assert r.text == "Got it — when you say gramps, I'll play Hearth Ensemble."
    assert voice.asked == [{"any": "hearth ensemble"}]
    assert voice.invalidated == [1]                       # the very next turn sees it
    row = await repo.find_alias(voice.db, "gramps")
    assert (row.source, row.created_by_kind, row.created_by_room, row.target_key) == (
        "voice", "voice", "kitchen", "hearthensemble")
    again = await voice("when i say gramps i mean hearth ensemble")
    assert again.text == "Gramps already means Hearth Ensemble."
    assert voice.invalidated == [1]                        # nothing written, nothing dropped


@requires_db
@pytest.mark.asyncio
async def test_many_names_one_at_a_time_then_list_them(voice) -> None:
    for name in ("gramps", "the kindlers", "hearth band"):
        assert (await voice(f"when i say {name} i mean hearth ensemble")).text.startswith("Got it")
    r = await voice("what else is hearth ensemble called")
    assert r.text == "Hearth Ensemble is also called gramps, the kindlers and hearth band."
    assert r.data["aliases"] == ["gramps", "the kindlers", "hearth band"]
    # asking by one of the names lists the same target's names
    r = await voice("what other names does gramps have")
    assert r.text == "Hearth Ensemble is also called gramps, the kindlers and hearth band."
    r = await voice("what else is ember choir called")
    assert r.text == "Ember Choir doesn't have any other names yet."
    # Nothing in the library: a question about something else — declined,
    # and answered as any question would be (2026-10-03 review).
    r = await voice("what else is nobody here called")
    assert r.matched_handler != "music_alias" and r.matched_path == "qa"


@requires_db
@pytest.mark.asyncio
async def test_a_taken_name_asks_to_replace_it(voice) -> None:
    await voice("when i say gramps i mean hearth ensemble")
    r = await voice("when i say gramps i mean ember choir")
    assert r.text == "Gramps already means Hearth Ensemble. Replace it?"
    assert r.expect_followup is True
    pending = await _pending(voice)
    assert (pending["handler"], pending["kind"]) == ("music_alias", "core.alias_replace")
    yes = await voice("yes")
    assert yes.matched_path == "confirmation"
    assert yes.text == "Got it — when you say gramps, I'll play Ember Choir."
    assert (await repo.find_alias(voice.db, "gramps")).target_key == "emberchoir"
    assert await _pending(voice) is None


@requires_db
@pytest.mark.asyncio
async def test_no_leaves_it_and_another_room_may_not_replace(voice) -> None:
    await voice("when i say gramps i mean hearth ensemble")
    await voice("when i say gramps i mean ember choir")
    assert (await voice("no")).text == "OK, I'll leave it."
    assert (await repo.find_alias(voice.db, "gramps")).target_key == "hearthensemble"
    # asked from another room, unrecognized: the question is asked, the yes refused
    r = await voice("when i say gramps i mean ember choir", room="den")
    assert r.text.endswith("Replace it?")
    r = await voice("yes", room="den")
    assert r.text == "Only an admin can change that one — someone else added it."
    assert (await repo.find_alias(voice.db, "gramps")).target_key == "hearthensemble"


@requires_db
@pytest.mark.asyncio
async def test_a_fair_guess_at_the_target_is_asked_about(voice) -> None:
    r = await voice("when i say the embers i mean ember quire")
    assert r.text == "Do you mean Ember Choir?" and r.expect_followup
    assert (await _pending(voice))["kind"] == "core.alias_target"
    assert await repo.find_alias(voice.db, "the embers") is None       # nothing written yet
    r = await voice("yeah")
    assert r.text == "Got it — when you say the embers, I'll play Ember Choir."
    assert (await repo.find_alias(voice.db, "the embers")).target_key == "emberchoir"


@requires_db
@pytest.mark.asyncio
async def test_an_unknown_target_and_its_own_name(voice) -> None:
    # A target the library has nothing like: not a music name — declined
    # ("when I say goodnight I mean turn off the lights").
    r = await voice("when i say gramps i mean nobody here")
    assert r.matched_handler != "music_alias" and r.matched_path == "qa"
    r = await voice("when i say hearth-ensemble i mean hearth ensemble")
    assert r.text == "That's already what Hearth Ensemble is called."
    assert (await voice.db.execute(text("SELECT count(*) FROM library_aliases"))).scalar_one() == 0


@requires_db
@pytest.mark.asyncio
async def test_a_name_that_shadows_a_library_name_says_instead_of(voice) -> None:
    r = await voice("when i say kindling i mean ember choir")
    assert r.text == "Got it — when you say kindling, I'll play Ember Choir instead of Kindling."
    assert r.data["shadows"] == [{"type": "title", "name": "Kindling"}]


@requires_db
@pytest.mark.asyncio
async def test_songs_and_albums_by_name(voice) -> None:
    r = await voice("when i say warmies play warm stones")
    assert r.text == "Got it — when you say warmies, I'll play Warm Stones by Hearth Ensemble."
    row = await repo.find_alias(voice.db, "warmies")
    assert (row.target_type, row.target_track_id) == ("track", 1)
    r = await voice("when i say hearth record i want the album by the hearth")
    assert r.text == "Got it — when you say hearth record, I'll play the album By The Hearth by Hearth Ensemble."
    row = await repo.find_alias(voice.db, "hearth record")
    assert (row.target_type, row.target_key, row.target_artist_key) == ("album", "bythehearth", "hearthensemble")


@requires_db
@pytest.mark.asyncio
async def test_this_song_artist_and_album_mean_what_is_playing(voice, monkeypatch) -> None:
    stub = MPDStubClient()
    stub._state = "play"
    stub._song = {"file": LIBRARY[1][1], "title": "Kindling", "artist": "Hearth Ensemble"}
    monkeypatch.setitem(mpd_module._clients, "kitchen", stub)
    r = await voice("when i say lullaby i mean this song")
    assert r.text == "Got it — when you say lullaby, I'll play Kindling by Hearth Ensemble."
    assert (await repo.find_alias(voice.db, "lullaby")).target_track_id == 2
    r = await voice("when i say gramps i mean this band")
    assert r.text == "Got it — when you say gramps, I'll play Hearth Ensemble."
    assert (await repo.find_alias(voice.db, "gramps")).target_key == "hearthensemble"
    r = await voice("when i say hearth record i mean this album")
    assert r.text == "Got it — when you say hearth record, I'll play the album By The Hearth by Hearth Ensemble."
    assert voice.asked == []                                            # never needed the resolver
    stub._state = "stop"
    assert (await voice("when i say quiet one i mean this song")).text == "Nothing is playing right now."
    stub._state = "play"
    stub._song = {"file": "https://stream.example/live", "title": "Live"}
    assert "streaming" in (await voice("when i say quiet one i mean this song")).text


@requires_db
@pytest.mark.asyncio
async def test_forget_that_name_and_forget_by_name(voice) -> None:
    await voice("when i say gramps i mean hearth ensemble")
    r = await voice("forget that name")
    assert r.text == "OK — gramps doesn't mean Hearth Ensemble anymore."
    assert await repo.find_alias(voice.db, "gramps") is None
    assert (await voice("forget that name")).text.startswith("I haven't learned a new name")
    await voice("when i say the kindlers i mean hearth ensemble")
    r = await voice("forget the name the kindlers")
    assert r.text == "OK — the kindlers doesn't mean Hearth Ensemble anymore."
    assert (await voice("forget the name warmies")).text == "I don't have a name called warmies."


@requires_db
@pytest.mark.asyncio
async def test_forgetting_someone_elses_name_needs_an_admin(voice) -> None:
    dash = repo.Actor(kind="device", device_id="test-alias-dev-a")
    target = repo.Target(type="artist", name="Hearth Ensemble", key="hearthensemble")
    await repo.add_alias(voice.db, alias="gramps", target=target, actor=dash, source="manual")
    await voice.db.execute(text(
        "INSERT INTO library_aliases (alias, alias_key, target_type, target_key, target_name, source, "
        "created_by_kind) VALUES ('hearth band', 'hearthband', 'artist', 'hearthensemble', 'Hearth Ensemble', "
        "'musicbrainz', 'system')"
    ))
    await voice.db.commit()
    r = await voice("forget the name gramps")
    assert r.text == "Only an admin can remove that one — someone else added it."
    assert await repo.find_alias(voice.db, "gramps") is not None
    # a MusicBrainz name anyone may remove (it is hidden, not deleted)
    r = await voice("forget the name hearth band")
    assert r.text == "OK — hearth band doesn't mean Hearth Ensemble anymore."
    hidden = await repo.get_by_key(voice.db, "hearthband")
    assert hidden is not None and hidden.suppressed


@requires_db
@pytest.mark.asyncio
async def test_a_recognized_speaker_owns_their_names_in_any_room(voice) -> None:
    await voice.db.execute(text("INSERT INTO people (id, name) VALUES (7, 'Test Person'), (8, 'Other Person')"))
    await voice.db.commit()
    await voice("when i say gramps i mean hearth ensemble", room="den", person=7)
    r = await voice("forget the name gramps", room="kitchen", person=8)
    assert r.text.startswith("Only an admin")
    r = await voice("forget the name gramps", room="kitchen", person=7)
    assert r.text == "OK — gramps doesn't mean Hearth Ensemble anymore."


@requires_db
@pytest.mark.asyncio
@pytest.mark.parametrize("question", [
    "What else is the moon called?",
    "What other names does the north star have?",
    "What else is soccer called?",
    "What other names does God have?",
    "What names do the planets go by?",
])
async def test_a_question_about_the_world_is_not_a_names_list(voice, question) -> None:
    """2026-10-03 review: the list phrases have the shape of any question
    about names; 15 of 16 such questions got "I couldn't find the moon in
    your library". Nothing in the library is called that, so the fast path
    declines and the question is answered as a question."""
    r = await voice(question)
    assert r.matched_handler != "music_alias" and r.matched_path == "qa"
    assert "in your library" not in r.text
    assert voice.asked[-1]["any"]                      # the library WAS asked first


@requires_db
@pytest.mark.asyncio
async def test_a_loose_match_is_not_listed_as_if_asked_about(voice) -> None:
    """Only a SURE library match is listed: a middle-band one ("ember
    quire" → Ember Choir, asked about when teaching a name) is declined."""
    r = await voice("what else is ember quire called")
    assert r.matched_path == "qa"


@requires_db
@pytest.mark.asyncio
async def test_when_i_say_something_that_is_not_music_is_declined(voice) -> None:
    r = await voice("when I say goodnight I mean turn off the lights")
    assert r.matched_handler != "music_alias" and r.matched_path == "qa"
    assert (await voice.db.execute(text("SELECT count(*) FROM library_aliases"))).scalar_one() == 0


@requires_db
@pytest.mark.asyncio
async def test_the_tool_call_declines_what_is_not_a_library_name(voice) -> None:
    h = HANDLER_BY_NAME["music_alias"]
    sid = await SessionRepository(voice.db).get_or_create(None, "kitchen")
    ctx = Context(room_id="kitchen", session_id=sid)
    # "my email alias is kam at example dot com": no target at all.
    assert await h.execute_from_tool({"action": "add", "alias": "kam"}, ctx, voice.db) is None
    # "what other names did the moon go by": nothing in the library.
    assert await h.execute_from_tool({"action": "list", "target": "the moon"}, ctx, voice.db) is None
    # "when I say goodnight I mean turn off the lights": nothing in the library.
    assert await h.execute_from_tool(
        {"action": "add", "alias": "goodnight", "target": "turn off the lights"}, ctx, voice.db
    ) is None
    # "forget my email alias": no such household name.
    assert await h.execute_from_tool({"action": "forget", "alias": "email alias"}, ctx, voice.db) is None
    assert await h.execute_from_tool({"action": "rename"}, ctx, voice.db) is None


@requires_db
@pytest.mark.asyncio
async def test_a_declined_tool_call_is_answered_as_a_question(voice, monkeypatch) -> None:
    """Through the router: the tool model picks music_alias for a question
    about the world, the tool declines, and the Q&A model answers."""
    from domovoi.clients.ollama import OllamaStubClient

    routed: list[str] = []

    async def picks_music_alias(self, transcript, tool_schemas):
        routed.append(transcript)
        assert any(s["name"] == "music_alias" for s in tool_schemas)
        return {"handler": "music_alias", "args": {"action": "list", "target": "napoleon"}}

    monkeypatch.setattr(OllamaStubClient, "route", picks_music_alias)
    r = await voice("what other names did napoleon go by")
    assert routed == ["what other names did napoleon go by"]
    assert voice.asked[-1] == {"any": "napoleon"}
    assert r.matched_handler is None and r.matched_path == "qa"
    assert r.text.startswith("(stub qa)")


@requires_db
@pytest.mark.asyncio
async def test_do_you_mean_takes_a_correction(voice) -> None:
    """2026-10-03 review: "Do you mean Ember Choir?" took only yes or no —
    "No, I mean Hearth Ensemble" hit the yes/no pre-empt's ``^no\b`` and
    the correction was lost. It is a choice now: the whole reply counts."""
    r = await voice("when i say the embers i mean ember quire")
    assert r.text == "Do you mean Ember Choir?"
    r = await voice("No, I mean Hearth Ensemble.")
    assert r.matched_path == "confirmation"
    assert r.text == "Got it — when you say the embers, I'll play Hearth Ensemble."
    assert (await repo.find_alias(voice.db, "the embers")).target_key == "hearthensemble"
    # A plain no still leaves it; a reply about something else is its own turn.
    await voice("when i say embers two i mean ember quire")
    assert (await voice("no")).text == "OK, I'll leave it."
    assert await repo.find_alias(voice.db, "embers two") is None
    await voice("when i say embers two i mean ember quire")
    r = await voice("set a timer for 5 minutes")
    assert r.matched_handler == "timer"
    assert await repo.find_alias(voice.db, "embers two") is None


@requires_db
@pytest.mark.asyncio
async def test_the_tool_call_does_the_same(voice) -> None:
    h = HANDLER_BY_NAME["music_alias"]
    sid = await SessionRepository(voice.db).get_or_create(None, "kitchen")
    ctx = Context(room_id="kitchen", session_id=sid)
    r = await h.execute_from_tool({"action": "add", "alias": "gramps", "target": "hearth ensemble"}, ctx, voice.db)
    assert r.text == "Got it — when you say gramps, I'll play Hearth Ensemble."
    r = await h.execute_from_tool({"action": "list", "target": "hearth ensemble"}, ctx, voice.db)
    assert r.text == "Hearth Ensemble is also called gramps."
    r = await h.execute_from_tool({"action": "forget", "alias": "gramps"}, ctx, voice.db)
    assert r.text == "OK — gramps doesn't mean Hearth Ensemble anymore."


# ─── On the REAL resolver (spoken-match integration) ──────────────────────
#
# The real ``library_match.speak_for`` says an entity by a household name
# that sounds like it ("SBTRKT" is said "subtract" once the household
# taught that name) — right for "Playing …" and "Did you mean …?". A reply
# ABOUT that very name must say the target by its own name instead, or it
# reads "when you say subtract, I'll play subtract". Found by the vt.py
# battery on the merged branch (the fake resolver above can't show it).

HEDGE = [(41, "BRMBL/Hedge Songs/01 Thorn Path.mp3", "Thorn Path", "BRMBL", "Hedge Songs")]


@pytest_asyncio.fixture
async def voice_real(db_session):
    """``voice`` without the fakes: V019, a one-track library, the
    process-wide resolver index reset around the test."""
    from domovoi.tests.library_aliases_testkit import apply_v019

    await apply_v019()
    library_match.reset_for_tests()
    for tid, rel, title, artist, album in HEDGE:
        await db_session.execute(
            text("INSERT INTO library_tracks (id, file_path, title, artist, album) VALUES (:i, :f, :t, :a, :al)"),
            {"i": tid, "f": str(MUSIC / rel), "t": title, "a": artist, "al": album},
        )
    await db_session.commit()
    sessions: dict[str, object] = {}

    async def say(utterance: str, room: str = "kitchen"):
        ctx = Context(room_id=room, session_id=sessions.get(room), online=True)
        resp = await route(Intent(transcript=utterance, room_id=room), ctx, db_session)
        sessions[room] = resp.session_id
        return resp

    say.db = db_session
    yield say
    library_match.reset_for_tests()


@requires_db
@pytest.mark.asyncio
async def test_a_name_that_sounds_like_its_target_is_not_read_back_as_the_target(voice_real) -> None:
    r = await voice_real("when i say bramble i mean brmbl")
    assert (r.matched_handler, r.matched_path) == ("music_alias", "fast")
    assert r.text == "Got it — when you say bramble, I'll play BRMBL."
    assert (await voice_real("when i say bramble i mean brmbl")).text == "Bramble already means BRMBL."
    assert (await voice_real("what else is brmbl called")).text == "BRMBL is also called bramble."
    # Everywhere else the household's name IS how the target is said.
    res = await library_match.resolve_request(voice_real.db, {"any": "brmbl"})
    assert res.decision == "play" and res.best is not None and res.best.speak == "bramble"
    r = await voice_real("forget the name bramble")
    assert r.text == "OK — bramble doesn't mean BRMBL anymore."
