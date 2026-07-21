"""News API — per-person topics of interest, their feeds, saved items, and
the current briefing.

Organized BY PERSON to answer "whose topics are whose": topics belong to a
``people`` row (cascade on delete), each topic resolves to one or more
``news_feeds`` (default for categories, discovered/manual for free-form), and
each person has a saved feed of ``news_items`` plus one ``news_briefings``
digest.

Read endpoints hit Postgres directly. Mutations that need the network (feed
discovery on free-form topic add, "poll now") run the shared
:pymod:`domovoi.news_service` in-process — the web backend imports the
domovoi package, so this is fine for a user-driven button.

Every mutation issues ``pg_notify('news_changed', ...)`` so the dashboard
refreshes sub-second via the LISTEN/NOTIFY accelerator (see
``realtime.py``'s ``news_changed`` → ``news`` mapping).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from domovoi import news_service
from domovoi.clients import news_feeds as news_feeds_client
from domovoi.clients.news_feeds import CATEGORY_KEYS
from domovoi.config import settings as core_settings
from web.backend.db import session_scope

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/news", tags=["news"])


# ─── Schemas ─────────────────────────────────────────────────────────────


class NewsTopic(BaseModel):
    id: int
    person_id: int
    kind: str
    topic: str
    feed_count: int = 0


class NewsTopicCreate(BaseModel):
    kind: str = Field(..., description="'category' or 'freeform'")
    topic: str = Field(..., min_length=1, max_length=200)


class NewsFeed(BaseModel):
    id: int
    url: str
    title: str | None = None
    source: str | None = None
    discovered_via: str
    scope: str | None = None
    valid: bool
    last_polled_at: Any = None
    last_error: str | None = None


class NewsFeedCreate(BaseModel):
    url: str = Field(..., min_length=4, max_length=2000)


class NewsItem(BaseModel):
    id: int
    title: str | None = None
    summary: str | None = None
    source: str | None = None
    url: str | None = None
    topic: str | None = None
    published_at: Any = None
    fetched_at: Any = None
    favorited: bool = False
    read_at: Any = None


class NewsItemFavorite(BaseModel):
    favorited: bool = True


class NewsBriefing(BaseModel):
    briefing: str | None = None
    generated_at: Any = None


# ─── Categories (static) ─────────────────────────────────────────────────


@router.get("/categories", response_model=list[str])
async def list_categories() -> list[str]:
    """The fixed curatable category set the UI offers as chips."""
    return list(CATEGORY_KEYS)


# ─── Topics ──────────────────────────────────────────────────────────────


@router.get("/people/{person_id}/topics", response_model=list[NewsTopic])
async def list_topics(person_id: int) -> list[NewsTopic]:
    async with session_scope() as s:
        await _ensure_person(s, person_id)
        rows = await s.execute(
            text(
                """
                SELECT nt.id, nt.person_id, nt.kind, nt.topic,
                       COUNT(tf.feed_id) AS feed_count
                  FROM news_topics nt
                  LEFT JOIN topic_feeds tf ON tf.topic_id = nt.id
                 WHERE nt.person_id = :pid
                 GROUP BY nt.id
                 ORDER BY nt.kind, LOWER(nt.topic)
                """
            ),
            {"pid": person_id},
        )
        return [
            NewsTopic(
                id=int(r[0]), person_id=int(r[1]), kind=r[2], topic=r[3],
                feed_count=int(r[4]),
            )
            for r in rows.all()
        ]


@router.post("/people/{person_id}/topics", response_model=NewsTopic, status_code=201)
async def add_topic(person_id: int, payload: NewsTopicCreate) -> NewsTopic:
    kind = (payload.kind or "").strip().lower()
    topic = (payload.topic or "").strip()
    if kind not in ("category", "freeform"):
        raise HTTPException(status_code=400, detail="kind must be category or freeform")
    if not topic:
        raise HTTPException(status_code=400, detail="topic is required")
    if kind == "category":
        topic = topic.lower()
        if topic not in CATEGORY_KEYS:
            raise HTTPException(
                status_code=400,
                detail=f"unknown category {topic!r}; choose one of {list(CATEGORY_KEYS)}",
            )

    async with session_scope() as s:
        await _ensure_person(s, person_id)
        row = await s.execute(
            text(
                """
                INSERT INTO news_topics (person_id, kind, topic)
                VALUES (:pid, :kind, :topic)
                ON CONFLICT (person_id, kind, topic) DO NOTHING
                RETURNING id
                """
            ),
            {"pid": person_id, "kind": kind, "topic": topic},
        )
        result = row.first()
        if result is None:
            # Already existed — return the existing row.
            existing = await s.execute(
                text(
                    "SELECT id FROM news_topics "
                    "WHERE person_id=:pid AND kind=:kind AND topic=:topic"
                ),
                {"pid": person_id, "kind": kind, "topic": topic},
            )
            topic_id = int(existing.scalar_one())
        else:
            topic_id = int(result[0])
            # Resolve feeds. Categories attach curated defaults instantly;
            # free-form kicks off discovery (network, best-effort).
            try:
                if kind == "category":
                    await news_service.attach_category_feeds(s, topic_id, topic)
                else:
                    await news_service.discover_and_attach_feeds(s, topic_id, topic)
            except Exception as e:  # noqa: BLE001
                log.warning("news: feed resolution failed for topic %s: %s", topic_id, e)
        feed_count = (
            await s.execute(
                text("SELECT COUNT(*) FROM topic_feeds WHERE topic_id = :tid"),
                {"tid": topic_id},
            )
        ).scalar_one()
        await s.execute(text("SELECT pg_notify('news_changed', 'topic_added')"))
    return NewsTopic(
        id=topic_id, person_id=person_id, kind=kind, topic=topic,
        feed_count=int(feed_count),
    )


@router.delete("/topics/{topic_id}", status_code=204)
async def delete_topic(topic_id: int) -> None:
    async with session_scope() as s:
        result = await s.execute(
            text("DELETE FROM news_topics WHERE id = :id"), {"id": topic_id}
        )
        if (result.rowcount or 0) == 0:
            raise HTTPException(status_code=404, detail=f"topic {topic_id} not found")
        await s.execute(text("SELECT pg_notify('news_changed', 'topic_removed')"))


# ─── Feeds ───────────────────────────────────────────────────────────────


@router.get("/topics/{topic_id}/feeds", response_model=list[NewsFeed])
async def list_topic_feeds(topic_id: int) -> list[NewsFeed]:
    async with session_scope() as s:
        rows = await s.execute(
            text(
                """
                SELECT nf.id, nf.url, nf.title, nf.source, nf.discovered_via,
                       nf.scope, nf.valid, nf.last_polled_at, nf.last_error
                  FROM news_feeds nf
                  JOIN topic_feeds tf ON tf.feed_id = nf.id
                 WHERE tf.topic_id = :tid
                 ORDER BY nf.title NULLS LAST, nf.url
                """
            ),
            {"tid": topic_id},
        )
        return [_row_to_feed(r) for r in rows.all()]


@router.post("/topics/{topic_id}/feeds", response_model=NewsFeed, status_code=201)
async def add_topic_feed(topic_id: int, payload: NewsFeedCreate) -> NewsFeed:
    url = (payload.url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="url must be http(s)")
    # Validate by parsing before we attach so a bad URL surfaces immediately.
    valid = await news_feeds_client.feed_is_valid(url)
    async with session_scope() as s:
        topic = await s.execute(
            text("SELECT 1 FROM news_topics WHERE id = :id"), {"id": topic_id}
        )
        if topic.first() is None:
            raise HTTPException(status_code=404, detail=f"topic {topic_id} not found")
        fid = await news_service._upsert_feed(s, url, discovered_via="manual")
        await s.execute(
            text(
                "UPDATE news_feeds SET valid = :v, last_error = :e WHERE id = :id"
            ),
            {"id": fid, "v": valid, "e": None if valid else "no articles on add"},
        )
        await news_service._attach_topic_feed(s, topic_id, fid)
        row = await s.execute(
            text(
                """
                SELECT id, url, title, source, discovered_via, scope, valid,
                       last_polled_at, last_error
                  FROM news_feeds WHERE id = :id
                """
            ),
            {"id": fid},
        )
        feed = _row_to_feed(row.first())
        await s.execute(text("SELECT pg_notify('news_changed', 'feed_added')"))
    return feed


@router.delete("/topics/{topic_id}/feeds/{feed_id}", status_code=204)
async def detach_feed(topic_id: int, feed_id: int) -> None:
    async with session_scope() as s:
        result = await s.execute(
            text(
                "DELETE FROM topic_feeds WHERE topic_id = :tid AND feed_id = :fid"
            ),
            {"tid": topic_id, "fid": feed_id},
        )
        if (result.rowcount or 0) == 0:
            raise HTTPException(
                status_code=404, detail=f"feed {feed_id} not on topic {topic_id}"
            )
        await s.execute(text("SELECT pg_notify('news_changed', 'feed_removed')"))


@router.post("/feeds/{feed_id}/validate", response_model=NewsFeed)
async def validate_feed(feed_id: int) -> NewsFeed:
    """Re-check a feed's validity by parsing it now; update the validity dot."""
    async with session_scope() as s:
        row = await s.execute(
            text("SELECT url FROM news_feeds WHERE id = :id"), {"id": feed_id}
        )
        r = row.first()
        if r is None:
            raise HTTPException(status_code=404, detail=f"feed {feed_id} not found")
        url = r[0]
    valid = await news_feeds_client.feed_is_valid(url)
    async with session_scope() as s:
        await s.execute(
            text(
                "UPDATE news_feeds SET valid = :v, last_error = :e, "
                "last_polled_at = now() WHERE id = :id"
            ),
            {"id": feed_id, "v": valid, "e": None if valid else "validation: no articles"},
        )
        out = await s.execute(
            text(
                """
                SELECT id, url, title, source, discovered_via, scope, valid,
                       last_polled_at, last_error
                  FROM news_feeds WHERE id = :id
                """
            ),
            {"id": feed_id},
        )
        feed = _row_to_feed(out.first())
        await s.execute(text("SELECT pg_notify('news_changed', 'feed_validated')"))
    return feed


