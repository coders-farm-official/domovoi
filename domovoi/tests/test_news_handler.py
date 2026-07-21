"""NewsHandler voice-surface tests.

Non-DB: fast-path regex behavior + registry ordering vs the greedy media
handlers + read-vs-fetch verb classification.

DB-gated (@requires_db): routing through the real router — read verb reads
cached with no confirm, fetch verb parks a single pending_confirmation,
fallback_offline degrades, count edit, favorite-that.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from domovoi.config import settings
from domovoi.handlers import (
    HANDLERS,
    HANDLER_BY_NAME,
    LibraryHandler,
    MusicHandler,
    NewsHandler,
    SpokenAudioHandler,
)
from domovoi.handlers.news import (
    _BRIEFING_RE,
    _COUNT_EDIT_RE,
    _FAVORITE_RE,
    _FETCH_NEW_RE,
    _MY_NEWS_RE,
    _READ_LATEST_RE,
    _READ_OLDEST_RE,
    _SUBJECT_A_RE,
    _SUBJECT_B_RE,
    _is_fetch_verb,
)
from domovoi.models import Context, Intent
from domovoi.router import route
from domovoi.tests.conftest import requires_db


# ─── Registry / ordering ────────────────────────────────────────────────


def test_news_handler_registered() -> None:
    assert HANDLER_BY_NAME.get("news") is not None
    assert any(isinstance(h, NewsHandler) for h in HANDLERS)


def test_news_before_greedy_media_handlers() -> None:
    news = HANDLER_BY_NAME["news"]
    for greedy in (SpokenAudioHandler, MusicHandler, LibraryHandler):
        g = next(h for h in HANDLERS if isinstance(h, greedy))
        assert news.priority_band < g.priority_band, (
            f"news must dispatch before {greedy.__name__}"
        )
    # ... and well before the greedy-catch-all plugin band (900+).
    assert news.priority_band < 900


def test_news_declares_degraded_with_fallback() -> None:
    h = HANDLER_BY_NAME["news"]
    assert h.requires_network == "degraded"
    assert type(h).fallback_offline is not type(h).__mro__[1].fallback_offline


# ─── Fast-path regex ────────────────────────────────────────────────────


@pytest.mark.parametrize("q", [
    "what's the news", "what is the news today", "give me the news",
    "read me the latest news", "catch me up on the news",
])
def test_briefing_re(q) -> None:
    assert _BRIEFING_RE.match(q) is not None, q


@pytest.mark.parametrize("q", ["what's my news", "my news", "whats my news"])
def test_my_news_re(q) -> None:
    assert _MY_NEWS_RE.match(q) is not None, q


def test_read_latest_oldest_re() -> None:
    assert _READ_LATEST_RE.match("read the latest")
    assert _READ_LATEST_RE.match("read me the latest stories")
    assert _READ_OLDEST_RE.match("read the oldest")
    assert _READ_OLDEST_RE.match("read me the oldest news")


def test_fetch_new_re() -> None:
    assert _FETCH_NEW_RE.match("fetch new")
    assert _FETCH_NEW_RE.match("get me the new ones")
    assert _FETCH_NEW_RE.match("grab the new stories")


def test_subject_a_read_and_fetch_verbs() -> None:
    m = _SUBJECT_A_RE.match("give me the latest news for lansing")
    assert m and m.group("subject") == "lansing"
    assert not _is_fetch_verb(m.group("verb"))  # give me → read

    m = _SUBJECT_A_RE.match("fetch me the latest news for lansing")
    assert m and m.group("subject") == "lansing"
    assert _is_fetch_verb(m.group("verb"))  # fetch → fetch


def test_subject_a_count_capture() -> None:
    m = _SUBJECT_A_RE.match("read me 5 stories about tech")
    assert m and m.group("count") == "5" and m.group("subject") == "tech"


def test_subject_b_latest_on() -> None:
    m = _SUBJECT_B_RE.match("the latest on the election")
    assert m and m.group("subject") == "the election"
    m = _SUBJECT_B_RE.match("fetch the latest on ai")
    assert m and _is_fetch_verb(m.group("verb"))


def test_subject_re_does_not_poach_play() -> None:
    # Music's "play X" catch-all must not be poached.
    assert _SUBJECT_A_RE.match("play stories from the crypt") is None
    assert _SUBJECT_B_RE.match("play the latest song") is None


def test_count_edit_re() -> None:
    m = _COUNT_EDIT_RE.match("give me 5 stories")
    assert m and m.group("count") == "5"
    assert _COUNT_EDIT_RE.match("set my news to 4 items")
    # A subject-bearing phrase is NOT a count edit.
    assert _COUNT_EDIT_RE.match("give me 5 stories about tech") is None


def test_favorite_re() -> None:
    for q in ("favorite that", "save that", "bookmark this", "star that story"):
        assert _FAVORITE_RE.match(q) is not None, q
    # Never claim a bare stop/next (those belong to music).
    assert _FAVORITE_RE.match("stop") is None


# ─── DB-gated routing ───────────────────────────────────────────────────


async def _mk_person(session, name: str = "Sam") -> int:
    row = await session.execute(
        text("INSERT INTO people (name) VALUES (:n) RETURNING id"), {"n": name}
    )
    return int(row.scalar_one())


async def _seed_person_item(session, person_id: int, *, title: str, guid: str) -> int:
    # A per-person item needs a feed_id; create a throwaway feed.
    fid = int(
        (
            await session.execute(
                text(
                    "INSERT INTO news_feeds (url, discovered_via) "
                    "VALUES (:u, 'manual') RETURNING id"
                ),
                {"u": f"https://feed.example/{guid}"},
            )
        ).scalar_one()
    )
    row = await session.execute(
        text(
            """
            INSERT INTO news_items (person_id, feed_id, guid, title, source)
            VALUES (:pid, :fid, :guid, :title, 'Example')
            RETURNING id
            """
        ),
        {"pid": person_id, "fid": fid, "guid": guid, "title": title},
    )
    return int(row.scalar_one())


@requires_db
@pytest.mark.asyncio
async def test_read_subject_no_confirm(db_session) -> None:
    pid = await _mk_person(db_session)
    await _seed_person_item(db_session, pid, title="Lansing council votes", guid="g1")
    await db_session.commit()

    intent = Intent(transcript="give me the latest news about lansing", room_id="kitchen")
    ctx = Context(room_id="kitchen", online=True, person_id=pid)
    resp = await route(intent, ctx, db_session)
    await db_session.commit()

    assert resp.matched_handler == "news"
    assert not resp.expect_followup  # read verb reads immediately
    assert "lansing" in resp.text.lower()

    # No pending_confirmation parked.
    ctx_row = await db_session.execute(
        text("SELECT context FROM sessions WHERE id = :sid"),
        {"sid": resp.session_id},
    )
    context = ctx_row.scalar() or {}
    assert not (context or {}).get("pending_confirmation")


@requires_db
@pytest.mark.asyncio
async def test_fetch_subject_parks_single_confirm(db_session) -> None:
    pid = await _mk_person(db_session)
    await db_session.commit()

    intent = Intent(transcript="fetch me the latest news about mars", room_id="kitchen")
    ctx = Context(room_id="kitchen", online=True, person_id=pid)
    resp = await route(intent, ctx, db_session)
    await db_session.commit()

    assert resp.matched_handler == "news"
    assert resp.expect_followup  # awaiting the single internet confirm
    ctx_row = await db_session.execute(
        text("SELECT context FROM sessions WHERE id = :sid"),
        {"sid": resp.session_id},
    )
    pending = (ctx_row.scalar() or {}).get("pending_confirmation")
    assert pending and pending["handler"] == "news"
    assert pending["kind"] == "core.news_fetch"
    assert pending["subject"] == "mars"


@requires_db
@pytest.mark.asyncio
async def test_fetch_subject_offline_degrades(db_session) -> None:
    pid = await _mk_person(db_session)
    await db_session.commit()

    intent = Intent(transcript="fetch me the latest news about mars", room_id="kitchen")
    ctx = Context(room_id="kitchen", online=False, person_id=pid)
    resp = await route(intent, ctx, db_session)
    await db_session.commit()

    assert resp.matched_handler == "news"
    assert not resp.expect_followup
    assert "internet" in resp.text.lower()


@requires_db
@pytest.mark.asyncio
async def test_count_edit_updates_setting(db_session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "news_items_per_ask", 3)
    intent = Intent(transcript="give me 5 stories", room_id="kitchen")
    ctx = Context(room_id="kitchen", online=True)
    resp = await route(intent, ctx, db_session)
    await db_session.commit()
    assert resp.matched_handler == "news"
    assert settings.news_items_per_ask == 5


@requires_db
@pytest.mark.asyncio
async def test_favorite_that_favorites_last_read(db_session) -> None:
    pid = await _mk_person(db_session)
    item_id = await _seed_person_item(db_session, pid, title="Tech breakthrough", guid="t1")
    await db_session.commit()

    ctx = Context(room_id="kitchen", online=True, person_id=pid)
    # Read something so news_last_item_id gets stamped. Thread the session_id
    # forward the way the streaming layer does across turns — a bare None-session
    # ctx spawns a fresh session on each route() call, losing the stashed item.
    r1 = await route(Intent(transcript="read me the latest", room_id="kitchen"), ctx, db_session)
    await db_session.commit()
    ctx = ctx.model_copy(update={"session_id": r1.session_id})

    resp = await route(Intent(transcript="favorite that", room_id="kitchen"), ctx, db_session)
    await db_session.commit()
    assert resp.matched_handler == "news"

    fav = await db_session.execute(
        text("SELECT favorited FROM news_items WHERE id = :id"), {"id": item_id}
    )
    assert fav.scalar() is True
