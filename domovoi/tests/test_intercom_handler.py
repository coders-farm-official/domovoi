from __future__ import annotations

from uuid import uuid4

import pytest

from domovoi.handlers.intercom import (
    IntercomHandler,
    _ANNOUNCE_BARE_RE,
    _ANNOUNCE_TO_RE,
    _TELL_RE,
    _normalize_room_phrase,
    _resolve_target_rooms,
)
from domovoi.models import Context


# ─── Regex tests ────────────────────────────────────────────────────────────

def test_announce_to_regex_with_explicit_recipient_and_colon() -> None:
    m = _ANNOUNCE_TO_RE.match("announce to the house: dinner's ready")
    assert m and m.group("room") == "the house" and m.group("message") == "dinner's ready"


def test_announce_to_regex_in_room_form_with_comma() -> None:
    m = _ANNOUNCE_TO_RE.match("announce in the kitchen, someone is at the door")
    assert m and m.group("room") == "the kitchen"
    assert m.group("message") == "someone is at the door"


def test_announce_to_regex_requires_punctuation_separator() -> None:
    """The explicit-recipient form needs a colon or comma between room and
    message — without it the lazy room capture can't tell where the
    recipient ends. Bare broadcasts (no recipient) skip this constraint
    via _ANNOUNCE_BARE_RE."""
    assert not _ANNOUNCE_TO_RE.match("announce in the kitchen someone is at the door")


def test_announce_bare_regex_with_colon() -> None:
    m = _ANNOUNCE_BARE_RE.match("announce: pizza is here")
    assert m and m.group("message") == "pizza is here"


def test_announce_bare_regex_no_colon() -> None:
    m = _ANNOUNCE_BARE_RE.match("announce dinner is ready")
    assert m and m.group("message") == "dinner is ready"


def test_broadcast_alias_works_for_both_forms() -> None:
    assert _ANNOUNCE_BARE_RE.match("broadcast: the package arrived")
    assert _ANNOUNCE_TO_RE.match("broadcast to the house: pizza")


def test_tell_regex_broadcast_phrases() -> None:
    """The TELL pattern is intentionally restricted to clear broadcast
    phrases — single-room "tell the kitchen X" routes through
    "announce in the kitchen X" instead, since "tell" overlaps with
    conversational requests like "tell the time" or "tell me a joke"."""
    for s in (
        "tell everyone dinner is ready",
        "tell everybody the package is here",
        "tell the house pizza arrived",
        "tell all rooms power is back",
    ):
        m = _TELL_RE.match(s)
        assert m, f"expected match: {s!r}"


def test_tell_regex_does_not_swallow_qa_phrasings() -> None:
    """QA phrasings like "tell me a story" must NOT match — they should
    fall through to Ollama. Regression test for an earlier draft of the
    pattern that captured "me" as the recipient."""
    for s in (
        "tell me a story",
        "tell me about jupiter",
        "tell me what time it is",
        "tell the time",
        "tell us when to leave",
    ):
        assert not _TELL_RE.match(s), f"unexpected match: {s!r}"


# ─── Room resolution ────────────────────────────────────────────────────────

def test_normalize_room_phrase_strips_the_prefix() -> None:
    assert _normalize_room_phrase("The Kitchen") == "kitchen"
    assert _normalize_room_phrase("the Living Room") == "living room"


def test_resolve_target_rooms_broadcast_phrases() -> None:
    from domovoi.clients import mpd as mpd_module

    mpd_module._room_ports = {"kitchen": (6600, 8001), "garage": (6601, 8002)}
    try:
        for phrase in ("the house", "everyone", "everywhere", "every room", "all rooms"):
            result = _resolve_target_rooms(phrase)
            assert result is not None
            assert set(result) == {"kitchen", "garage"}
    finally:
        mpd_module._room_ports = {}


def test_resolve_target_rooms_specific_room() -> None:
    from domovoi.clients import mpd as mpd_module

    mpd_module._room_ports = {"kitchen": (6600, 8001), "garage": (6601, 8002)}
    try:
        assert _resolve_target_rooms("the kitchen") == ["kitchen"]
        assert _resolve_target_rooms("garage") == ["garage"]
    finally:
        mpd_module._room_ports = {}


def test_resolve_target_rooms_loose_match_on_spaces_underscores() -> None:
    """Spoken "living room" should match a room_id of "living_room" or
    "livingroom" — collapse spaces / underscores before comparing."""
    from domovoi.clients import mpd as mpd_module

    mpd_module._room_ports = {"living_room": (6600, 8001)}
    try:
        assert _resolve_target_rooms("the living room") == ["living_room"]
        assert _resolve_target_rooms("living room") == ["living_room"]
    finally:
        mpd_module._room_ports = {}


