from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from domovoi.clients.mpd import MPDNotProvisioned, MPDStubClient
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


# ─── F-V007: metadata lookups must not need the room's MPD ─────────────────
#
# "find X in my library" / "do i have X" went straight at the room's MPD
# daemon. A room whose satellite has never connected has no daemon, so
# get_mpd_client_for raised MPDNotProvisioned, the router caught it and
# answered with the *playback* wording ("no satellite has connected, so
# there are no speakers set up"). These drive the real handler path with
# the client raising, against a fake session, so they stay DB-free.


class _FakeResult:
    def __init__(self, row) -> None:
        self._row = row

    def first(self):
        return self._row


class _FakeSession:
    """Minimal async stand-in for AsyncSession: records the params it was
    handed and returns one canned library_tracks row."""

    def __init__(self, row=None) -> None:
        self.row = row
        self.params: list = []

    async def execute(self, _stmt, params=None):
        self.params.append(params)
        return _FakeResult(self.row)


def _no_mpd(monkeypatch) -> None:
    def raise_unprovisioned(_room_id=None):
        raise MPDNotProvisioned("no rooms provisioned yet")

    monkeypatch.setattr(
        "domovoi.handlers.library.get_mpd_client_for", raise_unprovisioned
    )


@pytest.mark.asyncio
async def test_have_without_mpd_answers_from_library_tracks(monkeypatch) -> None:
    _no_mpd(monkeypatch)
    session = _FakeSession(("Purple Rain", "Prince", "Purple Rain", "/music/pr.mp3"))

    handler = LibraryHandler()
    ctx = Context(session_id=None, room_id="vt-bench", online=True)
    m = _HAVE_RE.match("do i have purple rain")
    assert m
    response = await handler._have_from_match(m, ctx, session)

    assert "purple rain" in response.text.lower()
    # The playback apology must never reach a metadata question.
    assert "satellite" not in response.text.lower()
    assert "speakers" not in response.text.lower()


@pytest.mark.asyncio
async def test_find_without_mpd_answers_from_library_tracks(monkeypatch) -> None:
    _no_mpd(monkeypatch)
    session = _FakeSession(("Ember Waltz", "Nightjar", None, "/music/ew.mp3"))

    handler = LibraryHandler()
    ctx = Context(session_id=None, room_id="vt-bench", online=True)
    m = _FIND_RE.match("find ember waltz in my library")
    assert m
    response = await handler._find_from_match(m, ctx, session)

    assert "ember waltz" in response.text.lower()
    assert "nightjar" in response.text.lower()
    assert "satellite" not in response.text.lower()


@pytest.mark.asyncio
async def test_miss_without_mpd_says_not_in_library(monkeypatch) -> None:
    """A genuine miss reads as a library answer, not a speaker problem."""
    _no_mpd(monkeypatch)
    session = _FakeSession(None)

    handler = LibraryHandler()
    ctx = Context(session_id=None, room_id="vt-bench", online=True)
    m = _HAVE_RE.match("do i have tuba concerto")
    assert m
    response = await handler._have_from_match(m, ctx, session)

    assert response.text.lower().startswith("no")
    assert "satellite" not in response.text.lower()


@pytest.mark.asyncio
async def test_db_fallback_escapes_like_wildcards(monkeypatch) -> None:
    """Underscores/percents in a title are literals, not wildcards."""
    _no_mpd(monkeypatch)
    session = _FakeSession(None)

    handler = LibraryHandler()
    ctx = Context(session_id=None, room_id="vt-bench", online=True)
    await handler._have("100%_pure", ctx, session)

    assert session.params, "the fallback never queried library_tracks"
    assert session.params[0]["q"] == r"%100\%\_pure%"


@pytest.mark.asyncio
async def test_db_fallback_tolerates_missing_session(monkeypatch) -> None:
    """Callers without a session (tool-free helpers) still get an answer."""
    _no_mpd(monkeypatch)

    handler = LibraryHandler()
    ctx = Context(session_id=None, room_id="vt-bench", online=True)
    m = _HAVE_RE.match("do i have creep")
    assert m
    response = await handler._have_from_match(m, ctx, None)

    assert response.text.lower().startswith("no")


@requires_db
@pytest.mark.asyncio
async def test_db_fallback_sql_runs_on_postgres(monkeypatch, db_session) -> None:
    """The fake-session tests can't catch a SQL syntax error (the fallback
    logs and returns [] on any exception, which would silently read as
    "no, I don't have it"). This one runs the real statement."""
    _no_mpd(monkeypatch)
    await db_session.execute(
        text(
            """
            INSERT INTO library_tracks (file_path, title, artist)
            VALUES ('/music/ember.mp3', 'Ember Waltz', 'Nightjar')
            """
        )
    )
    await db_session.commit()

    handler = LibraryHandler()
    ctx = Context(session_id=None, room_id="vt-bench", online=True)
    m = _FIND_RE.match("find ember waltz in my library")
    assert m
    response = await handler._find_from_match(m, ctx, db_session)

    assert "ember waltz" in response.text.lower()
    assert "satellite" not in response.text.lower()
