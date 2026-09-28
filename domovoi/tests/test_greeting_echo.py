"""A transcript that is only Domovoi's own wake greeting is never a command.

Office satellite, 2026-09-28. Row #186 "What time is it?" was answered by
the clock; ten seconds later the user woke it again, the satellite played
the funny greeting "Back so soon?", the greeting bled past the XVF3800's
echo cancellation, and the capture ended 1.2 s later on the pause after it.
Whisper heard "Back so soon." and row #187 routed Domovoi's own line to the
LLM as if the user had said it. The user's real request, "Tell me a joke.",
went into a closed mic and had to be asked again (#188).

The satellite now reports which clip played (``greeting_clip``) and keeps
listening past the greeting (satellite/tests/test_greeting_endpointing.py);
the core drops a turn that is nothing but a greeting whenever
``greeting_played`` was set. Driven through ``_process_utterance`` with a
fake socket and a scripted Whisper; the router must not be reached.
"""

from __future__ import annotations

import json
import types

import pytest

from domovoi import streaming
from domovoi.canned_sounds import _greeting_entries
from domovoi.streaming import StreamSession
from domovoi.tests.conftest import requires_db

# A slice of the shipped V001 bank, as app.state holds it after boot.
_ROWS = [
    ("Back so soon?", "funny"),
    ("I'm listening.", "generic"),
    ("Hello!", "generic"),
    ("Yes?", "generic"),
    ("Beep boop. How can I help?", "funny"),
    # Lines a person's own question can be built on (see
    # test_a_question_shaped_like_a_greeting_is_the_user).
    ("What can I do for you?", "generic"),
    ("How can I help?", "generic"),
    ("What do you need?", "generic"),
]
_PHRASES = [text for text, _ in _ROWS]
_CLIPS = {mp3: text for mp3, _sidecar, text in _greeting_entries(_ROWS)}
_BACK_SO_SOON = next(mp3 for mp3, text in _CLIPS.items() if text == "Back so soon?")


class _FakeWS:
    def __init__(self, *, clips: bool = True) -> None:
        self.app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                satellite_voice={}, wifi_status={}, satellite_volume={},
                # Every real app has it: utterance_start reads the room's
                # reported silence timeout (speculative transcription).
                satellite_config={},
                greeting_phrases=list(_PHRASES),
                greeting_clips=dict(_CLIPS) if clips else {},
                probe=types.SimpleNamespace(online=True),
            )
        )
        self.sent_text: list[dict] = []
        self.sent_bytes: list[bytes] = []

    async def send_text(self, data: str) -> None:
        self.sent_text.append(json.loads(data))

    async def send_bytes(self, data: bytes) -> None:
        self.sent_bytes.append(data)


class _Whisper:
    def __init__(self, text: str) -> None:
        self.text = text

    async def transcribe(self, pcm_bytes: bytes) -> str:
        return self.text


class _Reached(Exception):
    pass


@pytest.fixture
def routed(monkeypatch):
    """Transcripts that reached the router. Routing is stopped right there
    (the rest of the path needs a DB; the turn's own error handling takes
    the exception) — reaching it is the assertion."""
    seen: list[str] = []

    async def _route(intent, ctx, session):
        seen.append(intent.transcript)
        raise _Reached(intent.transcript)

    monkeypatch.setattr(streaming, "route", _route)
    return seen


async def _turn(monkeypatch, heard: str, *, ws: _FakeWS | None = None, **kwargs) -> _FakeWS:
    monkeypatch.setattr(streaming, "get_whisper_client", lambda: _Whisper(heard))
    ws = ws or _FakeWS()
    sess = StreamSession(ws, "office")  # type: ignore[arg-type]
    await sess._process_utterance(b"\x00" * 32_000, **kwargs)
    return ws


def _dropped(ws: _FakeWS) -> bool:
    ends = [f for f in ws.sent_text if f.get("type") == "response_end"]
    return (
        len(ends) == 1
        and ends[0]["interrupted"] is True
        and ends[0]["expect_followup"] is False
        and not any(f.get("type") in ("transcript", "response_start") for f in ws.sent_text)
        and ws.sent_bytes == []
    )