# ─── Items (saved feed) ──────────────────────────────────────────────────


@router.get("/people/{person_id}/items", response_model=list[NewsItem])
async def list_items(
    person_id: int, limit: int = Query(default=50, ge=1, le=200)
) -> list[NewsItem]:
    """A person's saved feed — newest first, favorited pinned to the top."""
    async with session_scope() as s:
        await _ensure_person(s, person_id)
        rows = await s.execute(
            text(
                """
                SELECT ni.id, ni.title, ni.summary, ni.source, ni.url,
                       nt.topic, ni.published_at, ni.fetched_at,
                       ni.favorited, ni.read_at
                  FROM news_items ni
                  LEFT JOIN news_topics nt ON nt.id = ni.topic_id
                 WHERE ni.person_id = :pid
                 ORDER BY ni.favorited DESC,
                          ni.published_at DESC NULLS LAST, ni.id DESC
                 LIMIT :limit
                """
            ),
            {"pid": person_id, "limit": limit},
        )
        return [_row_to_item(r) for r in rows.all()]


@router.post("/items/{item_id}/favorite", response_model=NewsItem)
async def favorite_item(item_id: int, payload: NewsItemFavorite) -> NewsItem:
    async with session_scope() as s:
        ok = await news_service.favorite_item(s, item_id, payload.favorited)
        if not ok:
            raise HTTPException(status_code=404, detail=f"item {item_id} not found")
        row = await s.execute(
            text(
                """
                SELECT ni.id, ni.title, ni.summary, ni.source, ni.url,
                       nt.topic, ni.published_at, ni.fetched_at,
                       ni.favorited, ni.read_at
                  FROM news_items ni
                  LEFT JOIN news_topics nt ON nt.id = ni.topic_id
                 WHERE ni.id = :id
                """
            ),
            {"id": item_id},
        )
        item = _row_to_item(row.first())
        await s.execute(text("SELECT pg_notify('news_changed', 'favorited')"))
    return item