def test_resolve_target_rooms_unknown_returns_none() -> None:
    from domovoi.clients import mpd as mpd_module

    mpd_module._room_ports = {"kitchen": (6600, 8001)}
    try:
        assert _resolve_target_rooms("the basement") is None
    finally:
        mpd_module._room_ports = {}


def test_resolve_target_rooms_no_provisioned_rooms_returns_none() -> None:
    from domovoi.clients import mpd as mpd_module

    mpd_module._room_ports = {}
    assert _resolve_target_rooms("the house") is None
    assert _resolve_target_rooms(None) is None


# ─── Behavior ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_announce_to_house_populates_response_targets() -> None:
    from domovoi.clients import mpd as mpd_module

    mpd_module._room_ports = {"kitchen": (6600, 8001), "garage": (6601, 8002)}
    try:
        handler = IntercomHandler()
        ctx = Context(session_id=uuid4(), room_id="kitchen", online=True)
        m = _ANNOUNCE_TO_RE.match("announce to the house: dinner is ready")
        assert m
        response = await handler._from_match(m, ctx, None)

        assert "broadcasting" in response.text.lower()
        assert response.announce_text == "dinner is ready"
        assert set(response.announce_to_rooms) == {"kitchen", "garage"}
    finally:
        mpd_module._room_ports = {}


@pytest.mark.asyncio
async def test_announce_to_specific_room_picks_one() -> None:
    from domovoi.clients import mpd as mpd_module

    mpd_module._room_ports = {"kitchen": (6600, 8001), "garage": (6601, 8002)}
    try:
        handler = IntercomHandler()
        ctx = Context(session_id=uuid4(), room_id="kitchen", online=True)
        m = _ANNOUNCE_TO_RE.match("announce in the garage: someone's at the door")
        assert m
        response = await handler._from_match(m, ctx, None)

        assert "garage" in response.text.lower()
        assert response.announce_text == "someone's at the door"
        assert response.announce_to_rooms == ["garage"]
    finally:
        mpd_module._room_ports = {}


@pytest.mark.asyncio
async def test_announce_to_unknown_room_returns_helpful_error() -> None:
    """Once the regex commits, an unknown room phrase shouldn't fall
    through silently — IntercomHandler tells the user it didn't understand
    the room rather than broadcasting to nobody."""
    from domovoi.clients import mpd as mpd_module

    mpd_module._room_ports = {"kitchen": (6600, 8001)}
    try:
        handler = IntercomHandler()
        ctx = Context(session_id=uuid4(), room_id="kitchen", online=True)
        m = _ANNOUNCE_TO_RE.match("announce in the basement: someone is here")
        assert m
        response = await handler._from_match(m, ctx, None)
        assert "don't know" in response.text.lower()
        assert response.announce_to_rooms == []
        assert response.announce_text is None
    finally:
        mpd_module._room_ports = {}


@pytest.mark.asyncio
async def test_announce_with_no_provisioned_rooms() -> None:
    """If no Pis have ever connected, broadcasting is a no-op with a
    user-readable explanation rather than a stack trace."""
    from domovoi.clients import mpd as mpd_module

    mpd_module._room_ports = {}
    handler = IntercomHandler()
    ctx = Context(session_id=uuid4(), room_id="kitchen", online=True)
    m = _ANNOUNCE_BARE_RE.match("announce dinner")
    assert m
    response = await handler._from_match(m, ctx, None)
    assert "no rooms" in response.text.lower()
    assert response.announce_to_rooms == []


@pytest.mark.asyncio
async def test_execute_from_tool_with_room_all() -> None:
    """LLM tool routing: 'all' / 'house' / 'everyone' / 'everywhere' all
    map to broadcast (recipient = None)."""
    from domovoi.clients import mpd as mpd_module

    mpd_module._room_ports = {"kitchen": (6600, 8001), "garage": (6601, 8002)}
    try:
        handler = IntercomHandler()
        ctx = Context(session_id=uuid4(), room_id="kitchen", online=True)
        for room_arg in ("all", "house", "everyone", "everywhere"):
            response = await handler.execute_from_tool(
                {"room": room_arg, "message": "test"}, ctx, None
            )
            assert set(response.announce_to_rooms) == {"kitchen", "garage"}
            assert response.announce_text == "test"
    finally:
        mpd_module._room_ports = {}
