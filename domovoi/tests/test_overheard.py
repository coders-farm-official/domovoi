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


# ─── What is long, what is capped ───────────────────────────────────────


def test_a_short_turn_is_untouched() -> None:
    v = _screen("what time is it", exit_reason=PAUSE, audio=2.0)
    assert (v.action, v.text, v.long, v.capped) == (ROUTE, "what time is it", False, False)


def test_long_is_twenty_seconds_or_sixty_words() -> None:
    assert overheard.is_long("hi", exit_reason=PAUSE, audio_seconds=20.0)
    assert not overheard.is_long("hi", exit_reason=PAUSE, audio_seconds=19.9)
    assert overheard.is_long("word " * 60, exit_reason=PAUSE, audio_seconds=8.0)
    assert not overheard.is_long("word " * 59, exit_reason=PAUSE, audio_seconds=8.0)


def test_capped_is_the_cap_after_twenty_seconds() -> None:
    assert overheard.is_capped(CAP, 30.0) and overheard.is_capped(CAP, 20.0)
    # A household that lowered "Max command length" to 10 s (S1): its
    # ordinary requests are not screened.
    assert not overheard.is_capped(CAP, 10.0)
    assert not overheard.is_capped(PAUSE, 30.0)
    # An older satellite sends no exit reason: a 30 s capture is capped.
    assert overheard.is_capped(None, 30.0) and not overheard.is_capped(None, 21.0)


def test_a_ten_second_cap_is_routed_as_always() -> None:
    for text in ("remind me to pick up the dry cleaning on tuesday after work and also grab",
                 "how many tablespoons are in a quarter cup because I need to double the"):
        v = _screen(text, exit_reason=CAP, audio=10.0)
        assert (v.action, v.text, v.long, v.capped) == (ROUTE, text, False, False)


# ─── A pause-ended long capture is a person's (B1) ──────────────────────


def test_a_long_capture_that_ended_on_a_pause_is_routed_whole_with_tools() -> None:
    for text in (LONG_QUESTION, TV_DIALOGUE, TV_ACTION_WORDS):
        v = _screen(text, exit_reason=PAUSE, audio=22.0)
        # Routed as always (the tool model included); long only means no
        # follow-up mic and no offers.
        assert (v.action, v.text, v.long, v.capped) == (ROUTE, text, True, False)


def test_push_to_talk_and_barge_in_are_never_screened() -> None:
    for trigger in ("push_to_talk", "barge_in"):
        v = _screen(TV_DIALOGUE, trigger=trigger)
        assert (v.action, v.text, v.long, v.capped) == (ROUTE, TV_DIALOGUE, False, False)


# ─── A command said first, over the TV ──────────────────────────────────


def test_play_with_the_tv_after_it_plays_and_drops_the_chatter() -> None:
    v = _screen(DTMF_WITH_TV)
    assert (v.action, v.long, v.capped) == (COMMAND, True, True)
    assert v.text == "Play DTMF by Bad Bunny."


def test_a_command_with_no_punctuation_is_found_at_the_start() -> None:
    # S3: Whisper gave no full stop; the clause before "and" is the command.
    v = _screen("play some jazz in the kitchen and in other news tonight the city council "
                "voted to approve the new budget which includes funding for parks and the "
                "mayor said it was a historic day")
    assert (v.action, v.text) == (COMMAND, "play some jazz in the kitchen")
    v = _screen("Play some jazz in the kitchen, and in other news tonight the city council "
                "voted to approve the new budget which includes funding for parks")
    assert (v.action, v.text) == (COMMAND, "Play some jazz in the kitchen")


def test_a_command_found_by_its_word_prefix() -> None:
    # No comma, no "and" before the chatter: the longest prefix of at most
    # 15 words the fast paths take.
    text = ("set a timer for ten minutes she said the whole time we were in the car "
            "she would not stop talking about it")
    v = _screen(text)
    assert v.action == COMMAND and v.text.startswith("set a timer for ten minutes")
    assert overheard.words(v.text) <= overheard.HEAD_MAX_WORDS


@pytest.mark.parametrize("text", [
    "I am so tired of this, I swear to god I will leave tomorrow and never come back here again",
    "This is the best day of my life and nobody can take that away from me, nobody at all",
    "Remember the time we went to the lake and your dad fell in the water with all his clothes on",
], ids=["im", "this-is", "remember"])
def test_tv_words_that_look_like_an_introduction_or_a_memory_are_not_commands(text) -> None:
    # voice_profile ("I'm ...", "this is ...") and memory ("remember ...")
    # never count on a capped capture: the TV would be enrolled or saved.
    assert _screen(text).action == IGNORE