# ─── Briefing ────────────────────────────────────────────────────────────


@router.get("/people/{person_id}/briefing", response_model=NewsBriefing)
async def get_briefing(person_id: int) -> NewsBriefing:
    async with session_scope() as s:
        await _ensure_person(s, person_id)
        row = await s.execute(
            text(
                "SELECT briefing, generated_at FROM news_briefings WHERE person_id = :pid"
            ),
            {"pid": person_id},
        )
        r = row.first()
    if r is None:
        return NewsBriefing(briefing=None, generated_at=None)
    return NewsBriefing(briefing=r[0], generated_at=r[1])


# ─── Poll now ────────────────────────────────────────────────────────────


@router.post("/poll")
async def poll_now(person_id: int | None = Query(default=None)) -> dict[str, Any]:
    """Trigger a fetch pass immediately (the 'poll now' button). Fetches the
    house scopes always; also this person's topics + briefing when
    ``person_id`` is given. Network — best-effort; runs in the web process."""
    house = topics = 0
    briefing = None
    async with session_scope() as s:
        await news_service.ensure_house_feeds(s)
        scopes = ["national", "global"]
        if core_settings.news_location.strip():
            scopes.insert(0, "local")
        for scope in scopes:
            house += await news_service.fetch_house_scope(s, scope)
        if person_id is not None:
            topics += await news_service.fetch_person_topics(s, person_id)
            briefing = await news_service.summarize_person_briefing(s, person_id)
        await s.execute(text("SELECT pg_notify('news_changed', 'polled')"))
    return {"house": house, "topics": topics, "briefed": briefing is not None}


# ─── Helpers ─────────────────────────────────────────────────────────────


async def _ensure_person(s, person_id: int) -> None:
    row = await s.execute(
        text("SELECT 1 FROM people WHERE id = :id"), {"id": person_id}
    )
    if row.first() is None:
        raise HTTPException(status_code=404, detail=f"person {person_id} not found")


def _row_to_feed(r: Any) -> NewsFeed:
    return NewsFeed(
        id=int(r[0]), url=r[1], title=r[2], source=r[3], discovered_via=r[4],
        scope=r[5], valid=bool(r[6]), last_polled_at=r[7], last_error=r[8],
    )


def _row_to_item(r: Any) -> NewsItem:
    return NewsItem(
        id=int(r[0]), title=r[1], summary=r[2], source=r[3], url=r[4],
        topic=r[5], published_at=r[6], fetched_at=r[7],
        favorited=bool(r[8]), read_at=r[9],
    )