def test_the_clip_name_is_the_one_the_satellite_plays():
    """greet_funny_a995c16f.mp3 is the file the office Pi played for #187."""
    assert _BACK_SO_SOON == "greet_funny_a995c16f.mp3"


@pytest.mark.asyncio
@pytest.mark.parametrize("clip", [_BACK_SO_SOON, None], ids=["clip-named", "old-satellite"])
async def test_row_187_is_dropped_not_routed(monkeypatch, routed, clip) -> None:
    ws = await _turn(
        monkeypatch, "Back so soon.",
        greeting_played=True, trigger="wake_word", greeting_clip=clip,
    )
    assert routed == []
    assert _dropped(ws)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "heard,clip_text",
    [
        ("I am listening.", "I'm listening."),        # #169
        ("Hello.", "Hello!"),                         # #168
        ("Big boob, how can I help?", "Beep boop. How can I help?"),
        ("Uh, back so soon?", "Back so soon?"),
    ],
)
async def test_other_greeting_echoes_are_dropped(monkeypatch, routed, heard, clip_text) -> None:
    clip = next(mp3 for mp3, text in _CLIPS.items() if text == clip_text)
    ws = await _turn(
        monkeypatch, heard, greeting_played=True, trigger="wake_word", greeting_clip=clip,
    )
    assert routed == []
    assert _dropped(ws)


@pytest.mark.asyncio
async def test_a_command_after_the_greeting_still_routes(monkeypatch, routed) -> None:
    await _turn(
        monkeypatch, "Back so soon? Tell me a joke.",
        greeting_played=True, trigger="wake_word", greeting_clip=_BACK_SO_SOON,
    )
    assert routed == ["Tell me a joke."]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "heard,clip_text,reaches",
    [
        # "Yes?" played, bled in, and the person answered "yes" after it.
        ("Yes? Yes.", "Yes?", "Yes."),
        ("Yes? Yes.", None, "Yes."),
        # Any greeting stripped off the front; what is left is a bank line.
        ("Back so soon? Yes.", None, "Yes."),
        ("Back so soon? Hello.", None, "Hello."),
    ],
    ids=["yes-after-yes-clip", "yes-after-yes-bank", "yes-after-back-bank", "hello-after-back-bank"],
)
async def test_what_follows_a_stripped_greeting_is_the_user(
    monkeypatch, routed, heard, clip_text, reaches,
) -> None:
    """The greeting plays once. Once it has been stripped off the front of
    a transcript, whatever follows it was said after it — by the person —
    even when those words are also a line of the greeting bank. A "yes"
    that answers a parked question must not be dropped as the greeting."""
    clip = (
        next(mp3 for mp3, text in _CLIPS.items() if text == clip_text) if clip_text else None
    )
    await _turn(
        monkeypatch, heard, greeting_played=True, trigger="wake_word", greeting_clip=clip,
    )
    assert routed == [reaches]


@pytest.mark.asyncio
async def test_a_different_bank_line_is_the_user_when_the_clip_is_known(monkeypatch, routed) -> None:
    """"Back so soon?" played; "Hello." is the person, not the greeting."""
    await _turn(
        monkeypatch, "Hello.",
        greeting_played=True, trigger="wake_word", greeting_clip=_BACK_SO_SOON,
    )
    assert routed == ["Hello."]


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["followup", "barge_in", "wake_word"])
async def test_no_greeting_played_means_no_greeting_drop(monkeypatch, routed, trigger) -> None:
    """A follow-up "Yes." (#164) or a wake turn with the greeting off is
    the user speaking, whatever it sounds like."""
    await _turn(monkeypatch, "Yes.", greeting_played=False, trigger=trigger)
    assert routed == ["Yes."]