def test_a_one_word_prefix_is_not_a_command() -> None:
    # "Next time I see him..." is TV, not "next song".
    v = _screen("Next time I see him I am going to tell him exactly what I think of him and "
                "his whole family and that stupid boat of his that he never takes out")
    assert v.action == IGNORE


def test_a_command_after_the_name_or_the_wake_phrase_still_counts() -> None:
    assert _screen("Domovoi, set a timer for ten minutes. " + TV_DIALOGUE).text == (
        "set a timer for ten minutes.")
    v = _screen("Hey Jarvis, pause. " + TV_DIALOGUE)
    assert (v.action, v.text) == (COMMAND, "pause.")


def test_a_yes_to_a_parked_question_on_a_follow_up_counts() -> None:
    pending = {"handler": "double_check", "kind": "core.self_doubt_offer"}
    v = _screen("Yes. " + TV_DIALOGUE, trigger="followup", pending=pending)
    assert (v.action, v.text) == (COMMAND, "Yes.")
    # Without a parked question a bare "yes" is not a command.
    assert _screen("Yes. " + TV_DIALOGUE, trigger="followup").action == IGNORE


# ─── A question over the TV ─────────────────────────────────────────────


def test_addressed_by_name_over_the_tv_answers_its_first_sentences() -> None:
    v = _screen("Domovoi, why is the sky blue? " + TV_DIALOGUE)
    assert (v.action, v.capped) == (ANSWER, True)
    assert v.text.startswith("why is the sky blue?")
    assert overheard.words(v.text) <= overheard.ANSWER_MAX_WORDS
    # "Hey Domo" is the assistant; a bare "domo" is not.
    assert overheard.strip_address("Hey Domo, play jazz")[0] is True
    assert overheard.strip_address("Domo arigato, Mister Roboto")[0] is False


def test_a_clear_question_at_the_start_is_answered() -> None:
    assert _screen("Can you tell me how long to boil an egg? " + TV_DIALOGUE).action == ANSWER
    assert _screen("What is the capital of France? " + TV_DIALOGUE).action == ANSWER


def test_a_question_later_in_a_capped_capture_is_answered_alone() -> None:
    # S4: the question at the end, or in the middle, of a capped capture.
    v = _screen("My son and I have been arguing about this all afternoon. He says it is the "
                "Nile and I say it is the Amazon. Which river is the longest in the world? "
                + TV_DIALOGUE)
    assert (v.action, v.text) == (ANSWER, "Which river is the longest in the world?")
    v = _screen("I'm cooking dinner for eight people tonight. How long do I roast a twelve "
                "pound turkey? " + TV_DIALOGUE)
    assert (v.action, v.text) == (ANSWER, "How long do I roast a twelve pound turkey?")


# ─── Background talk is ignored ─────────────────────────────────────────


# The media shapes FALSE-WAKES-DIAGNOSIS.md describes (the live history was
# not readable, so these are written to them): TV dialogue run to the cap,
# the "Jarvis, ..." meme, somebody talking to an Echo, a TV argument, and TV
# words that sound like an action.
@pytest.mark.parametrize("text", [TV_DIALOGUE, JARVIS_MEME, ECHO_TALK, TV_ARGUMENT, TV_ACTION_WORDS],
                         ids=["tv", "jarvis-meme", "echo-talk", "tv-argument", "tv-action-words"])
def test_chatter_cut_off_at_the_cap_is_ignored(text: str) -> None:
    v = _screen(text)
    assert (v.action, v.long, v.capped) == (IGNORE, True, True)
    assert v.text == text


def test_jarvis_is_not_an_address() -> None:
    # The capture opens after the wake word: "Jarvis" again is the media.
    assert overheard.strip_address("Jarvis, run the numbers.")[0] is False
    assert overheard.strip_address("Hey Domovoi, run the numbers.") == (True, "run the numbers.")


def test_tell_me_about_it_is_not_a_question() -> None:
    assert not overheard.is_clear_question("Tell me about it.")
    assert overheard.is_clear_question("Tell me about the Roman empire.")


def test_the_wake_score_is_kept_only_when_it_is_one() -> None:
    assert overheard.wake_score(0.97321) == 0.973
    for bad in (None, True, "0.9", 1.5, -0.1, float("nan")):
        assert overheard.wake_score(bad) is None


