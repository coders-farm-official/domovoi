"""RepeatHandler tests — phrasing coverage, session-context happy path,
conversation_log room-scoped fallback, empty-context graceful, and the
tool-call entry point.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from domovoi.db.repositories import SessionRepository
from domovoi.handlers.repeat import _REPEAT_RE, RepeatHandler
from domovoi.models import Context, Intent
from domovoi.tests.conftest import requires_db


# ─── Regex coverage ───────────────────────────────────────────────────────


def test_repeat_regex_canonical_phrasings() -> None:
    for s in (
        "repeat",
        "repeat that",
        "repeat it",
        "repeat please",
        "repeat again",
        "please repeat",
        "please repeat that",
        "please repeat it",
        "say that again",
        "say it again",
        "can you repeat",
        "can you repeat that",
        "can you repeat it",
        "could you repeat that",
        "would you repeat that",
        "can you please repeat that",
        "could you please say that again",
        "can you say that again",
        "could you say it again",
        "what did you say",
        "what did you just say",
        "what'd you say",
        "what was that",
        "come again",
        "one more time",
        "once more",
        "i didn't hear that",
        "i didn't hear you",
        "i didn't catch that",
        "i didn't catch what you said",
        "i didnt hear that",
        "i did not catch that",
        "i missed that",
    ):
        assert _REPEAT_RE.match(s), f"expected match: {s!r}"


def test_repeat_regex_does_not_swallow_unrelated() -> None:
    """Anchored against false positives — phrasings that contain
    'say' / 'again' / 'what' but mean a different intent must not
    route here."""
    for s in (
        # music — "play that again" is a music intent, not a repeat
        "play that again",
        "play it again",
        # bare singletons — too ambiguous to route
        "again",
        "what",
        "huh",
        # questions about other things
        "what time is it",
        "what was the time",
        "say hello to the kitchen",
        "tell the kitchen i'm home",
        # not actually asking for a repeat
        "i didn't know that",
        "i didn't ask",
        "i missed dinner",
        "come back tomorrow",
    ):
        assert not _REPEAT_RE.match(s), f"unexpected match: {s!r}"


# ─── No-context graceful ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_repeat_with_no_session_and_no_room_responds_gracefully() -> None:
    """No session_id, no room_id → no prior response anywhere → polite
    'nothing recent' message rather than an error."""
    handler = RepeatHandler()
    ctx = Context(session_id=None, room_id=None, online=True)
    response = await handler._repeat(ctx, None)
    assert "anything recent" in response.text.lower()
    assert response.matched_handler == "repeat"


@requires_db
@pytest.mark.asyncio
async def test_repeat_with_no_session_and_empty_room_history_responds_gracefully(
    db_session,
) -> None:
    """room_id set but conversation_log is empty → friendly fallback."""
    handler = RepeatHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    response = await handler._repeat(ctx, db_session)
    assert "anything recent" in response.text.lower()


@requires_db
@pytest.mark.asyncio
async def test_repeat_with_session_but_empty_last_response_responds_gracefully(
    db_session,
) -> None:
    """Session exists but last_assistant_response is missing AND the
    conversation_log is empty — same friendly fallback."""
    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")
    await db_session.commit()

    handler = RepeatHandler()
    ctx = Context(session_id=sid, room_id="kitchen", online=True)
    response = await handler._repeat(ctx, db_session)
    assert "anything recent" in response.text.lower()


# ─── Happy path: session.context.last_assistant_response ──────────────────


@requires_db
@pytest.mark.asyncio
async def test_repeat_returns_session_last_response_verbatim(db_session) -> None:
    """The primary path: prior assistant_text comes from session
    context. Repeated verbatim, no embellishment."""
    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")
    original = "It's 3:47 PM."
    await SessionRepository(db_session).set_context_key(
        sid, "last_assistant_response", original,
    )
    await db_session.commit()

    handler = RepeatHandler()
    ctx = Context(session_id=sid, room_id="kitchen", online=True)
    response = await handler._repeat(ctx, db_session)
    assert response.text == original
    assert response.matched_handler == "repeat"


@requires_db
@pytest.mark.asyncio
async def test_repeat_via_fast_path_match(db_session) -> None:
    """End-to-end fast-path: regex match → _from_match → session lookup
    → verbatim response."""
    sid = await SessionRepository(db_session).get_or_create(None, "garage")
    original = "Set a timer for 5 minutes."
    await SessionRepository(db_session).set_context_key(
        sid, "last_assistant_response", original,
    )
    await db_session.commit()

    handler = RepeatHandler()
    ctx = Context(session_id=sid, room_id="garage", online=True)
    m = _REPEAT_RE.match("say that again")
    assert m
    response = await handler._from_match(m, ctx, db_session)
    assert response.text == original


@requires_db
@pytest.mark.asyncio
async def test_repeat_session_takes_priority_over_room_history(db_session) -> None:
    """When both session context AND conversation_log have content, the
    session path wins — it's the more specific signal."""
    # Seed conversation_log with one row in this room.
    await db_session.execute(
        text(
            """
            INSERT INTO conversation_log (room_id, user_text, assistant_text, at)
            VALUES (:room_id, :user_text, :assistant_text, NOW())
            """
        ),
        {
            "room_id": "kitchen",
            "user_text": "something old",
            "assistant_text": "OLD room-scoped text.",
        },
    )

    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")
    fresh = "FRESH session-scoped text."
    await SessionRepository(db_session).set_context_key(
        sid, "last_assistant_response", fresh,
    )
    await db_session.commit()

    handler = RepeatHandler()
    ctx = Context(session_id=sid, room_id="kitchen", online=True)
    response = await handler._repeat(ctx, db_session)
    assert response.text == fresh


