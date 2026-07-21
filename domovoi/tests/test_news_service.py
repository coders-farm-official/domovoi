"""news_service DB tests — retention (favorites exempt), dedup, discovery
attach, and per-person read/status. All DB-gated.

Discovery uses monkeypatched ``news_feeds.discover_feeds`` so no live network
is touched — the SearxNG→probe→validate loop itself is covered in
test_news_feeds.py.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from domovoi import news_service
from domovoi.clients import news_feeds
from domovoi.clients.news_feeds import DiscoveredFeed, FeedArticle
from domovoi.tests.conftest import requires_db


async def _mk_person(session, name: str = "Ada") -> int:
    return int(
        (
            await session.execute(
                text("INSERT INTO people (name) VALUES (:n) RETURNING id"), {"n": name}
            )
        ).scalar_one()
    )


async def _mk_feed(session, url: str) -> int:
    return await news_service._upsert_feed(session, url, discovered_via="manual")


# ─── dedup ──────────────────────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_insert_article_dedups_on_guid(db_session) -> None:
    pid = await _mk_person(db_session)
    fid = await _mk_feed(db_session, "https://f.example/a")
    art = FeedArticle(
        guid="dup-1", url="https://x/1", title="One", source="X",
        summary="s", published_at=datetime.now(timezone.utc),
    )
    first = await news_service._insert_article(
        db_session, art, feed_id=fid, person_id=pid, scope=None, topic_id=None
    )
    second = await news_service._insert_article(
        db_session, art, feed_id=fid, person_id=pid, scope=None, topic_id=None
    )
    await db_session.commit()
    assert first is True
    assert second is False
    n = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM news_items WHERE person_id = :p"), {"p": pid}
        )
    ).scalar_one()
    assert n == 1


@requires_db
@pytest.mark.asyncio
async def test_house_and_person_same_guid_coexist(db_session) -> None:
    """Dedup coalesces NULLs, so a house-scope guid and a person guid don't
    collide — both can land."""
    pid = await _mk_person(db_session)
    fid = await _mk_feed(db_session, "https://f.example/b")
    art = FeedArticle(guid="shared", url="u", title="t", source="s",
                      summary="x", published_at=None)
    a = await news_service._insert_article(
        db_session, art, feed_id=fid, person_id=pid, scope=None, topic_id=None
    )
    b = await news_service._insert_article(
        db_session, art, feed_id=fid, person_id=None, scope="national", topic_id=None
    )
    await db_session.commit()
    assert a and b


# ─── retention ──────────────────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_retention_sweep_exempts_favorites(db_session) -> None:
    pid = await _mk_person(db_session)
    fid = await _mk_feed(db_session, "https://f.example/c")
    # Two old items (200 days), one favorited; one fresh item.
    await db_session.execute(
        text(
            """
            INSERT INTO news_items
                (person_id, feed_id, guid, title, favorited, fetched_at)
            VALUES
                (:p, :f, 'old-plain', 'Old plain', FALSE, now() - interval '200 days'),
                (:p, :f, 'old-fav',   'Old fav',   TRUE,  now() - interval '200 days'),
                (:p, :f, 'fresh',     'Fresh',     FALSE, now())
            """
        ),
        {"p": pid, "f": fid},
    )
    await db_session.commit()

    deleted = await news_service.retention_sweep(db_session, 90)
    await db_session.commit()
    assert deleted == 1  # only the old, non-favorited one

    remaining = {
        r[0]
        for r in (
            await db_session.execute(
                text("SELECT guid FROM news_items WHERE person_id = :p"), {"p": pid}
            )
        ).all()
    }
    assert remaining == {"old-fav", "fresh"}


# ─── discovery attach ───────────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_discover_and_attach_feeds(db_session, monkeypatch) -> None:
    pid = await _mk_person(db_session)
    topic_id = int(
        (
            await db_session.execute(
                text(
                    "INSERT INTO news_topics (person_id, kind, topic) "
                    "VALUES (:p, 'freeform', 'formula 1') RETURNING id"
                ),
                {"p": pid},
            )
        ).scalar_one()
    )

    async def fake_discover(phrase, **kw):
        return [
            DiscoveredFeed(url="https://f1.example/rss", title="F1", source="F1"),
            DiscoveredFeed(url="https://motorsport.example/rss", title="MS", source="MS"),
        ]

    monkeypatch.setattr(news_feeds, "discover_feeds", fake_discover)

    n = await news_service.discover_and_attach_feeds(db_session, topic_id, "formula 1")
    await db_session.commit()
    assert n == 2

    attached = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM topic_feeds WHERE topic_id = :t"), {"t": topic_id}
        )
    ).scalar_one()
    assert attached == 2


@requires_db
@pytest.mark.asyncio
async def test_attach_category_feeds(db_session) -> None:
    pid = await _mk_person(db_session)
    topic_id = int(
        (
            await db_session.execute(
                text(
                    "INSERT INTO news_topics (person_id, kind, topic) "
                    "VALUES (:p, 'category', 'tech') RETURNING id"
                ),
                {"p": pid},
            )
        ).scalar_one()
    )
    n = await news_service.attach_category_feeds(db_session, topic_id, "tech")
    await db_session.commit()
    assert n >= 1
    attached = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM topic_feeds WHERE topic_id = :t"), {"t": topic_id}
        )
    ).scalar_one()
    assert attached == n


# ─── status / read ──────────────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_unread_status_and_read_marks(db_session) -> None:
    pid = await _mk_person(db_session)
    fid = await _mk_feed(db_session, "https://f.example/d")
    await db_session.execute(
        text(
            """
            INSERT INTO news_items (person_id, feed_id, guid, title, published_at)
            VALUES
                (:p, :f, 'a', 'Oldest', now() - interval '3 days'),
                (:p, :f, 'b', 'Newest', now())
            """
        ),
        {"p": pid, "f": fid},
    )
    await db_session.commit()

    status = await news_service.unread_status(db_session, pid)
    assert status["unread"] == 2
    assert status["oldest"]["title"] == "Oldest"
    assert status["newest"]["title"] == "Newest"

    items = await news_service.read_person_items(
        db_session, pid, limit=5, order="newest", unread_only=True, mark_read=True
    )
    await db_session.commit()
    assert [it["title"] for it in items] == ["Newest", "Oldest"]

    status2 = await news_service.unread_status(db_session, pid)
    assert status2["unread"] == 0
