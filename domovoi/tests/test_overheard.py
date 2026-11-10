"""Long captures that are background talk, not a request (false wakes,
2026-11-09; domovoi/overheard.py).

Rooms woke on a TV or a phone saying "Jarvis"; the media never paused, so
the capture ran to its 30 s cap, the core answered what it heard, offered
"Want me to check that online?" and reopened the mic with no wake word.

* the rule, on transcripts: a command said first still works with the TV
  talking after it, a genuine long question is still answered, chatter is
  ignored (the transcripts below are the shapes the diagnosis describes:
  the 30 s office capture of TV dialogue, the "Jarvis, ..." meme, people
  talking to an Echo; the live history itself wasn't readable, see
  design-notes/FALSE-WAKES-DIAGNOSIS.md);
* the voice turn: an ignored capture is never routed (no action handler can
  run), the satellite gets a bare ``response_end`` (back to idle: no error,
  no apology, no stall), the history entry is closed ``overheard``;
* no follow-up mic after a long capture, while a normal short turn keeps it;
* the router: no tool model and no follow-up offers on a long capture (DB).
"""

from __future__ import annotations

import asyncio
import json
import types

import pytest

from domovoi import overheard, streaming, turn_history, turn_watchdog
from domovoi.config import settings
from domovoi.models import Context, Intent, Response
from domovoi.overheard import ANSWER, COMMAND, IGNORE, ROUTE, screen
from domovoi.streaming import StreamSession
from domovoi.tests.conftest import requires_db

CAP = "max_record_seconds"
PAUSE = "vad_silence_after_speech"

# A TV / TikTok capture that ran to the cap: no command, no question.
TV_DIALOGUE = (
    "Okay so this is the part where everybody loses their minds, watch this, "
    "three, two, one, oh my god, did you see that, no way, that is insane, I "
    "cannot believe he actually did that in front of everyone at the wedding, "
    "and her mother was right there."
)
JARVIS_MEME = (
    "Jarvis, I need you to run the numbers on this suit. No, no, the other one. "
    "Sir, the suit is not ready. I do not care, we are going anyway, get the "
    "car, Pepper is going to kill me, she is going to absolutely kill me."
)
ECHO_TALK = (
    "Alexa, what's on my calendar tomorrow? You have two events tomorrow, the "
    "first one is the dentist at nine in the morning and the second one is "
    "dinner with Sam at seven in the evening."
)
TV_ARGUMENT = (
    "What is going on here? Nobody told me about this and I want answers right "
    "now, Mark, you said you would handle it and now look at the mess we are in "
    "with the client and the lawyers."
)
TV_ACTION_WORDS = (
    "Send the invite for Thursday to everyone on the team and make sure Karen "
    "is included this time because last time she was left out and she was "
    "furious about it for a week straight."
)
DTMF_WITH_TV = (
    "Play DTMF by Bad Bunny. And then she told me she was never coming back to "
    "this town, I mean can you believe it, after everything we did for her and "
    "the whole family, honestly."
)
LONG_QUESTION = (
    "I have been wondering for a while now about something my daughter asked me "
    "at dinner, which is why the sky looks blue during the day but turns orange "
    "and red at sunset, and what actually makes the colors change like that "
    "over the evening and why it is different on cloudy days, can you explain it "
    "simply?"
)


def _screen(text: str, *, exit_reason=CAP, audio=30.0, trigger="wake_word", pending=None):
    return screen(text, trigger=trigger, exit_reason=exit_reason, audio_seconds=audio,
                  pending=pending, bot_name="Domovoi")


# ─── What is long ───────────────────────────────────────────────────────


def test_a_short_turn_is_untouched() -> None:
    v = _screen("what time is it", exit_reason=PAUSE, audio=2.0)
    assert (v.action, v.text, v.long) == (ROUTE, "what time is it", False)


def test_long_is_the_cap_or_twenty_seconds_or_sixty_words() -> None:
    assert overheard.is_long("hi", exit_reason=CAP, audio_seconds=12.0)
    assert overheard.is_long("hi", exit_reason=PAUSE, audio_seconds=20.0)
    assert not overheard.is_long("hi", exit_reason=PAUSE, audio_seconds=19.9)
    assert overheard.is_long("word " * 60, exit_reason=PAUSE, audio_seconds=8.0)
    assert not overheard.is_long("word " * 59, exit_reason=PAUSE, audio_seconds=8.0)
    # An older satellite sends no exit reason: a 30 s capture is capped.
    assert overheard.is_capped(None, 30.0) and not overheard.is_capped(None, 21.0)