# ─── The review's probe: 23 genuine requests ────────────────────────────
# (label, text, exit_reason, audio seconds, trigger). Every one must be
# routed or answered, never ignored. Before the review's fixes 16 of these
# were ignored.
PROBE = [
    ("reminder-detail-pause-nopunct", "remind me tomorrow at nine to call the insurance company about the claim for the car accident last week and ask them whether the rental car is covered and how long the repair is going to take", PAUSE, 21, "wake_word"),
    ("reminder-detail-two-sentences", "Remind me tomorrow at nine to call the insurance company. I need to ask them whether the rental car is covered and how long the repair at the body shop is going to take because the kids need rides.", PAUSE, 21, "wake_word"),
    ("shopping-list", "add milk eggs bread butter cheese apples bananas chicken breasts rice pasta tomato sauce ground beef onions garlic lettuce and orange juice to the shopping list", PAUSE, 20.5, "wake_word"),
    ("shopping-list-sentences", "Add milk to the shopping list. Also eggs, bread, butter, cheese, apples, bananas, chicken breasts, rice, pasta, tomato sauce, ground beef, onions, garlic and lettuce.", PAUSE, 21, "wake_word"),
    ("note-dictation", "Take a note. The plumber is coming on Thursday between eight and noon, he needs access to the basement, the water heater model number is on the sticker, and we owe him two hundred dollars from last time.", PAUSE, 22, "wake_word"),
    ("story-kid", "So today at school we learned about dinosaurs and my friend Jake said the T rex was the biggest but I think it was the spinosaurus and then at lunch we played tag and I won. Tell me a story about a spinosaurus.", PAUSE, 22, "wake_word"),
    ("story-kid-nopunct", "so today at school we learned about dinosaurs and my friend jake said the t rex was the biggest but I think it was the spinosaurus and then at lunch we played tag and I won and I want you to tell me a story about a spinosaurus", PAUSE, 22, "wake_word"),
    ("ramble-then-world-q", "My son and I have been arguing about this all afternoon. He says it is the Nile and I say it is the Amazon and we looked it up and got two different answers from two different websites. Which river is the longest in the world?", PAUSE, 21, "wake_word"),
    ("ramble-then-whats", "We're planning a trip next month with the kids and my wife wants to go somewhere warm but not too far because the little one gets carsick on long drives. What's the weather usually like in San Diego in March?", PAUSE, 21, "wake_word"),
    ("multi-q-middle", "I'm cooking dinner for eight people tonight. How long do I roast a twelve pound turkey? It's stuffed and I'm using a convection oven, and my oven runs a bit hot, so I want to make sure it's not dry.", PAUSE, 20.5, "wake_word"),
    ("calendar-long-nopunct", "add a dentist appointment to my calendar next tuesday at three with doctor smith at the downtown office on fifth street and put a note that I need to bring the insurance card", PAUSE, 21, "wake_word"),
    ("can-you-calendar", "Can you add a dentist appointment to the calendar next Tuesday at three with Doctor Smith at the downtown office on Fifth Street and put a note that I need to bring the insurance card?", PAUSE, 21, "wake_word"),
    ("spanish", "Oye, quiero saber cuánto tiempo tengo que hervir los huevos para que queden duros pero no demasiado, porque la última vez quedaron verdes por dentro y a mis hijos no les gustó nada.", PAUSE, 21, "wake_word"),
    ("name-mid", "Okay so Domovoi, I was thinking about the trip and I need to know what time the sun sets tomorrow because we want to be at the beach before it gets dark and the kids need to eat first.", PAUSE, 21, "wake_word"),
    ("capped-play-nopunct", "play some jazz in the kitchen and in other news tonight the city council voted to approve the new budget which includes funding for parks and the mayor said it was a historic day", CAP, 30, "wake_word"),
    ("capped-play-comma", "Play some jazz in the kitchen, and in other news tonight the city council voted to approve the new budget which includes funding for parks", CAP, 30, "wake_word"),
    ("capped10-reminder", "remind me to pick up the dry cleaning on tuesday after work and also grab", CAP, 10, "wake_word"),
    ("capped10-question", "how many tablespoons are in a quarter cup because I need to double the", CAP, 10, "wake_word"),
    ("followup-answer-long", "Yeah I think the second one, the one with the red cover, because my daughter really liked that one last time and she has been asking for it all week", PAUSE, 20.5, "followup"),
    ("set-timer-detail", "Set a timer for twenty minutes for the lasagna and then when it goes off I need to take the foil off and put it back in for another ten minutes with the cheese on top", PAUSE, 21, "wake_word"),
    ("what-do-you-think", "I'm trying to decide between the blue sofa and the grey one for the living room, the blue one is cheaper but the grey one matches the curtains better and my husband likes the grey one. What do you think?", PAUSE, 21, "wake_word"),
    ("explain", "Explain how a mortgage refinance works, like what the closing costs are, whether it makes sense when rates drop by one percent, and how long it takes to break even on the fees.", PAUSE, 20.5, "wake_word"),
    ("hey-jarvis-repeat", "Hey Jarvis, what's the weather tomorrow? Hey Jarvis, are you listening? I asked you what the weather is going to be like tomorrow because we have a soccer game in the morning.", PAUSE, 20.5, "wake_word"),
]


