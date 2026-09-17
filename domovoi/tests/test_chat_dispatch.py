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

import asyncio
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


class _SlowFakeWS(_FakeWS):
    """Like ``_FakeWS`` but its sends actually SUSPEND, as a real socket write
    does. That matters for the silence-watchdog tests: a pending cancellation
    is only delivered at a true suspension point, so a send that never yields
    would hide the self-cancel bug these tests pin down."""

    async def send_text(self, data: str) -> None:
        await asyncio.sleep(0)
        await super().send_text(data)


@pytest.mark.asyncio
async def test_clear_chat_mode_does_not_cancel_its_own_watchdog() -> None:
    """F-V009: ``_chat_silence_watchdog`` tears the mode down by calling
    ``_clear_chat_mode``, which used to cancel ``self._chat_silence_task``
    unconditionally — i.e. the very task it was running in. The CancelledError
    landed at the next await and killed the teardown mid-way. Cancelling
    another task is still fine; cancelling yourself is not (same guard as
    ``_end_dropin``)."""
    sess = StreamSession(_FakeWS(), "kitchen")  # type: ignore[arg-type]
    sess.session_id = None  # keep the teardown DB-free
    sess.conversational_mode = True

    async def pretend_to_be_the_watchdog() -> str:
        sess._chat_silence_task = asyncio.current_task()
        await sess._clear_chat_mode()
        # A cancellation scheduled by the call above would be delivered here.
        await asyncio.sleep(0)
        return "survived"

    task = asyncio.create_task(pretend_to_be_the_watchdog())
    try:
        outcome = await task
    except asyncio.CancelledError:
        outcome = "cancelled itself"
    assert outcome == "survived"
    assert sess.conversational_mode is False
    assert sess._chat_silence_task is None


@pytest.mark.asyncio
async def test_silence_watchdog_sends_chat_end_on_an_idle_chat() -> None:
    """F-V009 (step 2): a satellite that goes quiet after "let's chat" must be
    returned to command mode — the watchdog has to actually deliver the
    ``chat_end`` frame that drops the Pi back to its wake loop, not die of its
    own cancellation on the way there."""
    ws = _SlowFakeWS()
    sess = StreamSession(ws, "kitchen")  # type: ignore[arg-type]
    sess.session_id = None  # keep the teardown DB-free
    sess.conversational_mode = True
    # Chat opened long ago and nobody said anything since.
    sess._chat_last_activity = asyncio.get_running_loop().time() - 600.0

    task = asyncio.create_task(sess._chat_silence_watchdog(1.0))
    sess._chat_silence_task = task
    try:
        await asyncio.wait_for(task, timeout=10)
    except asyncio.CancelledError:
        pass  # the bug: the watchdog cancelled itself; assertions below fail

    assert any(
        f.get("type") == "chat_end" and f.get("reason") == "silence_timeout"
        for f in ws.sent_text
    ), "an idle chat must be closed with a chat_end frame"
    assert sess.conversational_mode is False


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
