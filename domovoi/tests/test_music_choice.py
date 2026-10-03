"""The "did you mean …?" dialog (domovoi/handlers/music_choice.py).

A middle-band spoken match asks instead of guessing; the reply comes back
through the REAL router (``route()``), which hands a parked choice the
whole reply before its yes/no pre-empt. The resolver itself is faked
(music_choice_testkit): these tests pin the dialog, not the scoring.

Pinned:

* every reply class — yes, an ordinal, no, "no, play X", a bare name, and
  everything that is not about the choice (routed as a new turn);
* "no, play X" plays X: the router's yes/no regex (``^no\\b``) used to
  read it as a bare "no" and drop the "play X";
* a parked choice expires (CHOICE_TTL_SEC), and is never asked for where
  nobody can answer (the dashboard's play box, chat-mode tools, the
  setting off);
* CHOICE_PARKED, the in-process list that keeps the per-turn cost of all
  this at zero for sessions without a choice: added on park, dropped when
  a yes/no question replaces the slot, consumed by the next turn, pruned;
* after a core restart (CHOICE_PARKED empty) a yes/no reply still reaches
  the choice whole;
* the other confirmations behave as before.
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import text

from domovoi import confirmations
from domovoi.clients.mpd import MPDNotProvisioned
from domovoi.config import settings
from domovoi.db.repositories import SessionRepository
from domovoi.handlers import HANDLER_BY_NAME
from domovoi.handlers.music_choice import (
    CHOICE_TTL_SEC,
    MUSIC_CHOICE_KIND,
    ChoiceReply,
    parse_choice_reply,
)
from domovoi.handlers.shared.library_match import Resolution
from domovoi.models import Context, Intent
from domovoi.router import plan_route, route
from domovoi.tests.conftest import requires_db
from domovoi.tests.music_choice_testkit import (  # noqa: F401 - fake_resolver is a fixture
    ASK,
    GLASS,
    KITES,
    LANTERN,
    MOONLIT,
    NEW_URL,
    cand,
    clear_choice_parked,
    fake_resolver,
)

ROOM = "den"


# ─── Reading a reply (pure) ────────────────────────────────────────────────

PICK0 = ChoiceReply("pick", index=0)
PICK1 = ChoiceReply("pick", index=1)
DECLINE = ChoiceReply("decline")
PASS = ChoiceReply("pass")


def NAME(name: str) -> ChoiceReply:  # noqa: N802 - reads like the others
    return ChoiceReply("name", name=name)


@pytest.mark.parametrize("reply, expected", [
    # yes
    ("yes", PICK0), ("yeah", PICK0), ("yep", PICK0), ("sure", PICK0),
    ("yes please", PICK0), ("yeah, play it", PICK0), ("yes, that one", PICK0),
    ("yes thank you", PICK0), ("sure thing", PICK0), ("that's right", PICK0),
    ("yeah yeah", PICK0), ("okay", PICK0), ("ok please", PICK0), ("that one", PICK0),
    ("play it", PICK0), ("play that one", PICK0), ("go ahead", PICK0),
    # a yes with a command after it: the yes answered; nothing else is run
    ("yes and turn it up", PICK0), ("yes, what time is it", PICK0),
    # ordinals
    ("the second one", PICK1), ("second", PICK1), ("number two", PICK1),
    ("the other one", PICK1), ("the first one", PICK0), ("the last one", PICK1),
    ("yes, the second one", PICK1), ("yeah please the second one", PICK1),
    ("no, the first one", PICK0), ("not that one, the other one", PICK1),
    ("play the second one", PICK1), ("um the second one", PICK1),
    ("no, i meant the other one", PICK1),
    # no
    ("no", DECLINE), ("nope", DECLINE), ("nah", DECLINE), ("no thanks", DECLINE),
    ("no, never mind", DECLINE), ("no no no", DECLINE), ("neither", DECLINE),
    ("none of them", DECLINE), ("not quite", DECLINE), ("wrong", DECLINE),
    ("no, thank you, that's all", DECLINE),
    # a "no" never runs another command off its tail
    ("no, stop", DECLINE), ("no, set a timer for five minutes", DECLINE),
    ("no, what time is it", DECLINE),
    # "no, play X" and its kin: X is what to play
    ("no, play the velvet kites", NAME("the velvet kites")),
    ("no play velvet kites", NAME("velvet kites")),
    ("nope, put on velvet kites", NAME("velvet kites")),
    ("no, i said velvet kites", NAME("velvet kites")),
    ("no, i meant play velvet kites", NAME("velvet kites")),
    ("no, i want to hear velvet kites", NAME("velvet kites")),
    ("no, can you play velvet kites", NAME("velvet kites")),
    ("no, velvet kites", NAME("velvet kites")),
    ("not that one, paper lantern parade", NAME("paper lantern parade")),
    ("yes, play velvet kites", NAME("velvet kites")),
    ("yeah the velvet kites", NAME("the velvet kites")),
    # a bare name
    ("velvet kites", NAME("velvet kites")),
    ("um, velvet kites", NAME("velvet kites")),
    ("the velvet kites please", NAME("the velvet kites")),
    ("no, play the velvet kites, thanks", NAME("the velvet kites")),
    # not about the choice: routed as a turn of its own
    ("play velvet kites", PASS), ("put on paper lantern parade", PASS),
    ("stop", PASS), ("pause the music", PASS), ("never mind", PASS),
    ("what time is it", PASS), ("how many songs do i have", PASS),
    ("who sings this", PASS), ("tell me a joke", PASS),
    ("set a timer for five minutes", PASS),
    ("thank you", PASS), ("you", PASS), ("hmm", PASS), ("i don't know", PASS),
    ("i was thinking we could maybe listen to something a bit more upbeat tonight", PASS),
    ("", PASS),
])
def test_what_a_reply_to_did_you_mean_says(reply: str, expected: ChoiceReply) -> None:
    assert parse_choice_reply(reply, 2) == expected


def test_ordinals_count_what_was_parked_and_what_was_said() -> None:
    # One candidate: there is no "other" one.
    assert parse_choice_reply("the other one", 1) == DECLINE
    assert parse_choice_reply("the second one", 1) == DECLINE
    assert parse_choice_reply("the first one", 1) == PICK0
    # Three parked, two said: "the last one" is the last one SAID; the
    # third is still reachable by number.
    assert parse_choice_reply("the last one", 3) == PICK1
    assert parse_choice_reply("the third one", 3) == ChoiceReply("pick", index=2)
    assert parse_choice_reply("the third one", 2) == DECLINE
    assert parse_choice_reply("yes", 0) == PASS


def test_a_no_with_a_play_request_is_not_a_plain_no_to_the_router() -> None:
    """The bug this dialog fixes, at its source: the router's yes/no reader
    is anchored on the first word, so "no, play X" is a "no" to it."""
    from domovoi.router import _parse_yes_no

    assert _parse_yes_no("no, play the velvet kites") is False
    assert parse_choice_reply("no, play the velvet kites", 2) == NAME("the velvet kites")


# ─── Asking ────────────────────────────────────────────────────────────────


def _music():
    return HANDLER_BY_NAME["music"]


class _Parks:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, session, session_id, **kw) -> None:
        self.calls.append({"session_id": session_id, **kw})


@pytest.fixture
def parks(monkeypatch):
    from domovoi.handlers import music_choice

    p = _Parks()
    monkeypatch.setattr(music_choice, "request_confirmation", p)
    return p


async def test_the_question_names_the_first_two_candidates(parks, fake_resolver) -> None:
    from uuid import uuid4

    sid = uuid4()
    three = Resolution("ask", (cand(GLASS, 0.83), cand(MOONLIT, 0.8), cand(LANTERN, 0.76)),
                       heard="glass harber", reason="fuzzy")
    resp = await _music().ask_did_you_mean(three, {"any": "glass harber"},
                                           Context(room_id=ROOM, session_id=sid), None)
    assert resp.text == "Did you mean Glass Harbor, or the album Moonlit Static by Glass Harbor?"
    assert resp.expect_followup is True
    assert resp.music_action is None and resp.music_stream_url is None
    assert resp.matched_handler == "music"
    assert [c["ref"]["label"] for c in resp.data["choice"]] == [
        "Glass Harbor", "Moonlit Static", "Paper Lantern Parade"]
    (park,) = parks.calls
    assert park["kind"] == MUSIC_CHOICE_KIND and park["handler"] == "music"
    assert park["session_id"] == sid and park["prompt"] == resp.text
    data = park["data"]
    assert len(data["candidates"]) == 3          # the third is parked, not said
    assert data["query"] == {"any": "glass harber"}
    assert data["heard"] == "glass harber" and data["room_id"] == ROOM
    assert time.time() < data["expires_at"] <= time.time() + CHOICE_TTL_SEC


async def test_a_song_is_named_with_its_artist_and_one_candidate_is_asked_alone(
    parks, fake_resolver,
) -> None:
    from uuid import uuid4

    one = Resolution("ask", (cand(LANTERN, 0.8, speak="Paper Lantern Parade"),),
                     heard="paper lantern", reason="fuzzy")
    resp = await _music().ask_did_you_mean(one, {"any": "paper lantern"},
                                           Context(room_id=ROOM, session_id=uuid4()), None)
    assert resp.text == "Did you mean Paper Lantern Parade by The Velvet Kites?"


@pytest.mark.parametrize("why", ["setting_off", "no_session", "not_answerable", "no_candidates"])
async def test_it_never_asks_where_nobody_can_answer(why, parks, fake_resolver, monkeypatch) -> None:
    from uuid import uuid4

    ctx = Context(room_id=ROOM, session_id=uuid4())
    res = ASK
    if why == "setting_off":
        monkeypatch.setattr(settings, "music_choice_enabled", False)
    elif why == "no_session":                       # a chat-mode tool call
        ctx = Context(room_id=ROOM)
    elif why == "not_answerable":                   # the dashboard's play box
        ctx = Context(room_id=ROOM, session_id=uuid4(), answerable=False)
    else:
        res = Resolution("ask", (), heard="glass harber")
    assert await _music().ask_did_you_mean(res, {"any": "glass harber"}, ctx, None) is None
    assert parks.calls == []


async def test_a_question_that_cannot_be_parked_is_not_asked(fake_resolver, monkeypatch) -> None:
    from uuid import uuid4

    from domovoi.handlers import music_choice

    async def _broken(*a, **kw):
        raise RuntimeError("database went away")

    monkeypatch.setattr(music_choice, "request_confirmation", _broken)
    ctx = Context(room_id=ROOM, session_id=uuid4())
    assert await _music().ask_did_you_mean(ASK, {"any": "glass harber"}, ctx, None) is None


def test_the_dashboard_play_box_says_nobody_can_answer() -> None:
    """main._admin_route_intent (the dashboard's play box and room
    buttons) builds its Context with answerable=False. Read from the
    source, so the test doesn't import the whole app."""
    import ast
    from pathlib import Path

    import domovoi

    tree = ast.parse((Path(domovoi.__file__).parent / "main.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "_admin_route_intent")
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
             and getattr(n.func, "id", None) == "Context"]
    assert len(calls) == 1
    kw = {k.arg: k.value for k in calls[0].keywords}
    assert isinstance(kw.get("answerable"), ast.Constant) and kw["answerable"].value is False
    assert Context().answerable is True


# ─── CHOICE_PARKED (no database) ───────────────────────────────────────────


def test_choice_parked_is_consumed_once_and_pruned(monkeypatch) -> None:
    clear_choice_parked()
    try:
        confirmations._note_choice("s1", True)
        assert "s1" in confirmations.CHOICE_PARKED
        assert confirmations.take_parked_choice("s1") is True
        assert confirmations.take_parked_choice("s1") is False
        confirmations._note_choice("s2", True)
        confirmations._note_choice("s2", False)       # a yes/no question took the slot
        assert confirmations.take_parked_choice("s2") is False

        # Sessions that never speak again don't pile up.
        clock = [1000.0]
        monkeypatch.setattr(confirmations.time, "monotonic", lambda: clock[0])
        for i in range(confirmations._CHOICE_PARKED_PRUNE_AT):
            confirmations._note_choice(f"old{i}", True)
        clock[0] += confirmations._CHOICE_PARKED_MAX_AGE_SEC + 1
        confirmations._note_choice("fresh", True)
        assert confirmations.CHOICE_PARKED == {"fresh"}
        assert set(confirmations._CHOICE_PARKED_AT) == {"fresh"}
    finally:
        clear_choice_parked()


# ─── Through the real router ───────────────────────────────────────────────


async def _session(db_session):
    sid = await SessionRepository(db_session).get_or_create(None, ROOM)
    await db_session.commit()
    return sid


async def _say(db_session, sid, said: str, **ctx_kw):
    resp = await route(
        Intent(transcript=said, room_id=ROOM, session_id=sid),
        Context(session_id=sid, room_id=ROOM, online=True, **ctx_kw),
        db_session,
    )
    await db_session.commit()
    return resp


async def _pending(db_session, sid):
    return (await SessionRepository(db_session).get_context(sid) or {}).get("pending_confirmation")


async def _asked(db_session) -> object:
    """A fresh session that has just been asked "did you mean Glass Harbor,
    or the album Moonlit Static by Glass Harbor?" — through route()."""
    sid = await _session(db_session)
    resp = await _say(db_session, sid, "Play glass harber.")
    assert resp.text == "Did you mean Glass Harbor, or the album Moonlit Static by Glass Harbor?"
    assert resp.expect_followup is True and resp.music_action is None
    assert resp.matched_handler == "music" and resp.matched_path == "fast"
    assert str(sid) in confirmations.CHOICE_PARKED
    pending = await _pending(db_session, sid)
    assert pending["kind"] == MUSIC_CHOICE_KIND and pending["handler"] == "music"
    return sid


@requires_db
async def test_yes_plays_the_first_candidate(db_session, fake_resolver) -> None:
    sid = await _asked(db_session)
    resp = await _say(db_session, sid, "Yes.")
    assert fake_resolver.played_labels == ["Glass Harbor"]
    assert resp.text == "Playing Glass Harbor."
    assert resp.music_action == "start" and resp.music_stream_url == NEW_URL
    assert resp.matched_handler == "music" and resp.matched_path == "confirmation"
    assert await _pending(db_session, sid) is None
    assert str(sid) not in confirmations.CHOICE_PARKED
    row = (await db_session.execute(text(
        "SELECT transcript, matched_handler, matched_path FROM intents_log ORDER BY id DESC LIMIT 1"
    ))).one()
    assert tuple(row) == ("Yes.", "music", "confirmation")


@requires_db
@pytest.mark.parametrize("said, label", [
    ("The second one.", "Moonlit Static"),
    ("the other one", "Moonlit Static"),
    ("Yeah, the first one.", "Glass Harbor"),
    ("Moonlit Static.", "Moonlit Static"),            # a bare name: one of them
    ("No, I meant Moonlit Static.", "Moonlit Static"),
])
async def test_an_ordinal_or_a_name_picks_among_the_candidates(
    db_session, fake_resolver, said, label,
) -> None:
    sid = await _asked(db_session)
    resp = await _say(db_session, sid, said)
    assert fake_resolver.played_labels == [label]
    assert resp.matched_path == "confirmation" and resp.music_action == "start"
    # Only "glass harber" was ever resolved: a name that is one of the
    # candidates never goes back to the resolver.
    assert fake_resolver.resolved == [{"any": "glass harber"}]


@requires_db
@pytest.mark.parametrize("said", ["No.", "nope", "No thanks.", "Neither.", "No, stop."])
async def test_no_is_ok_and_plays_nothing(db_session, fake_resolver, said) -> None:
    sid = await _asked(db_session)
    resp = await _say(db_session, sid, said)
    assert resp.text == "OK."
    assert resp.music_action is None and resp.expect_followup is False
    assert resp.matched_path == "confirmation"
    assert fake_resolver.played == []
    assert await _pending(db_session, sid) is None


@requires_db
async def test_no_play_something_else_plays_that_instead(db_session, fake_resolver) -> None:
    """The `\\b` bug: "no, play the velvet kites" was a bare "no" to the
    router's yes/no reader — "OK." and the request was lost. Now it is a
    request: not one of the candidates, so it is played as "play the
    velvet kites" would be."""
    sid = await _asked(db_session)
    resp = await _say(db_session, sid, "No, play the Velvet Kites.")
    assert fake_resolver.resolved[-1] == {"any": "the velvet kites"}
    assert fake_resolver.played_labels == ["The Velvet Kites"]
    assert resp.text == "Playing The Velvet Kites."
    assert resp.music_action == "start"
    assert resp.matched_handler == "music" and resp.matched_path == "confirmation"
    assert await _pending(db_session, sid) is None


@requires_db
async def test_a_bare_name_that_is_no_candidate_is_a_new_request(db_session, fake_resolver) -> None:
    sid = await _asked(db_session)
    resp = await _say(db_session, sid, "Velvet Kites.")
    assert fake_resolver.replies[-1] == ("velvet kites", ["Glass Harbor", "Moonlit Static"])
    assert fake_resolver.resolved[-1] == {"any": "velvet kites"}
    assert fake_resolver.played_labels == ["The Velvet Kites"]
    assert resp.matched_path == "confirmation"


@requires_db
async def test_a_bare_name_the_resolver_is_unsure_of_asks_again(db_session, fake_resolver) -> None:
    fake_resolver.asks["coldharbour"] = Resolution(
        "ask", (cand(KITES, 0.77),), heard="cold harbour", reason="fuzzy")
    sid = await _asked(db_session)
    resp = await _say(db_session, sid, "cold harbour")
    assert resp.text == "Did you mean The Velvet Kites?"
    assert resp.expect_followup is True and resp.music_action is None
    assert resp.matched_path == "confirmation"
    pending = await _pending(db_session, sid)
    assert [c["ref"]["label"] for c in pending["candidates"]] == ["The Velvet Kites"]
    assert str(sid) in confirmations.CHOICE_PARKED
    # …and that question is answered like the first.
    resp = await _say(db_session, sid, "yes")
    assert fake_resolver.played_labels == ["The Velvet Kites"]


@requires_db
async def test_a_bare_name_nothing_matches_goes_on_to_todays_search(db_session, fake_resolver) -> None:
    sid = await _asked(db_session)
    resp = await _say(db_session, sid, "Quiet Ember.")
    assert fake_resolver.resolved[-1] == {"any": "quiet ember"}
    assert fake_resolver.played == []
    assert resp.matched_path == "confirmation" and resp.matched_handler == "music"
    assert not resp.expect_followup
    assert await _pending(db_session, sid) is None


@requires_db
@pytest.mark.parametrize("said, handler, path", [
    ("Set a timer for 5 minutes.", "timer", "fast"),
    ("Play the Velvet Kites.", "music", "fast"),
    ("Never mind.", "dismiss", "fast"),
])
async def test_anything_else_is_routed_as_a_new_turn_and_drops_the_question(
    db_session, fake_resolver, said, handler, path,
) -> None:
    sid = await _asked(db_session)
    resp = await _say(db_session, sid, said)
    assert resp.matched_handler == handler and resp.matched_path == path
    assert await _pending(db_session, sid) is None
    assert str(sid) not in confirmations.CHOICE_PARKED
    # A "yes" now is not an answer to the old question.
    played = list(fake_resolver.played)
    await _say(db_session, sid, "yes")
    assert fake_resolver.played == played


@requires_db
async def test_an_expired_question_is_not_answered(db_session, fake_resolver) -> None:
    sid = await _asked(db_session)
    pending = await _pending(db_session, sid)
    assert pending["expires_at"] <= time.time() + CHOICE_TTL_SEC
    pending["expires_at"] = time.time() - 1          # a minute has gone by
    await SessionRepository(db_session).set_context_key(sid, "pending_confirmation", pending)
    await db_session.commit()
    resp = await _say(db_session, sid, "Yes.")
    assert fake_resolver.played == []
    assert resp.matched_path != "confirmation"
    assert await _pending(db_session, sid) is None


@requires_db
async def test_a_candidate_gone_from_the_library_is_said_so(db_session, fake_resolver) -> None:
    sid = await _asked(db_session)
    fake_resolver.gone.add("Glass Harbor")
    resp = await _say(db_session, sid, "yes")
    assert resp.text == "That one isn't in your library anymore."
    assert resp.matched_path == "confirmation" and fake_resolver.played == []


@requires_db
async def test_a_room_with_no_speakers_yet_is_answered_not_raised(db_session, fake_resolver) -> None:
    sid = await _asked(db_session)
    fake_resolver.raise_on_play = MPDNotProvisioned("no rooms")
    resp = await _say(db_session, sid, "yes")
    assert resp.failure == "no_speakers" and resp.matched_path == "confirmation"


@requires_db
async def test_after_a_restart_a_yes_or_no_still_reaches_the_choice_whole(
    db_session, fake_resolver,
) -> None:
    """CHOICE_PARKED is in-process; a core restart empties it while the
    question stays parked in the session. A yes/no reply still reaches it
    through the yes/no pre-empt — whole, so "no, play X" plays X there
    too. (A bare name can't be told from a new request without reading
    the session on every turn: it is routed as one.)"""
    sid = await _asked(db_session)
    clear_choice_parked()
    resp = await _say(db_session, sid, "No, play the Velvet Kites.")
    assert fake_resolver.played_labels == ["The Velvet Kites"]
    assert resp.matched_path == "confirmation"

    sid = await _asked(db_session)
    clear_choice_parked()
    resp = await _say(db_session, sid, "yes")
    assert fake_resolver.played_labels[-1] == "Glass Harbor"
    assert resp.matched_path == "confirmation"

    sid = await _asked(db_session)
    clear_choice_parked()
    resp = await _say(db_session, sid, "Moonlit Static")
    assert resp.matched_path != "confirmation"
    assert fake_resolver.played_labels[-1] == "Glass Harbor"   # nothing new played


@requires_db
async def test_a_yes_no_question_that_replaces_the_choice_takes_the_reply(
    db_session, fake_resolver,
) -> None:
    sid = await _asked(db_session)
    await confirmations.request_confirmation(
        db_session, sid, kind="core.self_intro", handler="voice_profile",
        data={"name": "Sam", "embedding_b64": ""},
    )
    await db_session.commit()
    assert str(sid) not in confirmations.CHOICE_PARKED
    resp = await _say(db_session, sid, "no")
    assert resp.matched_handler == "voice_profile" and resp.matched_path == "confirmation"
    assert fake_resolver.played == []


@requires_db
async def test_an_ordinary_turn_reads_no_session_context(db_session, fake_resolver, monkeypatch) -> None:
    """The per-turn cost of the choice check is a set lookup: with nothing
    parked, a command turn never reads the session's context."""
    import sys

    sid = await _session(db_session)
    readers: list[str] = []
    real = SessionRepository.get_context

    async def _counting(self, session_id):
        readers.append(sys._getframe(1).f_code.co_name)
        return await real(self, session_id)

    monkeypatch.setattr(SessionRepository, "get_context", _counting)
    resp = await _say(db_session, sid, "set a timer for 5 minutes")
    assert resp.matched_handler == "timer"
    assert "route" not in readers and "_answer_choice" not in readers


@requires_db
async def test_the_dashboard_play_box_never_asks(db_session, fake_resolver) -> None:
    sid = await _session(db_session)
    resp = await _say(db_session, sid, "play glass harber", answerable=False)
    assert not resp.text.startswith("Did you mean")
    assert resp.expect_followup is False
    assert await _pending(db_session, sid) is None
    assert str(sid) not in confirmations.CHOICE_PARKED


@requires_db
async def test_with_asking_switched_off_the_middle_band_goes_to_todays_search(
    db_session, fake_resolver, monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "music_choice_enabled", False)
    sid = await _session(db_session)
    resp = await _say(db_session, sid, "play glass harber")
    assert not resp.text.startswith("Did you mean")
    assert await _pending(db_session, sid) is None


@requires_db
async def test_the_playlist_choice_is_still_a_yes_no_question(db_session, fake_resolver) -> None:
    """Existing confirmations are untouched: no choice_kinds, so the slot
    is read only for a yes/no, and a non-answer leaves it parked."""
    sid = await _session(db_session)
    await confirmations.request_confirmation(
        db_session, sid, kind="core.playlist_choice", handler="playlist",
        data={"candidates": [], "mode": "ordered"},
    )
    await db_session.commit()
    assert str(sid) not in confirmations.CHOICE_PARKED
    resp = await _say(db_session, sid, "set a timer for 5 minutes")
    assert resp.matched_handler == "timer"
    assert (await _pending(db_session, sid))["kind"] == "core.playlist_choice"
    resp = await _say(db_session, sid, "yes")
    assert resp.matched_handler == "playlist" and resp.matched_path == "confirmation"
    assert resp.text == "I lost track of those playlists — say it again?"


@requires_db
async def test_handle_confirmation_answers_a_plain_yes_or_no(db_session, fake_resolver) -> None:
    """The ABC's yes/no entry, for a caller with no reply text."""
    sid = await _asked(db_session)
    pending = await _pending(db_session, sid)
    music = _music()
    ctx = Context(room_id=ROOM, session_id=sid)
    assert (await music.handle_confirmation(MUSIC_CHOICE_KIND, pending, False, ctx, db_session)).text == "OK."
    resp = await music.handle_confirmation(MUSIC_CHOICE_KIND, pending, True, ctx, db_session)
    assert fake_resolver.played_labels == ["Glass Harbor"] and resp.music_action == "start"
    stale = dict(pending, expires_at=time.time() - 1)
    resp = await music.handle_confirmation(MUSIC_CHOICE_KIND, stale, True, ctx, db_session)
    assert resp.text.startswith("I've lost track of that one") and resp.expect_followup is True


def test_a_parked_choice_still_plans_like_any_yes_no_answer() -> None:
    """plan_route() is unchanged: a whole yes/no to a parked music choice
    plans as its confirmation; a correction or a name does not."""
    pending = {"handler": "music", "kind": MUSIC_CHOICE_KIND, "candidates": []}
    plan = plan_route("yes", pending=pending)
    assert plan is not None and plan.path == "confirmation" and plan.handler.name == "music"
    assert plan_route("velvet kites", pending=pending) is None
