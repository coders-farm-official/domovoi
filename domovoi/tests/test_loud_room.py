"""A command said in a loud room (1.5.0, noisy rooms; domovoi/loud_room.py).

Owner answer Q7: a capture that ran to the cap but STARTS with a clear
command is answered after "It's loud in here. I heard: ..."; anything that
changes the house is asked about first and runs only on a yes. Pinned:

* the window: from just before ``first_voiced_frame``, a few seconds; an
  older satellite (no field) is transcribed whole, as before;
* read-only commands are answered with the prefix;
* house-changing commands are asked about; yes runs, no doesn't, a stale
  yes is an ordinary turn;
* unclear capped audio and true background talk are still dropped;
* the shared fixtures (docs/uml/satellite-fixtures.json, "noisy_rooms").
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from domovoi import loud_room, overheard, streaming
from domovoi.handlers import HANDLER_BY_NAME
from domovoi.models import Context, Intent, Response
from domovoi.overheard import COMMAND, IGNORE
from domovoi.router import plan_route
from domovoi.streaming import StreamSession
from domovoi.tests.conftest import requires_db
from domovoi.tests.test_overheard import (  # noqa: F401 — fixtures
    TV_ACTION_WORDS,
    TV_DIALOGUE,
    _FakeWS,
    _settle,
    _Whisper,
    history_calls,
    quiet_turn,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = json.loads(
    (ROOT / "docs" / "uml" / "satellite-fixtures.json").read_text(encoding="utf-8")
)["noisy_rooms"]
BPS = 32_000  # bytes a second: 16 kHz, 16-bit
CAP = "max_record_seconds"


# ─── The window ─────────────────────────────────────────────────────────


def test_the_fixture_capture_is_transcribed_from_its_first_voiced_frame() -> None:
    end = FIXTURES["capped_utterance_end"]
    start, stop = loud_room.stt_window(end, frames=end["frames"], trigger="wake_word")
    first = end["first_voiced_frame"] * 960
    assert start == first - int(FIXTURES["lead_sec"] * BPS)
    assert stop == first + int(FIXTURES["window_sec"] * BPS)
    assert FIXTURES["window_sec"] == loud_room.WINDOW_SEC
    assert FIXTURES["lead_sec"] == loud_room.LEAD_SEC


def test_an_older_satellite_is_transcribed_whole() -> None:
    end = FIXTURES["capped_utterance_end_older_satellite"]
    assert "first_voiced_frame" not in end
    assert loud_room.stt_window(end, frames=end["frames"], trigger="wake_word") is None


def test_only_a_capped_wake_or_follow_up_capture_gets_a_window() -> None:
    end = dict(FIXTURES["capped_utterance_end"])
    frames = end["frames"]
    assert loud_room.stt_window(end, frames=frames, trigger="followup") is not None
    for trigger in ("push_to_talk", "barge_in", None):
        assert loud_room.stt_window(end, frames=frames, trigger=trigger) is None
    # Ended on a pause: a person, transcribed whole.
    assert loud_room.stt_window(
        {**end, "exit_reason": "vad_silence_after_speech"}, frames=frames, trigger="wake_word",
    ) is None
    # The counts must be about the core's own buffer.
    assert loud_room.stt_window(end, frames=frames - 1, trigger="wake_word") is None
    # A household that lowered its cap to 10 s: routed whole, as always.
    short = {**end, "frames": 333, "last_voiced_frame": 332}
    assert loud_room.stt_window(short, frames=333, trigger="wake_word") is None
    # Nonsense.
    for bad in (None, -1, frames, True, "12"):
        assert loud_room.stt_window(
            {**end, "first_voiced_frame": bad}, frames=frames, trigger="wake_word",
        ) is None


def test_the_window_never_runs_past_the_capture() -> None:
    end = {**FIXTURES["capped_utterance_end"], "first_voiced_frame": 995}
    start, stop = loud_room.stt_window(end, frames=1000, trigger="wake_word")
    assert stop == 1000 * 960 and start < stop


# ─── The wording ────────────────────────────────────────────────────────


def test_the_lines() -> None:
    assert loud_room.heard_line("what time is it?") == FIXTURES["heard_line_example"]
    assert loud_room.ask_line("play some jazz in the kitchen") == FIXTURES["ask_line_example"]
    assert loud_room.ask_line("add milk to my shopping list.") == (
        "It's loud in here. Should I add milk to your shopping list?"
    )
    assert loud_room.ask_line("remind me to call mom") == (
        "It's loud in here. Should I remind you to call mom?"
    )
    # Not a verb first: said back, then asked.
    assert loud_room.ask_line("i want to hear jazz") == (
        "It's loud in here. I heard: i want to hear jazz. Should I do that?"
    )
    assert loud_room.NO_LINE == FIXTURES["no_line"]


# ─── What changes the house ─────────────────────────────────────────────


@pytest.mark.parametrize("command", [
    "play some jazz in the kitchen",
    "add milk to the shopping list",
    "set a timer for ten minutes",
    "pause the music",
])
def test_commands_that_act_change_the_house(command) -> None:
    plan = plan_route(command)
    assert plan is not None, command
    assert loud_room.changes_house(plan.handler, plan.transcript) is True


@pytest.mark.parametrize("command", [
    "what time is it",
    "what's on the shopping list",
    "what's playing",
    "what's two plus two",
])
def test_reading_something_does_not(command) -> None:
    plan = plan_route(command)
    assert plan is not None, command
    assert loud_room.changes_house(plan.handler, plan.transcript) is False


def test_a_plugin_handler_changes_the_house_unless_it_says_otherwise() -> None:
    class _Plugin:
        pass

    assert loud_room.changes_house(_Plugin(), "turn on the porch light") is True
    assert HANDLER_BY_NAME["clock"].read_only is True
    assert HANDLER_BY_NAME["music"].read_only is False


# ─── Overheard: a clear command at the start counts, chatter doesn't ────


def test_the_window_with_a_clear_command_is_a_command() -> None:
    v = overheard.screen("Play some jazz in the kitchen and um", trigger="wake_word",
                         exit_reason=CAP, audio_seconds=30.0)
    assert v.action == COMMAND and v.capped


@pytest.mark.parametrize("text", [TV_DIALOGUE, TV_ACTION_WORDS, "and then he said it was over"])
def test_true_background_talk_is_still_dropped(text) -> None:
    v = overheard.screen(text, trigger="wake_word", exit_reason=CAP, audio_seconds=30.0)
    assert v.action == IGNORE


def test_a_yes_to_the_loud_question_is_a_command_on_the_follow_up() -> None:
    pending = loud_room.pending_payload("music", "play some jazz")
    v = overheard.screen("Yes. " + TV_DIALOGUE, trigger="followup", exit_reason=CAP,
                         audio_seconds=30.0, pending=pending)
    assert v.action == COMMAND and v.text == "Yes."
    plan = plan_route("yes", pending=pending)
    assert plan is not None and plan.path == "confirmation" and plan.handler.name == "music"
    # A stale one plans nothing.
    stale = loud_room.pending_payload("music", "play some jazz", now=0.0)
    assert plan_route("yes", pending=stale) is None


# ─── The streaming layer ────────────────────────────────────────────────


class _Recorder(_Whisper):
    def __init__(self, text_: str) -> None:
        super().__init__(text_)
        self.heard: list[int] = []

    async def transcribe(self, pcm_bytes: bytes) -> str:
        self.heard.append(len(pcm_bytes))
        return self.text


def _route_stub(monkeypatch, response: Response):
    seen: list[tuple[Intent, Context]] = []

    async def _route(intent, ctx, session):
        seen.append((intent, ctx))
        return response

    monkeypatch.setattr(streaming, "route", _route)
    return seen


PCM_30S = b"\x00" * (BPS * 30)


@pytest.mark.asyncio
async def test_a_capped_capture_transcribes_only_its_window_and_is_a_loud_command(
    monkeypatch, history_calls, quiet_turn,  # noqa: F811
) -> None:
    whisper = _Recorder("Play some jazz in the kitchen.")
    monkeypatch.setattr(streaming, "get_whisper_client", lambda: whisper)
    seen = _route_stub(monkeypatch, Response(text="ok", matched_handler="music",
                                             matched_path="fast"))
    end = FIXTURES["capped_utterance_end"]
    window = loud_room.stt_window(end, frames=end["frames"], trigger="wake_word")
    sess = StreamSession(_FakeWS(), "lr-kitchen")  # type: ignore[arg-type]
    await asyncio.wait_for(sess._process_utterance(
        PCM_30S, trigger="wake_word", capture_meta={"end_reason": CAP}, stt_window=window,
    ), 10)
    await _settle()
    assert whisper.heard == [window[1] - window[0]]
    (intent, ctx), = seen
    assert ctx.loud_command is True and ctx.capped_capture is True


@pytest.mark.asyncio
async def test_an_older_satellite_capture_is_transcribed_whole(
    monkeypatch, history_calls, quiet_turn,  # noqa: F811
) -> None:
    whisper = _Recorder("Play some jazz in the kitchen. " + TV_DIALOGUE)
    monkeypatch.setattr(streaming, "get_whisper_client", lambda: whisper)
    seen = _route_stub(monkeypatch, Response(text="ok", matched_handler="music",
                                             matched_path="fast"))
    sess = StreamSession(_FakeWS(), "lr-den")  # type: ignore[arg-type]
    await asyncio.wait_for(sess._process_utterance(
        PCM_30S, trigger="wake_word", capture_meta={"end_reason": CAP},
    ), 10)
    await _settle()
    assert whisper.heard == [len(PCM_30S)]
    (intent, ctx), = seen
    assert intent.transcript == "Play some jazz in the kitchen."


@pytest.mark.asyncio
async def test_the_loud_question_keeps_its_follow_up_mic(
    monkeypatch, history_calls, quiet_turn,  # noqa: F811
) -> None:
    monkeypatch.setattr(streaming, "get_whisper_client", lambda: _Whisper("Play some jazz."))
    _route_stub(monkeypatch, Response(
        text=loud_room.ask_line("play some jazz"), matched_handler="music", matched_path="fast",
        expect_followup=True, data={"loud_confirm": True},
    ))
    ws = _FakeWS()
    sess = StreamSession(ws, "lr-hall")  # type: ignore[arg-type]
    await asyncio.wait_for(sess._process_utterance(
        PCM_30S, trigger="wake_word", capture_meta={"end_reason": CAP},
    ), 10)
    await _settle()
    end = [f for f in ws.sent_text if f.get("type") == "response_end"][-1]
    assert end["expect_followup"] is True


@pytest.mark.asyncio
async def test_unclear_capped_audio_is_dropped(
    monkeypatch, history_calls, quiet_turn,  # noqa: F811
) -> None:
    monkeypatch.setattr(streaming, "get_whisper_client",
                        lambda: _Whisper("so anyway she was like no way and"))

    async def _route(*a, **kw):
        raise AssertionError("unclear capped audio must not be routed")

    monkeypatch.setattr(streaming, "route", _route)
    ws = _FakeWS()
    sess = StreamSession(ws, "lr-patio")  # type: ignore[arg-type]
    end = FIXTURES["capped_utterance_end"]
    await asyncio.wait_for(sess._process_utterance(
        PCM_30S, trigger="wake_word", capture_meta={"end_reason": CAP},
        stt_window=loud_room.stt_window(end, frames=end["frames"], trigger="wake_word"),
    ), 10)
    await _settle()
    assert ws.sent_text[-1].get("ignored") is True


# ─── The router (database) ──────────────────────────────────────────────


async def _route(db_session, text: str, *, room: str, session_id=None, loud: bool = True):
    from domovoi.router import route

    ctx = Context(room_id=room, session_id=session_id, online=True,
                  long_capture=loud, capped_capture=loud, loud_command=loud,
                  answerable=not loud)
    response = await route(Intent(transcript=text, room_id=room, session_id=session_id),
                           ctx, db_session)
    await db_session.commit()
    return response


@requires_db
@pytest.mark.asyncio
async def test_a_read_only_loud_command_is_answered_with_the_prefix(db_session) -> None:
    response = await _route(db_session, "what time is it", room="lr-r1")
    assert response.text.startswith("It's loud in here. I heard: what time is it. ")
    assert len(response.text) > len("It's loud in here. I heard: what time is it. ")
    assert response.matched_handler == "clock"


async def _shopping(db_session):
    from domovoi import lists

    lst = await lists.find_list(db_session, "shopping")
    if lst is None:
        lst = await lists.create_list(db_session, "Shopping")
    await db_session.commit()
    return lst


async def _items(db_session, lst) -> list[str]:
    from domovoi import lists

    return [i.text.lower() for i in await lists.items_of(db_session, lst.id)]


@requires_db
@pytest.mark.asyncio
async def test_a_house_changing_loud_command_is_asked_about_and_yes_runs_it(db_session) -> None:
    from domovoi.db.repositories import SessionRepository

    lst = await _shopping(db_session)
    asked = await _route(db_session, "add oat milk to the shopping list", room="lr-r2")
    assert asked.text == "It's loud in here. Should I add oat milk to the shopping list?"
    assert asked.expect_followup is True and asked.data.get("loud_confirm") is True
    assert "oat milk" not in await _items(db_session, lst), "nothing runs before the yes"
    pending = (await SessionRepository(db_session).get_context(asked.session_id))[
        "pending_confirmation"]
    assert pending["kind"] == loud_room.LOUD_KIND and pending["handler"] == "lists"

    # The yes comes on the follow-up, itself maybe a loud capture.
    done = await _route(db_session, "Yes.", room="lr-r2", session_id=asked.session_id)
    assert done.matched_path == "confirmation" and done.matched_handler == "lists"
    assert "oat milk" in await _items(db_session, lst)
    assert (await SessionRepository(db_session).get_context(asked.session_id)).get(
        "pending_confirmation") is None


@requires_db
@pytest.mark.asyncio
async def test_no_runs_nothing(db_session) -> None:
    lst = await _shopping(db_session)
    asked = await _route(db_session, "add rye bread to the shopping list", room="lr-r3")
    assert asked.text.startswith("It's loud in here. Should I add rye bread")
    said = await _route(db_session, "no", room="lr-r3", session_id=asked.session_id, loud=False)
    assert said.text == "Okay, I won't."
    assert "rye bread" not in await _items(db_session, lst)
    # And a later yes is about nothing.
    later = await _route(db_session, "yes", room="lr-r3", session_id=asked.session_id, loud=False)
    assert "rye bread" not in await _items(db_session, lst)
    assert later.text != "Okay, I won't."


@requires_db
@pytest.mark.asyncio
async def test_a_stale_yes_runs_nothing(db_session) -> None:
    from domovoi.db.repositories import SessionRepository
    from domovoi.confirmations import PENDING_CONFIRMATION_KEY

    lst = await _shopping(db_session)
    asked = await _route(db_session, "add capers to the shopping list", room="lr-r4")
    stale = loud_room.pending_payload("lists", "add capers to the shopping list", now=0.0)
    await SessionRepository(db_session).set_context_key(
        asked.session_id, PENDING_CONFIRMATION_KEY, stale)
    await db_session.commit()
    await _route(db_session, "yes", room="lr-r4", session_id=asked.session_id, loud=False)
    assert "capers" not in await _items(db_session, lst)


@requires_db
@pytest.mark.asyncio
async def test_a_quiet_room_runs_commands_as_always(db_session) -> None:
    lst = await _shopping(db_session)
    response = await _route(db_session, "add figs to the shopping list", room="lr-r5", loud=False)
    assert not response.text.startswith("It's loud in here")
    assert "figs" in await _items(db_session, lst)