# ─── A command said first still works ───────────────────────────────────


def test_play_with_the_tv_after_it_plays_and_drops_the_chatter() -> None:
    v = _screen(DTMF_WITH_TV)
    assert v.action == COMMAND and v.long
    assert v.text == "Play DTMF by Bad Bunny."


def test_a_command_after_the_name_or_the_wake_phrase_still_counts() -> None:
    assert _screen("Domovoi, set a timer for ten minutes. " + TV_DIALOGUE).text == (
        "set a timer for ten minutes.")
    v = _screen("Hey Jarvis, pause. " + TV_DIALOGUE)
    assert (v.action, v.text) == (COMMAND, "pause.")


def test_a_long_command_said_in_one_breath_is_routed_whole() -> None:
    text = ("Remind me to call mom and tell her about the party on Saturday and that "
            "we need to bring the cake and the chairs from the garage and the folding "
            "table from the basement at five pm")
    v = _screen(text, exit_reason=PAUSE, audio=21.0)
    assert (v.action, v.text) == (COMMAND, text)
    # Cut off at the cap, the same words are not a command (the first
    # sentence is far past the head limit): ignored.
    assert _screen(text).action == IGNORE


def test_a_yes_to_a_parked_question_on_a_follow_up_counts() -> None:
    pending = {"handler": "double_check", "kind": "core.self_doubt_offer"}
    v = _screen("Yes. " + TV_DIALOGUE, trigger="followup", pending=pending)
    assert (v.action, v.text) == (COMMAND, "Yes.")
    # Without a parked question a bare "yes" is not a command.
    assert _screen("Yes. " + TV_DIALOGUE, trigger="followup").action == IGNORE


# ─── A genuine long question is still answered ──────────────────────────


def test_a_long_question_that_ends_on_a_pause_is_answered_whole() -> None:
    # 62 words, said in 22 s and ended by the person stopping: long, but a
    # question to the assistant at the end.
    v = _screen(LONG_QUESTION, exit_reason=PAUSE, audio=22.0)
    assert (v.action, v.text, v.long) == (ANSWER, LONG_QUESTION, True)


def test_a_question_under_the_lines_is_not_even_long() -> None:
    q = ("Why does the sky look blue during the day but orange at sunset, and what "
         "makes the colors change over the evening?")
    assert _screen(q, exit_reason=PAUSE, audio=9.0).action == ROUTE


def test_addressed_by_name_over_the_tv_answers_its_first_sentences() -> None:
    v = _screen("Domovoi, why is the sky blue? " + TV_DIALOGUE)
    assert v.action == ANSWER
    assert v.text.startswith("why is the sky blue?")
    assert overheard.words(v.text) <= overheard.ANSWER_MAX_WORDS


def test_a_clear_question_to_the_assistant_over_the_tv_is_answered() -> None:
    v = _screen("Can you tell me how long to boil an egg? " + TV_DIALOGUE)
    assert v.action == ANSWER
    v = _screen("What is the capital of France? " + TV_DIALOGUE)
    assert v.action == ANSWER


# ─── Background talk is ignored ─────────────────────────────────────────


@pytest.mark.parametrize("text", [TV_DIALOGUE, JARVIS_MEME, ECHO_TALK, TV_ARGUMENT, TV_ACTION_WORDS],
                         ids=["tv", "jarvis-meme", "echo-talk", "tv-argument", "tv-action-words"])
def test_chatter_cut_off_at_the_cap_is_ignored(text: str) -> None:
    v = _screen(text)
    assert v.action == IGNORE and v.long
    assert v.text == text


def test_chatter_that_paused_after_twenty_seconds_is_ignored_too() -> None:
    assert _screen(TV_DIALOGUE, exit_reason=PAUSE, audio=22.0).action == IGNORE


