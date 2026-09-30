"""The second hearing: a short-window transcript headed for the tool router
is decoded again on the 30 s path first.

Short captures decode on a 10 s mel window (clients/whisper.py), about a
quarter of the 30 s path's time on a CPU. On clean TTS clips the two
windows hear commands equally well; on the same clips made far-field
(2026-09-30 review: reverberation plus noise, small.en, 252 tier A/B/B1
commands) the 10 s window changed about one outcome in ten, nearly all
between a fast path and the LLM route. A transcript that matches no fast
path and isn't a plain question is either one of those misheard commands
or a turn about to wait seconds for the tool router — so it is heard again
on the 30 s path, and that text is routed. Scored at or above both windows
in every condition measured (e.g. 205 against 202 / 191).

Pinned here, DB-free:
  * which transcripts go to the tool model (``router.goes_to_the_tool_model``);
  * the Whisper client's 30 s path by name, even for a short capture;
  * a question about the world ("What is the capital of France?") is not
    heard again: streamed, the router gives it up in ~0.7 s, and a second
    decode would cost more than that for nothing;
  * the turn: a misheard command is heard again and the 30 s text routed,
    recorded as ``stt_window_s`` 30 + ``stt_rechecked`` + ``stt_recheck_ms``
    with the wait in ``stt_ms`` / ``stt_wait_ms``; a fast-path command, a
    plain question, a yes/no, an early commit, a 30 s first hearing, a
    client with one path and the setting off are not heard again; a
    second hearing that fails or hears nothing usable leaves the first.
"""

from __future__ import annotations

import asyncio
import json
import types

import pytest
from fastapi.testclient import TestClient

from domovoi.clients.whisper import (
    FULL_WINDOW_SEC,
    SHORT_WINDOW_SEC,
    FasterWhisperClient,
    WhisperStubClient,
    can_decode_full_window,
    transcribe_full_window,
)
from domovoi.config import Settings, settings
from domovoi.main import app
from domovoi.router import goes_to_the_tool_model, is_question_about_the_world
from domovoi.streaming import StreamSession, _Heard
from domovoi.tests.test_speculative_stt import (  # noqa: F401 - `pipeline` is a fixture
    LOUD,
    QUIET,
    _connect,
    _end,
    _finish_turn,
    _send,
    pipeline,
)

FRAME_BYTES = 960


# ─── which transcripts go to the tool model ───────────────────────────────


@pytest.mark.parametrize(
    "transcript",
    [
        "Pose the music.",                        # "pause the music", misheard
        "Could you put on something relaxing?",
        "What is the capital of France?",         # "what" fronts commands: routed
        "Remind me to take out the trash tomorrow.",
        "Please let the kitchen know dinner is ready.",
    ],
)
def test_these_go_to_the_tool_model(transcript: str) -> None:
    assert goes_to_the_tool_model(transcript)


@pytest.mark.parametrize(
    "transcript",
    [
        "Pause the music.", "pause the music", "Stop.",       # fast paths
        "Please pause the music.",                            # filler, then a fast path
        "Set a timer for ten minutes.",
        "Who wrote the Odyssey?", "Tell me a joke.",          # straight to Q&A
        "Yes.", "no", "Yeah, sure.",                          # a yes/no answer
        "", ".", "  ?! ",                                     # nothing in it
    ],
)
def test_these_do_not(transcript: str) -> None:
    assert not goes_to_the_tool_model(transcript)


@pytest.mark.parametrize(
    "transcript",
    ["What is the capital of France?", "How far away is the moon?",
     "When did World War Two end?", "Can penguins fly very far?"],
)
def test_a_question_about_the_world_is_not_heard_again(transcript: str) -> None:
    """It goes to the tool model like any unmatched turn, but the streamed
    router gives it up in ~0.7 s: a second 30 s decode would add more than
    that, for a question no command was misheard as."""
    assert is_question_about_the_world(transcript)


@pytest.mark.parametrize(
    "transcript",
    [
        "What plane?", "What's true?",                   # "What's playing?", misheard
        "How much time is left on my timer?",            # about the house
        "Can you tell me what time it is?",
        "Who sings this song?",
        "What is the capital of France",                 # not a question as heard
        "Tell me it a joke.",
        "Could you put on something relaxing?",          # a request, filler or not
        "Can you play some jazz?",
    ],
)
def test_short_or_house_questions_are_still_heard_again(transcript: str) -> None:
    assert not is_question_about_the_world(transcript)


# ─── the Whisper client's 30 s path, by name ──────────────────────────────