@pytest.mark.asyncio
async def test_an_unknown_clip_falls_back_to_the_whole_bank(monkeypatch, routed) -> None:
    ws = await _turn(
        monkeypatch, "Back so soon.",
        greeting_played=True, trigger="wake_word", greeting_clip="greet_deadbeef.mp3",
    )
    assert routed == [] and _dropped(ws)


@pytest.mark.asyncio
@pytest.mark.parametrize("heard", [".", " ?! ", "…"])
async def test_a_transcript_of_punctuation_is_blank(monkeypatch, routed, heard) -> None:
    """Office row #114 was "." — routed, and answered."""
    ws = await _turn(monkeypatch, heard, trigger="wake_word")
    assert routed == [] and _dropped(ws)


@pytest.mark.asyncio
async def test_utterance_end_hands_the_clip_to_the_turn(monkeypatch) -> None:
    """The control frame's optional ``greeting_clip`` reaches
    _process_utterance; a frame without it (an older satellite) gives None."""
    calls: list[dict] = []

    async def _capture(self, pcm, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(StreamSession, "_process_utterance", _capture)
    ws = _FakeWS()
    sess = StreamSession(ws, "office")  # type: ignore[arg-type]
    for frame in (
        {"type": "utterance_end", "greeting_played": True, "greeting_clip": _BACK_SO_SOON},
        {"type": "utterance_end", "greeting_played": True},
        {"type": "utterance_end", "greeting_played": True, "greeting_clip": 7},
    ):
        await sess._on_control({"type": "utterance_start", "trigger": "wake_word"})
        await sess._on_control(frame)
        await sess._response_task
    assert [c["greeting_clip"] for c in calls] == [_BACK_SO_SOON, None, None]
    assert all(c["greeting_played"] is True for c in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "heard,reaches",
    [
        ("Back so soon, set a timer for five minutes.", "set a timer for five minutes."),
        ("Back so soon? Set a timer for five minutes.", "Set a timer for five minutes."),
        # No break after the greeting's words: not stripped, and not only a
        # greeting either — it routes exactly as heard.
        ("Back so soon set a timer for five minutes", "Back so soon set a timer for five minutes"),
    ],
)
@pytest.mark.parametrize("clip", [_BACK_SO_SOON, None], ids=["clip-named", "old-satellite"])
async def test_a_command_that_starts_like_the_greeting_is_never_swallowed(
    monkeypatch, routed, heard, reaches, clip,
) -> None:
    await _turn(
        monkeypatch, heard, greeting_played=True, trigger="wake_word", greeting_clip=clip,
    )
    assert routed == [reaches]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "heard,clip_text",
    [
        ("What can you do for me?", "What can I do for you?"),
        ("How can you help?", "How can I help?"),
        ("What do you mean?", "What do you need?"),
        ("What do you know?", "What do you need?"),
    ],
)
@pytest.mark.parametrize("named", [True, False], ids=["clip-named", "old-satellite"])
async def test_a_question_shaped_like_a_greeting_is_the_user(
    monkeypatch, routed, heard, clip_text, named,
) -> None:
    """Close to a greeting in characters, but a different sentence: the
    words that differ are other words, not misheard ones. Dropping it would
    leave the person's question unanswered, in silence."""
    clip = next(mp3 for mp3, text in _CLIPS.items() if text == clip_text) if named else None
    await _turn(
        monkeypatch, heard, greeting_played=True, trigger="wake_word", greeting_clip=clip,
    )
    assert routed == [heard]


@requires_db
@pytest.mark.asyncio
async def test_the_stripped_timer_command_routes_to_the_timer(db_session) -> None:
    """What reaches the router after "Back so soon, …" is stripped is an
    ordinary timer command, and the timer takes it."""
    from domovoi.models import Context, Intent
    from domovoi.router import route

    response = await route(
        Intent(transcript="set a timer for five minutes.", room_id="office"),
        Context(room_id="office", online=True),
        db_session,
    )
    await db_session.commit()
    assert response.matched_handler == "timer"
    assert response.matched_path == "fast"