def test_jarvis_is_not_an_address() -> None:
    # The capture opens after the wake word: "Jarvis" again is the media.
    assert overheard.strip_address("Jarvis, run the numbers.")[0] is False
    assert overheard.strip_address("Hey Domovoi, run the numbers.") == (True, "run the numbers.")


def test_tell_me_about_it_is_not_a_question() -> None:
    assert not overheard.is_clear_question("Tell me about it.")
    assert overheard.is_clear_question("Tell me about the Roman empire.")


def test_push_to_talk_and_barge_in_are_never_ignored() -> None:
    for trigger in ("push_to_talk", "barge_in"):
        v = _screen(TV_DIALOGUE, trigger=trigger)
        assert (v.action, v.text, v.long) == (ROUTE, TV_DIALOGUE, True)


def test_the_wake_score_is_kept_only_when_it_is_one() -> None:
    assert overheard.wake_score(0.97321) == 0.973
    for bad in (None, True, "0.9", 1.5, -0.1, float("nan")):
        assert overheard.wake_score(bad) is None


# ─── The voice turn ─────────────────────────────────────────────────────


class _FakeWS:
    def __init__(self) -> None:
        self.app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                satellite_voice={}, wifi_status={}, satellite_volume={},
                greeting_phrases=[], probe=types.SimpleNamespace(online=True),
                resumable_music={}, current_playlist={}, pending_music_start={},
            )
        )
        self.sent_text: list[dict] = []

    async def send_text(self, data: str) -> None:
        self.sent_text.append(json.loads(data))

    async def send_bytes(self, data: bytes) -> None:
        pass

    def types(self) -> list[str]:
        return [f.get("type") for f in self.sent_text]


class _Whisper:
    def __init__(self, text_: str) -> None:
        self.text = text_

    async def transcribe(self, pcm_bytes: bytes) -> str:
        return self.text


class _NoSession:
    async def __aenter__(self):
        return types.SimpleNamespace()

    async def __aexit__(self, *a):
        return False


@pytest.fixture
def history_calls(monkeypatch):
    """The history writes, recorded instead of written (no database)."""
    calls: dict[str, list] = {"start": [], "close": []}

    class _Entry:
        taken = True
        person_id = None

    def _start(**kw):
        calls["start"].append(kw)
        return _Entry()

    async def _close(entry, reason, *, room_id):
        calls["close"].append(reason)

    monkeypatch.setattr(turn_history, "start_entry", _start)
    monkeypatch.setattr(turn_history, "close_entry", _close)
    return calls


@pytest.fixture
def quiet_turn(monkeypatch):
    """Everything around routing that would reach the database."""
    from domovoi import voice_identifier

    async def _identify(*a, **kw):
        return None

    async def _hooks(self, *a, **kw):
        return None

    monkeypatch.setattr(voice_identifier, "identify", _identify)
    monkeypatch.setattr(streaming, "session_scope", lambda: _NoSession())
    monkeypatch.setattr(StreamSession, "_voice_profile_hooks", _hooks)
    monkeypatch.setattr(StreamSession, "_wants_second_hearing", lambda self, *a, **kw: False)
    monkeypatch.setattr(StreamSession, "_finish_turn_timings", lambda self, *a, **kw: None)
    monkeypatch.setattr(StreamSession, "_keep_capture", lambda self, *a, **kw: None)
    turn_watchdog.reset()
    yield
    turn_watchdog.reset()


async def _settle() -> None:
    for _ in range(20):
        if not streaming._TIMING_WRITES:
            return
        await asyncio.sleep(0.01)
    await asyncio.gather(*list(streaming._TIMING_WRITES), return_exceptions=True)


PCM_30S = b"\x00" * (32_000 * 30)