def test_the_client_decodes_on_the_thirty_second_path_when_asked(monkeypatch) -> None:
    c = object.__new__(FasterWhisperClient)
    c.cpu_threads = 8
    c.short_window = types.SimpleNamespace(
        fits=lambda pcm: True, decode=lambda pcm: pytest.fail("the short window was used"),
    )
    full: list[int] = []

    def _full(pcm: bytes) -> str:
        full.append(len(pcm))
        return "Pause the music."

    monkeypatch.setattr(c, "_transcribe_full_sync", _full)
    assert can_decode_full_window(c)
    assert asyncio.run(transcribe_full_window(c, b"\x00\x00" * 16000)) == ("Pause the music.", FULL_WINDOW_SEC)
    assert full == [32000]


def test_a_client_with_one_path_has_nothing_to_ask_for() -> None:
    assert not can_decode_full_window(WhisperStubClient())
    assert not can_decode_full_window(types.SimpleNamespace(transcribe=lambda pcm: "hi"))


def test_the_setting_is_a_hot_advanced_bool_on_by_default() -> None:
    from pathlib import Path

    from domovoi.config_schema import FIELD_BY_NAME

    assert Settings.model_fields["whisper_short_window_recheck"].default is True
    f = FIELD_BY_NAME["whisper_short_window_recheck"]
    assert (f.type, f.tier, f.section, f.group) == ("bool", "hot", "advanced", "Speech-to-text")
    example = (Path(__file__).resolve().parents[2] / "domovoi" / ".env.example").read_text(encoding="utf-8")
    assert "# WHISPER_SHORT_WINDOW_RECHECK=true" in example


# ─── the turn ─────────────────────────────────────────────────────────────


class _TwoWindows:
    """A Whisper with both paths: ``transcribe_with_window`` answers from the
    10 s window (or ``first_window``), ``transcribe_full_window`` from the
    30 s one. Records every call."""

    def __init__(self, short_text: str, full_text: str = "", *, first_window: int = SHORT_WINDOW_SEC,
                 full_error: Exception | None = None, delay: float = 0.02) -> None:
        self.short_text, self.full_text = short_text, full_text
        self.first_window = first_window
        self.full_error = full_error
        self.delay = delay
        self.calls: list[tuple[str, int]] = []

    async def transcribe(self, pcm: bytes) -> str:
        return (await self.transcribe_with_window(pcm))[0]

    async def transcribe_with_window(self, pcm: bytes) -> tuple[str, int]:
        self.calls.append(("window", len(pcm)))
        await asyncio.sleep(self.delay)
        return self.short_text, self.first_window

    async def transcribe_full_window(self, pcm: bytes) -> tuple[str, int]:
        self.calls.append(("full", len(pcm)))
        await asyncio.sleep(self.delay)
        if self.full_error is not None:
            raise self.full_error
        return self.full_text, FULL_WINDOW_SEC

    async def transcribe_wav_bytes(self, wav: bytes) -> str:
        return self.short_text


def _one_turn(pipeline, whisper) -> tuple[str, dict]:
    pipeline["whisper"] = whisper
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        _connect(ws, hints=False)
        ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word"}))
        _send(ws, [LOUD] * 30 + [QUIET] * 10)
        ws.send_text(_end())
        spoken = _finish_turn(ws)
    (routed, doc), = pipeline["routed"]
    assert routed == spoken
    return routed, doc


@pytest.fixture
def recheck_on(monkeypatch):
    monkeypatch.setattr(settings, "whisper_short_window_recheck", True)


def test_a_misheard_command_is_heard_again_and_the_thirty_second_text_routed(pipeline, recheck_on) -> None:
    whisper = _TwoWindows("Pose the music.", "Pause the music.")
    routed, doc = _one_turn(pipeline, whisper)
    assert routed == "Pause the music."
    assert whisper.calls == [("window", 40 * FRAME_BYTES), ("full", 40 * FRAME_BYTES)]
    assert doc["stt_window_s"] == FULL_WINDOW_SEC
    assert doc["stt_rechecked"] is True
    assert doc["stt_recheck_ms"] >= 20 - 15               # the second call's own time
    assert doc["stt_ms"] >= doc["stt_recheck_ms"] + 20 - 15
    assert doc["stt_wait_ms"] >= doc["stt_ms"] - 15


def test_the_turn_log_line_says_it_was_heard_twice() -> None:
    from domovoi.turn_timings import TurnTimings

    t = TurnTimings()
    t.flags.update(stt_window_s=30, stt_rechecked=True, stt_recheck_ms=900)
    assert "window=30s/recheck" in t.describe()
    t.flags.pop("stt_rechecked")
    assert "window=30s " in t.describe() + " "


