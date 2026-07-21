"""DismissHandler — brush-off phrasing coverage, the closed "I'm <x>" set
that must NOT shadow VoiceProfile enrollment, and the minimal stand-down
response (short ack, no follow-up, no side effects).
"""

from __future__ import annotations

import pytest

from domovoi.handlers.dismiss import _ACKS, _DISMISS_RE, DismissHandler
from domovoi.handlers.voice_profile import _SELF_INTRO_RE
from domovoi.models import Context, Intent


# ─── Phrasing coverage ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "phrase",
    [
        "never mind",
        "nevermind",
        "never mind then",
        "nvm",
        "nothing",
        "nothing thanks",
        "nothing for now",
        "it's nothing",
        "i don't need anything",
        "i dont need anything else",
        "i don't need you",
        "we don't need anything right now",
        "i'm good",
        "im good",
        "i'm fine",
        "i'm all set",
        "i am done",
        "we're good",
        "no i'm good",
        "all good",
        "all set",
        "no thanks",
        "no thank you",
        "nope",
        "no",
        "nah",
        "nah thanks",
        "not now",
        "not right now",
        "forget it",
        "forget about it",
        "forget that",
        "ignore that",
        "disregard",
        "disregard that",
        "cancel",
        "cancel that",
        "leave it",
        "drop it",
        "false alarm",
        "my mistake",
        "my bad",
        "sorry",
        "sorry never mind",
        "oops",
        "oops never mind",
        "that's all",
        "that's it",
        "that's all for now",
        "go back to sleep",
        "go to sleep",
        "stand down",
        "dismissed",
    ],
)
def test_dismiss_matches(phrase: str) -> None:
    assert _DISMISS_RE.match(phrase), f"expected dismiss match: {phrase!r}"


# ─── Must NOT swallow real commands / enrollment ──────────────────────────


@pytest.mark.parametrize(
    "phrase",
    [
        # Real VoiceProfile enrollments — names outside the closed set.
        "i'm sarah",
        "i am alex",
        "i'm bob",
        # "stop" family is owned by music / radio / chat-exit.
        "stop",
        "stop the music",
        "stop the timer",
        # Real media / timer / memory commands.
        "play something",
        "play that again",
        "cancel the timer",
        "forget that i like jazz",
        "forget my favorite team",
        "what time is it",
        "turn it up",
        # Substrings that merely contain a dismissal word.
        "i need nothing but the best speakers",
        "nothing compares to you",  # a song request-ish phrase
        "no i want the other one",
    ],
)
def test_dismiss_does_not_swallow(phrase: str) -> None:
    assert not _DISMISS_RE.match(phrase), f"unexpected dismiss match: {phrase!r}"


def test_closed_set_does_not_shadow_real_enrollment() -> None:
    """The dismiss "i'm <x>" arm only covers the closed brush-off set, so a
    genuine "i'm <name>" still reaches VoiceProfile's self-intro regex."""
    # Dismissals: matched by dismiss, and (harmlessly) also enrollment-shaped —
    # dismiss wins by being first in the registry.
    assert _DISMISS_RE.match("i'm good")
    # Real names: NOT a dismissal, DO match enrollment.
    for name_phrase in ("i'm sarah", "i am alexandra", "i'm mary jane"):
        assert not _DISMISS_RE.match(name_phrase), name_phrase
        assert _SELF_INTRO_RE.match(name_phrase), name_phrase


def test_dismiss_is_registered_first() -> None:
    from domovoi.handlers import HANDLERS

    assert HANDLERS[0].name == "dismiss"


# ─── Response shape ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dismiss_response_is_minimal_ack() -> None:
    handler = DismissHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    m = _DISMISS_RE.match("never mind")
    assert m
    resp = await handler._from_match(m, ctx, None)
    assert resp.text in _ACKS
    assert resp.matched_handler == "dismiss"
    assert resp.expect_followup is False
    # A dismissal must never carry side effects (no music stop, no offer).
    assert resp.music_action is None
    assert resp.satellite_volume is None
    assert resp.pi_action is None


@pytest.mark.asyncio
async def test_execute_and_tool_paths_ack() -> None:
    handler = DismissHandler()
    ctx = Context(session_id=None, room_id=None, online=True)
    intent = Intent(transcript="i don't need anything", room_id=None)
    r1 = await handler.execute(intent, ctx, None)
    r2 = await handler.execute_from_tool({}, ctx, None)
    assert r1.text in _ACKS and r2.text in _ACKS
    assert r1.matched_handler == "dismiss" and r2.matched_handler == "dismiss"