@pytest.mark.asyncio
async def test_background_talk_is_never_routed_and_the_room_goes_back_to_idle(
    monkeypatch, history_calls, quiet_turn,
) -> None:
    monkeypatch.setattr(streaming, "get_whisper_client", lambda: _Whisper(TV_ACTION_WORDS))

    async def _route(*a, **kw):
        raise AssertionError("background talk must never reach the router (or a handler)")

    monkeypatch.setattr(streaming, "route", _route)
    ws = _FakeWS()
    sess = StreamSession(ws, "oh-office")  # type: ignore[arg-type]
    await asyncio.wait_for(sess._process_utterance(
        PCM_30S, trigger="wake_word", capture_meta={"end_reason": CAP}, wake_score=0.98,
    ), 10)
    await _settle()

    # One frame: the end of the turn. No transcript, no reply, no error,
    # no apology — the satellite takes a bare response_end as "done" and
    # goes back to idle, without its error ring or the no-answer clip.
    assert ws.sent_text == [{"type": "response_end", "interrupted": True, "expect_followup": False}]
    # The watchdog saw nothing: a deliberate silence is not a stall.
    assert turn_watchdog.last_stall() is None
    # History: what was heard, closed as "ignored: background talk".
    assert [(c["user_text"], c["trigger"], c["wake_score"], c["audio_ms"])
            for c in history_calls["start"]] == [(TV_ACTION_WORDS, "wake_word", 0.98, 30_000)]
    assert history_calls["close"] == [turn_history.OVERHEARD]


@pytest.mark.asyncio
async def test_the_music_the_wake_stopped_comes_back_after_ignoring(
    monkeypatch, history_calls, quiet_turn,
) -> None:
    monkeypatch.setattr(streaming, "get_whisper_client", lambda: _Whisper(TV_DIALOGUE))
    started: list[str] = []

    async def _music_start(app, sess, room, url):
        started.append(url)

    monkeypatch.setattr(streaming, "send_music_start", _music_start)
    ws = _FakeWS()
    ws.app.state.resumable_music["oh-den"] = "http://192.168.1.20:8051/stream"
    sess = StreamSession(ws, "oh-den")  # type: ignore[arg-type]
    await sess._process_utterance(PCM_30S, trigger="wake_word", capture_meta={"end_reason": CAP})
    assert started == ["http://192.168.1.20:8051/stream"]


def _routing(monkeypatch, *, expect_followup: bool):
    seen: list[tuple[Intent, Context]] = []

    async def _route(intent, ctx, session):
        seen.append((intent, ctx))
        return Response(text="Okay.", session_id=None, online=True,
                        matched_handler="music", matched_path="fast",
                        expect_followup=expect_followup)

    monkeypatch.setattr(streaming, "route", _route)
    return seen


@pytest.mark.asyncio
async def test_the_command_at_the_start_is_routed_alone_with_no_follow_up(
    monkeypatch, history_calls, quiet_turn,
) -> None:
    monkeypatch.setattr(streaming, "get_whisper_client", lambda: _Whisper(DTMF_WITH_TV))
    seen = _routing(monkeypatch, expect_followup=True)
    ws = _FakeWS()
    sess = StreamSession(ws, "oh-kitchen")  # type: ignore[arg-type]
    await asyncio.wait_for(sess._process_utterance(
        PCM_30S, trigger="wake_word", capture_meta={"end_reason": CAP},
    ), 10)
    await _settle()

    (intent, ctx), = seen
    assert intent.transcript == "Play DTMF by Bad Bunny."
    # No tool model, no follow-up question, no "did you mean" parked.
    assert ctx.long_capture is True and ctx.answerable is False
    assert history_calls["start"][0]["user_text"] == "Play DTMF by Bad Bunny."
    assert {"type": "transcript", "text": "Play DTMF by Bad Bunny."} in ws.sent_text
    # The reply asked for a follow-up; a long capture doesn't get one.
    end = [f for f in ws.sent_text if f.get("type") == "response_end"][-1]
    assert end["expect_followup"] is False


@pytest.mark.asyncio
async def test_a_long_follow_up_capture_opens_no_further_follow_up(
    monkeypatch, history_calls, quiet_turn,
) -> None:
    """The loop: the TV answers "Want me to check that online?" — that
    follow-up capture runs to the cap too, and must not open another."""
    question = "Can you tell me how long to boil an egg? " + TV_DIALOGUE
    monkeypatch.setattr(streaming, "get_whisper_client", lambda: _Whisper(question))
    seen = _routing(monkeypatch, expect_followup=True)
    ws = _FakeWS()
    sess = StreamSession(ws, "oh-dining")  # type: ignore[arg-type]
    await sess._process_utterance(PCM_30S, trigger="followup", capture_meta={"end_reason": CAP})
    await _settle()
    assert seen and seen[0][1].long_capture is True
    end = [f for f in ws.sent_text if f.get("type") == "response_end"][-1]
    assert end["expect_followup"] is False