# ─── Fallback: conversation_log scoped by room ────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_repeat_falls_back_to_conversation_log_when_session_empty(
    db_session,
) -> None:
    """Common case: session aged out, new session has no
    last_assistant_response yet, but conversation_log still has the
    last thing said in this room."""
    await db_session.execute(
        text(
            """
            INSERT INTO conversation_log (room_id, user_text, assistant_text, at)
            VALUES (:room_id, :user_text, :assistant_text, NOW())
            """
        ),
        {
            "room_id": "kitchen",
            "user_text": "what time is it",
            "assistant_text": "It's 3:47 PM.",
        },
    )
    await db_session.commit()

    handler = RepeatHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    response = await handler._repeat(ctx, db_session)
    assert response.text == "It's 3:47 PM."


@requires_db
@pytest.mark.asyncio
async def test_repeat_fallback_picks_latest_in_room(db_session) -> None:
    """Multiple rows in the same room — the latest one wins."""
    now = datetime.now(tz=timezone.utc)
    await db_session.execute(
        text(
            """
            INSERT INTO conversation_log (room_id, user_text, assistant_text, at)
            VALUES (:room_id, :user_text, :assistant_text, :at)
            """
        ),
        {
            "room_id": "kitchen",
            "user_text": "first",
            "assistant_text": "Older response.",
            "at": now - timedelta(seconds=300),
        },
    )
    await db_session.execute(
        text(
            """
            INSERT INTO conversation_log (room_id, user_text, assistant_text, at)
            VALUES (:room_id, :user_text, :assistant_text, :at)
            """
        ),
        {
            "room_id": "kitchen",
            "user_text": "second",
            "assistant_text": "Newer response.",
            "at": now - timedelta(seconds=10),
        },
    )
    await db_session.commit()

    handler = RepeatHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    response = await handler._repeat(ctx, db_session)
    assert response.text == "Newer response."


@requires_db
@pytest.mark.asyncio
async def test_repeat_fallback_ignores_other_rooms(db_session) -> None:
    """A repeat in the kitchen must not surface something said in the
    garage. Room scoping is load-bearing."""
    await db_session.execute(
        text(
            """
            INSERT INTO conversation_log (room_id, user_text, assistant_text, at)
            VALUES (:room_id, :user_text, :assistant_text, NOW())
            """
        ),
        {
            "room_id": "garage",
            "user_text": "garage question",
            "assistant_text": "Garage answer.",
        },
    )
    await db_session.commit()

    handler = RepeatHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    response = await handler._repeat(ctx, db_session)
    assert "anything recent" in response.text.lower()


@requires_db
@pytest.mark.asyncio
async def test_repeat_fallback_ignores_rows_outside_time_window(db_session) -> None:
    """An assistant_text from an hour ago is too stale — the user
    probably doesn't mean that. Falls through to the 'nothing recent'
    message."""
    await db_session.execute(
        text(
            """
            INSERT INTO conversation_log (room_id, user_text, assistant_text, at)
            VALUES (:room_id, :user_text, :assistant_text, :at)
            """
        ),
        {
            "room_id": "kitchen",
            "user_text": "ancient question",
            "assistant_text": "Ancient answer.",
            "at": datetime.now(tz=timezone.utc) - timedelta(hours=2),
        },
    )
    await db_session.commit()

    handler = RepeatHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    response = await handler._repeat(ctx, db_session)
    assert "anything recent" in response.text.lower()


@requires_db
@pytest.mark.asyncio
async def test_repeat_fallback_skips_empty_assistant_text(db_session) -> None:
    """conversation_log can hold rows with NULL or empty assistant_text
    (error paths). Those aren't useful to repeat — skip them."""
    await db_session.execute(
        text(
            """
            INSERT INTO conversation_log (room_id, user_text, assistant_text, at)
            VALUES (:room_id, :user_text, NULL, :at)
            """
        ),
        {
            "room_id": "kitchen",
            "user_text": "errored turn",
            "at": datetime.now(tz=timezone.utc) - timedelta(seconds=5),
        },
    )
    await db_session.execute(
        text(
            """
            INSERT INTO conversation_log (room_id, user_text, assistant_text, at)
            VALUES (:room_id, :user_text, :assistant_text, :at)
            """
        ),
        {
            "room_id": "kitchen",
            "user_text": "earlier good turn",
            "assistant_text": "Good response.",
            "at": datetime.now(tz=timezone.utc) - timedelta(seconds=60),
        },
    )
    await db_session.commit()

    handler = RepeatHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    response = await handler._repeat(ctx, db_session)
    assert response.text == "Good response."


# ─── Tool-call entry ──────────────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_execute_from_tool_uses_session_context(db_session) -> None:
    """LLM tool-routing path takes no args — pulls last_assistant_response
    out of session context just like the fast-path."""
    sid = await SessionRepository(db_session).get_or_create(None, "kitchen")
    original = "Reminder set for 7 AM tomorrow."
    await SessionRepository(db_session).set_context_key(
        sid, "last_assistant_response", original,
    )
    await db_session.commit()

    handler = RepeatHandler()
    ctx = Context(session_id=sid, room_id="kitchen", online=True)
    response = await handler.execute_from_tool({}, ctx, db_session)
    assert response.text == original
    assert response.matched_handler == "repeat"


@pytest.mark.asyncio
async def test_execute_with_no_state_responds_gracefully() -> None:
    """The handler's `execute` path (used when the router falls through
    to it without a fast-path match — e.g. LLM picked the handler but
    had no claim/args) should still gracefully bail when there's
    nothing to repeat."""
    handler = RepeatHandler()
    ctx = Context(session_id=None, room_id=None, online=True)
    intent = Intent(transcript="repeat that", room_id=None)
    response = await handler.execute(intent, ctx, None)
    assert "anything recent" in response.text.lower()
