from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from domovoi.clients.mpd import MPDStubClient
from domovoi.db.repositories import utcnow
from domovoi.handlers.library import (
    LibraryHandler,
    _ADDED_WHEN_RE,
    _COUNT_RE,
    _FIND_RE,
    _HAVE_RE,
)
from domovoi.models import Context
from domovoi.tests.conftest import requires_db


# ─── Regex tests ───────────────────────────────────────────────────────────

def test_find_regex() -> None:
    m = _FIND_RE.match("find creep in my library")
    assert m and m.group(1) == "creep"


def test_have_regex() -> None:
    m = _HAVE_RE.match("do i have ok computer")
    assert m and m.group(1) == "ok computer"
    m2 = _HAVE_RE.match("do i have creep in my library")
    assert m2 and m2.group(1) == "creep"


def test_added_when_regex() -> None:
    assert _ADDED_WHEN_RE.match("what did i add today")
    assert _ADDED_WHEN_RE.match("what did i add this week")
    assert _ADDED_WHEN_RE.match("what did i add recently")


def test_count_regex() -> None:
    assert _COUNT_RE.match("how many songs do i have")
    assert _COUNT_RE.match("library count")


# ─── MPD-backed search ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_returns_stub_match() -> None:
    from domovoi.clients import mpd as mpd_module

    mpd_module._clients = {"kitchen": MPDStubClient()}

    handler = LibraryHandler()
    ctx = Context(session_id=uuid4(), online=True)
    m = _FIND_RE.match("find creep in my library")
    assert m
    response = await handler._find_from_match(m, ctx, None)
    assert "yes" in response.text.lower()
    assert "creep" in response.text.lower()


@pytest.mark.asyncio
async def test_have_when_stub_finds_it() -> None:
    from domovoi.clients import mpd as mpd_module

    mpd_module._clients = {"kitchen": MPDStubClient()}

    handler = LibraryHandler()
    ctx = Context(session_id=None, online=True)
    m = _HAVE_RE.match("do i have paranoid android")
    assert m
    response = await handler._have_from_match(m, ctx, None)
    assert "yes" in response.text.lower()


# ─── DB-backed added-recently + count ──────────────────────────────────────

@requires_db
@pytest.mark.asyncio
async def test_added_recently_window(db_session) -> None:
    now = utcnow()
    await db_session.execute(
        text(
            """
            INSERT INTO library_tracks (file_path, title, artist, added_at)
            VALUES
                ('/music/a.mp3', 'A', 'Alpha', :recent),
                ('/music/b.mp3', 'B', 'Beta', :old)
            """
        ),
        {
            "recent": now - timedelta(hours=2),
            "old": now - timedelta(days=90),
        },
    )
    await db_session.commit()

    handler = LibraryHandler()
    ctx = Context(session_id=None, online=True)
    m = _ADDED_WHEN_RE.match("what did i add today")
    assert m
    response = await handler._added_from_match(m, ctx, db_session)
    assert "A by Alpha" in response.text
    assert "B by Beta" not in response.text


@requires_db
@pytest.mark.asyncio
async def test_count_with_rows(db_session) -> None:
    await db_session.execute(
        text(
            """
            INSERT INTO library_tracks (file_path, title) VALUES
                ('/music/x.mp3', 'X'),
                ('/music/y.mp3', 'Y'),
                ('/music/z.mp3', 'Z')
            """
        )
    )
    await db_session.commit()

    handler = LibraryHandler()
    ctx = Context(session_id=None, online=True)
    m = _COUNT_RE.match("how many songs do i have")
    assert m
    response = await handler._count_from_match(m, ctx, db_session)
    assert "3 track" in response.text


@requires_db
@pytest.mark.asyncio
async def test_count_empty(db_session) -> None:
    handler = LibraryHandler()
    ctx = Context(session_id=None, online=True)
    m = _COUNT_RE.match("library count")
    assert m
    response = await handler._count_from_match(m, ctx, db_session)
    assert "0 track" in response.text
