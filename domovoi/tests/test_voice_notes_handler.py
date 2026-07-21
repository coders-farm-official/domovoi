from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import text

from domovoi.db.repositories import VoiceNotesRepository, utcnow
from domovoi.handlers.voice_notes import (
    VoiceNotesHandler,
    _ADD_RE,
    _READ_LATEST_RE,
    _READ_WINDOW_RE,
    _strip_leading_filler,
)
from domovoi.models import Context
from domovoi.tests.conftest import requires_db


# ─── Regex tests ────────────────────────────────────────────────────────────

def test_add_regex_jot_down() -> None:
    m = _ADD_RE.match("jot down replace the air filter")
    assert m and m.group("body") == "replace the air filter"


def test_add_regex_jot_with_colon() -> None:
    m = _ADD_RE.match("jot down: replace the air filter")
    assert m and m.group("body") == "replace the air filter"


def test_add_regex_alternate_verbs() -> None:
    for s in (
        "write down call the plumber",
        "save a note: pizza is here",
        "note that the printer is out of toner",
    ):
        assert _ADD_RE.match(s), f"expected match: {s!r}"


def test_read_window_regex() -> None:
    for window in ("today", "yesterday", "this week", "recently"):
        m = _READ_WINDOW_RE.match(f"what did i jot down {window}")
        assert m and m.group("window") == window


def test_read_window_regex_no_window() -> None:
    """The window is optional — bare 'what did i jot down' defaults to recently."""
    m = _READ_WINDOW_RE.match("what did i jot down")
    assert m and m.group("window") is None


def test_read_latest_regex() -> None:
    for s in ("what was my last note", "read me my last note", "read my most recent note"):
        assert _READ_LATEST_RE.match(s), f"expected match: {s!r}"


def test_strip_leading_filler() -> None:
    assert _strip_leading_filler("that pizza is here") == "pizza is here"
    assert _strip_leading_filler("down replace the filter") == "replace the filter"
    assert _strip_leading_filler("pizza is here.") == "pizza is here"
    assert _strip_leading_filler("  this is fine  ") == "is fine"


# ─── Behavior ───────────────────────────────────────────────────────────────

@requires_db
@pytest.mark.asyncio
async def test_add_inserts_row(db_session) -> None:
    handler = VoiceNotesHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    m = _ADD_RE.match("jot down: replace the air filter")
    assert m
    response = await handler._add_from_match(m, ctx, db_session)
    await db_session.commit()

    assert response.text.lower().startswith("got it")
    rows = (
        await db_session.execute(text("SELECT room_id, text FROM voice_notes"))
    ).all()
    assert len(rows) == 1
    assert rows[0][0] == "kitchen"
    assert rows[0][1] == "replace the air filter"


@requires_db
@pytest.mark.asyncio
async def test_read_window_empty(db_session) -> None:
    handler = VoiceNotesHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    m = _READ_WINDOW_RE.match("what did i jot down today")
    assert m
    response = await handler._window_from_match(m, ctx, db_session)
    assert "no notes" in response.text.lower()


@requires_db
@pytest.mark.asyncio
async def test_read_window_returns_recent_notes(db_session) -> None:
    repo = VoiceNotesRepository(db_session)
    await repo.add(room_id="kitchen", body="pizza is here")
    await repo.add(room_id="kitchen", body="replace filter")
    await db_session.commit()

    handler = VoiceNotesHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    m = _READ_WINDOW_RE.match("what did i jot down today")
    assert m
    response = await handler._window_from_match(m, ctx, db_session)
    # Most recent first.
    assert "replace filter" in response.text
    assert "pizza is here" in response.text


@requires_db
@pytest.mark.asyncio
async def test_read_window_excludes_old_notes(db_session) -> None:
    """A note from 30+ days ago shouldn't show up under 'today'."""
    await db_session.execute(
        text(
            """
            INSERT INTO voice_notes (room_id, text, created_at)
            VALUES ('kitchen', 'old note', :old)
            """
        ),
        {"old": utcnow() - timedelta(days=10)},
    )
    await db_session.commit()

    handler = VoiceNotesHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    m = _READ_WINDOW_RE.match("what did i jot down today")
    assert m
    response = await handler._window_from_match(m, ctx, db_session)
    assert "no notes" in response.text.lower()


@requires_db
@pytest.mark.asyncio
async def test_read_latest_returns_most_recent(db_session) -> None:
    repo = VoiceNotesRepository(db_session)
    await repo.add(room_id="kitchen", body="first note")
    await repo.add(room_id="garage", body="second note")
    await db_session.commit()

    handler = VoiceNotesHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    m = _READ_LATEST_RE.match("what was my last note")
    assert m
    response = await handler._latest_from_match(m, ctx, db_session)
    assert "second note" in response.text


@requires_db
@pytest.mark.asyncio
async def test_read_latest_when_empty(db_session) -> None:
    handler = VoiceNotesHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    m = _READ_LATEST_RE.match("what was my last note")
    assert m
    response = await handler._latest_from_match(m, ctx, db_session)
    assert "haven't taken any notes" in response.text.lower()
