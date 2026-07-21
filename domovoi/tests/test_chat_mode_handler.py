"""ChatModeHandler — the spoken entry surface for conversational chat mode
(Feature 8).

The handler only flips the session into conversational mode (two keys in
``sessions.context``) and acks; the per-turn Letta dispatch + exit live in
the streaming layer. These tests cover the mode flip and assert the
tightly-anchored enter regex doesn't poach command-mode phrasing.
"""

from __future__ import annotations

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


@requires_db
@pytest.mark.asyncio
async def test_enter_sets_conversational_mode_and_start_pending() -> None:
    """Entering chat mode parks ``conversational_mode`` + ``chat_start_pending``
    in the session context and acks with expect_followup."""
    async with session_scope() as s:
        sid = await SessionRepository(s).get_or_create(None, "kitchen")

    ctx = Context(session_id=sid, room_id="kitchen", online=True)
    async with session_scope() as s:
        resp = await ChatModeHandler()._enter(ctx, s)

    assert resp.matched_handler == "chat_mode"
    assert resp.expect_followup is True
    assert resp.text  # a spoken ack

    async with session_scope() as s:
        c = await SessionRepository(s).get_context(sid)
    assert c.get("conversational_mode") is True
    assert c.get("chat_start_pending") is True


@requires_db
@pytest.mark.asyncio
async def test_enter_without_session_declines_gracefully() -> None:
    """A direct /v1/intent call (no session) can't open a mic — the handler
    speaks a soft decline instead of crashing."""
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    async with session_scope() as s:
        resp = await ChatModeHandler()._enter(ctx, s)
    assert resp.matched_handler == "chat_mode"
    assert "satellite" in resp.text.lower()