@pytest.mark.parametrize("label,text,exit_reason,audio,trigger", PROBE, ids=[p[0] for p in PROBE])
def test_the_probe_requests_are_never_ignored(label, text, exit_reason, audio, trigger) -> None:
    v = screen(text, trigger=trigger, exit_reason=exit_reason, audio_seconds=audio,
               bot_name="Domovoi")
    assert v.action != IGNORE, (label, v.why)
    if exit_reason == PAUSE:
        # Ended on a pause: routed whole, the tool model included.
        assert (v.action, v.text, v.capped) == (ROUTE, text, False)


def test_the_probe_is_twenty_three_requests() -> None:
    assert len(PROBE) == 23


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
    assert ws.sent_text == [{"type": "response_end", "interrupted": True,
                             "expect_followup": False, "ignored": True}]
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
    assert ctx.long_capture is True and ctx.capped_capture is True and ctx.answerable is False
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
async def test_a_long_turn_that_ended_on_a_pause_is_routed_whole_without_a_follow_up(
    monkeypatch, history_calls, quiet_turn,
) -> None:
    """B1: a person stopped talking. The whole transcript is routed, the
    tool model allowed; only the follow-up mic stays closed."""
    monkeypatch.setattr(streaming, "get_whisper_client", lambda: _Whisper(TV_ACTION_WORDS))
    seen = _routing(monkeypatch, expect_followup=True)
    ws = _FakeWS()
    sess = StreamSession(ws, "oh-study")  # type: ignore[arg-type]
    await sess._process_utterance(
        b"\x00" * (32_000 * 22), trigger="wake_word", capture_meta={"end_reason": PAUSE},
    )
    await _settle()
    (intent, ctx), = seen
    assert intent.transcript == TV_ACTION_WORDS
    assert ctx.long_capture is True and ctx.capped_capture is False
    end = [f for f in ws.sent_text if f.get("type") == "response_end"][-1]
    assert end["expect_followup"] is False and "ignored" not in end
    assert history_calls["close"] == []


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
        ctx = Context(room_id="oh-r1", online=True, long_capture=True, capped_capture=True,
                      answerable=False)
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
async def test_a_long_turn_that_ended_on_a_pause_still_asks_the_tool_model(db_session) -> None:
    from unittest.mock import patch

    from domovoi.clients.ollama import QAWithUncertainty
    from domovoi.router import route

    asked: list[str] = []

    class _Ollama:
        async def route(self, transcript, tool_schemas):
            asked.append(transcript)
            return None

        async def qa(self, transcript, system_prompt=None, history=None):
            return "(stub)"

        async def qa_with_uncertainty(self, transcript, history=None, profile_prefix=None):
            return QAWithUncertainty(answer="Nine minutes.", needs_verification=True,
                                     candidate_claim="Nine minutes.")

    with patch("domovoi.router.get_ollama_client", lambda: _Ollama()):
        intent = Intent(transcript="how many tracks are in my library", room_id="oh-r3")
        ctx = Context(room_id="oh-r3", online=True, long_capture=True, answerable=False)
        response = await route(intent, ctx, db_session)
        await db_session.commit()
    assert asked == ["how many tracks are in my library"]
    # Still no follow-up question on a long turn.
    assert "check that online" not in response.text.lower()
    assert response.expect_followup is False


@requires_db
@pytest.mark.asyncio
async def test_a_long_capture_skips_the_volatile_offer(db_session) -> None:
    from domovoi.router import route

    intent = Intent(transcript="who is the current president", room_id="oh-r2")
    ctx = Context(room_id="oh-r2", online=True, long_capture=True, capped_capture=True,
                  answerable=False)
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
    assert [(t.status, t.no_answer_reason, t.wake_score) for t in turns] == [
        ("no_answer", "overheard", 0.97)]


def test_settings_bot_name_is_an_address() -> None:
    assert overheard.strip_address(f"{settings.bot_name}, play jazz", settings.bot_name) == (
        True, "play jazz")