@pytest.mark.parametrize(
    "heard",
    ["Pause the music.", "Tell me a joke.", "Who wrote the Odyssey?", "Yes."],
)
def test_a_command_or_a_plain_question_keeps_the_short_window(pipeline, recheck_on, heard) -> None:
    whisper = _TwoWindows(heard, "should not be asked")
    routed, doc = _one_turn(pipeline, whisper)
    assert routed == heard
    assert [kind for kind, _ in whisper.calls] == ["window"]
    assert doc["stt_window_s"] == SHORT_WINDOW_SEC
    assert "stt_rechecked" not in doc


def test_a_plain_world_question_keeps_the_short_window(pipeline, recheck_on) -> None:
    whisper = _TwoWindows("What is the capital of France?", "should not be asked")
    routed, doc = _one_turn(pipeline, whisper)
    assert routed == "What is the capital of France?"
    assert [kind for kind, _ in whisper.calls] == ["window"]
    assert doc["stt_window_s"] == SHORT_WINDOW_SEC and "stt_rechecked" not in doc


def test_a_thirty_second_first_hearing_is_not_heard_again(pipeline, recheck_on) -> None:
    whisper = _TwoWindows("Could you put on something relaxing?", "nope", first_window=FULL_WINDOW_SEC)
    routed, doc = _one_turn(pipeline, whisper)
    assert routed == "Could you put on something relaxing?"
    assert [kind for kind, _ in whisper.calls] == ["window"]


def test_the_setting_off_routes_the_short_window_text(pipeline, monkeypatch) -> None:
    monkeypatch.setattr(settings, "whisper_short_window_recheck", False)
    whisper = _TwoWindows("Pose the music.", "Pause the music.")
    routed, doc = _one_turn(pipeline, whisper)
    assert routed == "Pose the music."
    assert [kind for kind, _ in whisper.calls] == ["window"]
    assert doc["stt_window_s"] == SHORT_WINDOW_SEC


@pytest.mark.parametrize(
    ("full_text", "full_error"),
    [("", None), ("...", None), ("", RuntimeError("decoder crashed"))],
    ids=["blank", "punctuation", "error"],
)
def test_a_second_hearing_with_nothing_usable_leaves_the_first(pipeline, recheck_on, full_text, full_error) -> None:
    """The first hearing did hear speech: a blank or failed second one is
    not a reason to drop the turn."""
    whisper = _TwoWindows("Could you put on something relaxing?", full_text, full_error=full_error)
    routed, doc = _one_turn(pipeline, whisper)
    assert routed == "Could you put on something relaxing?"
    assert [kind for kind, _ in whisper.calls] == ["window", "full"]
    assert doc["stt_window_s"] == SHORT_WINDOW_SEC
    if full_error is None:
        assert doc["stt_rechecked"] is True               # asked, and it counted
    else:
        assert "stt_rechecked" not in doc


def test_a_client_without_windows_is_never_heard_again(pipeline, recheck_on) -> None:
    from domovoi.tests.test_speculative_stt import _WatchedWhisper

    whisper = _WatchedWhisper("Pose the music.")
    routed, doc = _one_turn(pipeline, whisper)
    assert routed == "Pose the music."
    assert len(whisper.calls) == 1 and "stt_window_s" not in doc


def test_an_early_commit_is_never_heard_again(recheck_on) -> None:
    """An early commit is a closed command by construction (a tier A/B fast
    path), so its transcript never goes to the tool model anyway — and the
    turn skips the check outright."""
    ws = types.SimpleNamespace(app=types.SimpleNamespace(state=types.SimpleNamespace(satellite_config={})))
    sess = StreamSession(ws, "kitchen")  # type: ignore[arg-type]
    heard = _Heard(text="Pose the music.", stt_ms=230, whisper=None, window_s=SHORT_WINDOW_SEC)
    assert sess._wants_second_hearing(heard, "Pose the music.", early_commit=False)
    assert not sess._wants_second_hearing(heard, "Pose the music.", early_commit=True)


def test_a_chat_mode_turn_is_never_heard_again(recheck_on) -> None:
    """In chat mode a turn goes to the chat agent, not the tool router."""
    ws = types.SimpleNamespace(app=types.SimpleNamespace(state=types.SimpleNamespace(satellite_config={})))
    sess = StreamSession(ws, "kitchen")  # type: ignore[arg-type]
    sess.conversational_mode = True
    heard = _Heard(text="Pose the music.", stt_ms=230, whisper=None, window_s=SHORT_WINDOW_SEC)
    assert not sess._wants_second_hearing(heard, "Pose the music.", early_commit=False)
