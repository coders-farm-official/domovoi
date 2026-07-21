"""Conversational chat-turn dispatch in the streaming layer (Feature 8).

``StreamSession._run_chat_turn`` REPLACES the command-mode router for a
conversational turn: it dispatches to the Letta client (the stub under
USE_STUBS), streams the reply through TTS, and writes exactly one
``intents_log`` + ``conversation_log`` row with ``matched_path="chat"`` —
which only succeeds because both CHECK constraints allow it. The exit
phrase clears the mode + sends a ``chat_end`` frame.

These drive ``_run_chat_turn`` directly with a tiny fake socket whose
``.app.state`` carries the one field the path reads (``satellite_voice``),
so no full app lifespan is needed.
"""

from __future__ import annotations

import json
import types

import pytest
from sqlalchemy import text as sql_text

from domovoi.models import Context, Response
from domovoi.streaming import StreamSession
from domovoi.db.repositories import SessionRepository
from domovoi.db.session import session_scope
from domovoi.tests.conftest import requires_db


class _FakeWS:
    def __init__(self) -> None:
        self.app = types.SimpleNamespace(
            state=types.SimpleNamespace(satellite_voice={})
        )
        self.sent_text: list[dict] = []
        self.sent_bytes: list[bytes] = []

    async def send_text(self, data: str) -> None:
        self.sent_text.append(json.loads(data))

    async def send_bytes(self, data: bytes) -> None:
        self.sent_bytes.append(data)


def test_response_accepts_chat_matched_path() -> None:
    """The models.py Literal was widened — constructing a chat Response is
    valid (the construction path IS pydantic-validated, unlike assignment)."""
    r = Response(matched_path="chat", text="hi")
    assert r.matched_path == "chat"


@requires_db
@pytest.mark.asyncio
async def test_chat_turn_persists_matched_path_chat() -> None:
    """A normal conversational turn logs one conversation_log row with
    matched_path='chat' (no CheckViolation on either table)
    and emits a spoken reply, all without touching the router."""
    async with session_scope() as s:
        sid = await SessionRepository(s).get_or_create(None, "kitchen")
        await s.execute(
            sql_text("DELETE FROM conversation_log WHERE session_id = :sid"),
            {"sid": sid},
        )

    ws = _FakeWS()
    sess = StreamSession(ws, "kitchen")  # type: ignore[arg-type]
    sess.session_id = sid  # a live session has this set; teardown reads self.session_id
    ctx = Context(session_id=sid, room_id="kitchen", online=True)

    await sess._run_chat_turn(
        transcript="tell me a fun fact", ctx=ctx, session_id=sid, exiting=False
    )

    # The audit row landed with matched_path='chat'.
    async with session_scope() as s:
        row = await s.execute(
            sql_text(
                "SELECT matched_path, matched_handler FROM conversation_log "
                "WHERE session_id = :sid ORDER BY at DESC LIMIT 1"
            ),
            {"sid": sid},
        )
        got = row.first()
    assert got is not None, "a conversation_log row should have been written"
    assert got[0] == "chat"
    assert got[1] == "chat_mode"

    # A spoken reply was streamed (response_start framing from the stub reply).
    assert any(f.get("type") == "response_start" for f in ws.sent_text)


@requires_db
@pytest.mark.asyncio
async def test_chat_exit_clears_mode_and_sends_chat_end() -> None:
    """An exit turn clears conversational_mode from the session context and
    sends a chat_end frame so the Pi drops back to its wake loop."""
    async with session_scope() as s:
        repo = SessionRepository(s)
        sid = await repo.get_or_create(None, "kitchen")
        await repo.set_context_key(sid, "conversational_mode", True)
        await repo.set_context_key(sid, "letta_agent_id", "stub-agent-domovoi")

    ws = _FakeWS()
    sess = StreamSession(ws, "kitchen")  # type: ignore[arg-type]
    sess.session_id = sid  # a live session has this set; _clear_chat_mode reads it
    ctx = Context(session_id=sid, room_id="kitchen", online=True)

    await sess._run_chat_turn(
        transcript="we're done", ctx=ctx, session_id=sid, exiting=True
    )

    # chat_end frame sent.
    assert any(f.get("type") == "chat_end" for f in ws.sent_text)
    # Mode cleared in the session context.
    async with session_scope() as s:
        c = await SessionRepository(s).get_context(sid)
    assert not c.get("conversational_mode")
    assert not c.get("letta_agent_id")


def test_chat_exit_phrases_are_intent_explicit() -> None:
    """Only intent-explicit leave phrases (stop / done / end-the-chat) end a
    chat. Courtesy + wind-down phrases are deliberately NOT exits — too easily
    said mid-conversation without meaning to leave (see _CHAT_EXIT_RE)."""
    from domovoi.streaming import _is_chat_exit

    for phrase in (
        "stop", "stop talking", "stop the chat", "we're done", "i'm done",
        "all done", "done chatting", "end the chat", "let's stop talking",
        "exit chat", "quit chat mode", "end the conversation", "leave the chat",
    ):
        assert _is_chat_exit(phrase), f"{phrase!r} should end the chat"

    for phrase in (
        "thanks", "thank you", "thanks goodbye", "goodbye", "bye", "bye bye",
        "that's all", "that'll be all", "that's all for now", "never mind",
        "nevermind",
    ):
        assert not _is_chat_exit(phrase), f"{phrase!r} should NOT end the chat"

    # Explicit exit COMMANDS are honored even when tacked onto the end of a
    # sentence — users naturally append "end the chat" to a goodbye, which is
    # what made chat mode hard to leave (only a bare whole-turn "stop" worked).
    for phrase in (
        "take care and have a great day, end the chat",
        "they can assist you further, end the chat",
        "okay thanks, let's end the conversation",
        "alright, exit chat mode",
    ):
        assert _is_chat_exit(phrase), f"{phrase!r} should end the chat"

    # A bare/ambiguous leave word buried in a larger turn is still NOT an exit
    # — only whole-turn for those, so the turn goes to the model instead.
    assert not _is_chat_exit("stop, play something else")
    assert not _is_chat_exit("i'm done with work, what's the weather")