@pytest.mark.asyncio
async def test_a_normal_short_turn_keeps_its_follow_up(
    monkeypatch, history_calls, quiet_turn,
) -> None:
    monkeypatch.setattr(streaming, "get_whisper_client", lambda: _Whisper("play some jazz"))
    seen = _routing(monkeypatch, expect_followup=True)
    ws = _FakeWS()
    sess = StreamSession(ws, "oh-hall")  # type: ignore[arg-type]
    await sess._process_utterance(
        b"\x00" * 64_000, trigger="wake_word", capture_meta={"end_reason": PAUSE},
    )
    await _settle()
    (intent, ctx), = seen
    assert intent.transcript == "play some jazz"
    assert ctx.long_capture is False and ctx.answerable is True
    end = [f for f in ws.sent_text if f.get("type") == "response_end"][-1]
    assert end["expect_followup"] is True
    assert history_calls["close"] == []


# ─── The router (database) ──────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_a_long_capture_never_asks_the_tool_model_or_offers_a_follow_up(db_session) -> None:
    from unittest.mock import patch

    from domovoi.clients.ollama import QAWithUncertainty
    from domovoi.db.repositories import SessionRepository
    from domovoi.router import route

    class _Ollama:
        async def route(self, transcript, tool_schemas):
            raise AssertionError("a long capture must not reach the tool model")

        async def qa(self, transcript, system_prompt=None, history=None):
            return "(stub)"

        async def qa_with_uncertainty(self, transcript, history=None, profile_prefix=None):
            return QAWithUncertainty(answer="Nine minutes.", needs_verification=True,
                                     candidate_claim="Nine minutes.")

    with patch("domovoi.router.get_ollama_client", lambda: _Ollama()):
        intent = Intent(transcript="how many tracks are in my library", room_id="oh-r1")
        ctx = Context(room_id="oh-r1", online=True, long_capture=True, answerable=False)
        response = await route(intent, ctx, db_session)
        await db_session.commit()

    assert response.text == "Nine minutes."
    assert "check that online" not in response.text.lower()
    assert response.expect_followup is False
    pending = (await SessionRepository(db_session).get_context(response.session_id)).get(
        "pending_confirmation")
    assert pending is None


@requires_db
@pytest.mark.asyncio
async def test_a_long_capture_skips_the_volatile_offer(db_session) -> None:
    from domovoi.router import route

    intent = Intent(transcript="who is the current president", room_id="oh-r2")
    ctx = Context(room_id="oh-r2", online=True, long_capture=True, answerable=False)
    response = await route(intent, ctx, db_session)
    await db_session.commit()
    assert response.matched_path != "volatile_offer"
    assert response.expect_followup is False


@requires_db
@pytest.mark.asyncio
async def test_an_ignored_turn_reads_ignored_in_the_room_history(db_session) -> None:
    """The real row: written pending, closed no_answer / overheard, with the
    wake score (V044), and the dashboard's read says so."""
    from datetime import datetime, timezone

    from sqlalchemy import text

    from domovoi.db.session import session_scope
    from web.backend.api import satellites as sat_api

    turn_history._forget_probe()
    entry = await turn_history.open_entry(
        room_id="oh-hist", session_id=None, user_text=TV_DIALOGUE, trigger="wake_word",
        audio_ms=30_000, at=datetime.now(timezone.utc), wake_score=0.97,
    )
    assert entry is not None
    await turn_history.close_entry(entry, turn_history.OVERHEARD, room_id="oh-hist")
    async with session_scope() as s:
        row = (await s.execute(text(
            "SELECT turn_status, no_answer_reason, wake_score, audio_ms FROM conversation_log "
            "WHERE room_id = 'oh-hist'"))).one()
    assert (row[0], row[1], round(row[2], 2), row[3]) == ("no_answer", "overheard", 0.97, 30_000)
    turns = await sat_api.list_conversations("oh-hist", limit=10)
    assert [(t.status, t.no_answer_reason) for t in turns] == [("no_answer", "overheard")]


def test_settings_bot_name_is_an_address() -> None:
    assert overheard.strip_address(f"{settings.bot_name}, play jazz", settings.bot_name) == (
        True, "play jazz")
