"""ChatModeHandler — the spoken entry surface for conversational chat mode
(Feature 8).

The handler only flips the session into conversational mode (two keys in
``sessions.context``) and acks; the per-turn Letta dispatch + exit live in
the streaming layer. These tests cover the mode flip and assert the
tightly-anchored enter regex doesn't poach command-mode phrasing.
"""

from __future__ import annotations

import types
from typing import Any
from uuid import uuid4

import pytest

from domovoi.handlers.chat_mode import ChatModeHandler, _ENTER_RE
from domovoi.models import Context
from domovoi.db.repositories import SessionRepository
from domovoi.db.session import session_scope
from domovoi.tests.conftest import requires_db


@pytest.mark.parametrize(
    "phrase",
    [
        "let's chat",
        "let's have a chat",
        "lets chat",
        "let's talk",
        "can we talk",
        "can we have a conversation",
        "i'd like to chat",
        "i want to talk",
        "have a conversation",
        "start chat mode",
        "enter conversation mode",
    ],
)
def test_enter_regex_matches_conversation_openers(phrase: str) -> None:
    assert _ENTER_RE.match(phrase) is not None


@pytest.mark.parametrize(
    "phrase",
    [
        "play some jazz",
        "talk to the kitchen",          # intercom, not chat
        "find me a podcast",
        "chat",                          # bare — too greedy to accept
        "what's the chat about",
        "stop",
        "drop in on the garage",
        "set a timer for ten minutes",
    ],
)
def test_enter_regex_does_not_poach_commands(phrase: str) -> None:
    assert _ENTER_RE.match(phrase) is None


def _streaming_ctx(
    *, session_id: Any, room_id: str = "kitchen", live_rooms: tuple[str, ...] = ()
) -> Context:
    """A Context shaped like the one the STREAMING layer stamps: it carries the
    FastAPI app, whose ``state.active_sessions`` is the live room_id → Pi
    registry. ``/v1/intent`` stamps no app at all — that's `app=None` below."""
    app = types.SimpleNamespace(
        state=types.SimpleNamespace(
            active_sessions={r: object() for r in live_rooms},
            satellite_full_duplex={},
        )
    )
    return Context(session_id=session_id, room_id=room_id, online=True, app=app)


class _ExplodingSession:
    """Stand-in for the AsyncSession. Every decline branch must return BEFORE
    any DB work, so touching this at all means the gate let the turn through.
    (Inside the handler's try/except it degrades to the soft-failure reply,
    which the assertions below distinguish from the satellite decline.)"""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"declined chat entry touched the DB (.{name})")


@pytest.mark.asyncio
async def test_enter_declines_without_a_live_satellite_stream() -> None:
    """F-V009: the router mints a session id for EVERY turn, so a bare
    ``POST /v1/intent`` sailed straight past the old ``session_id is None``
    guard and flipped the session into conversational mode with no mic to
    serve it. The gate is now "is a satellite streaming this room" — and
    /v1/intent stamps no ``ctx.app``, so there is no live stream to find."""
    ctx = Context(session_id=uuid4(), room_id="kitchen", online=True)  # no app
    resp = await ChatModeHandler()._enter(ctx, _ExplodingSession())  # type: ignore[arg-type]
    assert resp.matched_handler == "chat_mode"
    assert "satellite" in resp.text.lower()
    assert resp.expect_followup is not True


@pytest.mark.asyncio
async def test_enter_declines_when_this_room_has_no_connected_pi() -> None:
    """A streaming context for a DIFFERENT room is still no mic here."""
    ctx = _streaming_ctx(session_id=uuid4(), room_id="kitchen", live_rooms=("office",))
    resp = await ChatModeHandler()._enter(ctx, _ExplodingSession())  # type: ignore[arg-type]
    assert "satellite" in resp.text.lower()


@pytest.mark.asyncio
async def test_enter_does_not_over_refuse_a_room_with_a_live_pi() -> None:
    """The gate must not refuse the real case. DB-free, so the context write
    fails and the handler speaks its soft failure — what this asserts is that
    the turn got PAST the satellite gate rather than being declined by it."""
    ctx = _streaming_ctx(session_id=uuid4(), room_id="kitchen", live_rooms=("kitchen",))
    resp = await ChatModeHandler()._enter(ctx, _ExplodingSession())  # type: ignore[arg-type]
    assert "satellite" not in resp.text.lower()


@requires_db
@pytest.mark.asyncio
async def test_enter_sets_conversational_mode_and_start_pending() -> None:
    """Entering chat mode parks ``conversational_mode`` + ``chat_start_pending``
    in the session context and acks with expect_followup."""
    async with session_scope() as s:
        sid = await SessionRepository(s).get_or_create(None, "kitchen")

    ctx = _streaming_ctx(session_id=sid, room_id="kitchen", live_rooms=("kitchen",))
    async with session_scope() as s:
        resp = await ChatModeHandler()._enter(ctx, s)

    assert resp.matched_handler == "chat_mode"
    assert resp.expect_followup is True
    assert resp.text  # a spoken ack

    async with session_scope() as s:
        c = await SessionRepository(s).get_context(sid)
    assert c.get("conversational_mode") is True
    assert c.get("chat_start_pending") is True


@pytest.mark.asyncio
async def test_enter_without_session_declines_gracefully() -> None:
    """A direct /v1/intent call (no session) can't open a mic — the handler
    speaks a soft decline instead of crashing. DB-free on purpose: the decline
    returns before any session write, so this must not hide behind
    ``requires_db``."""
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    resp = await ChatModeHandler()._enter(ctx, _ExplodingSession())  # type: ignore[arg-type]
    assert resp.matched_handler == "chat_mode"
    assert "satellite" in resp.text.lower()
