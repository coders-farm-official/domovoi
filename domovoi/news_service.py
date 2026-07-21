"""News domain operations — shared by the fetcher worker, the NewsHandler,
and the web News API so the SQL + fetch/summarize logic lives in ONE place.

Every function takes an ``AsyncSession`` (the caller owns the transaction /
commit), mirroring how ``profile_context`` / repositories are used. The web
backend runs in a separate process but imports this module and passes its own
``web.backend.db`` session — the functions never touch process-local state.

Ownership model:
  * HOUSE-scope items:  person_id NULL, scope in (local|national|global).
  * PER-PERSON items:   person_id set, scope NULL, topic_id set (or NULL for
                        an ad-hoc subject fetched for a known speaker).

All fetching is best-effort and network-gated by the caller (``ctx.online`` /
worker gating). A dead feed is flagged (``valid=false`` + ``last_error``) and
skipped, never fatal.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.clients import news_feeds
from domovoi.clients.ollama import get_ollama_client
from domovoi.config import settings

log = logging.getLogger(__name__)


# ─── Feed registry helpers ───────────────────────────────────────────────


async def _upsert_feed(
    session: AsyncSession,
    url: str,
    *,
    title: str | None = None,
    source: str | None = None,
    discovered_via: str = "manual",
    scope: str | None = None,
) -> int:
    """Insert (or fetch) a feed row by its UNIQUE url; returns feed id.
    Deduped across people/topics — a shared feed is stored once."""
    row = (
        await session.execute(
            text(
                """
                INSERT INTO news_feeds (url, title, source, discovered_via, scope)
                VALUES (:url, :title, :source, :dv, :scope)
                ON CONFLICT (url) DO UPDATE
                    SET title = COALESCE(news_feeds.title, EXCLUDED.title),
                        source = COALESCE(news_feeds.source, EXCLUDED.source),
                        -- promote scope if a house feed was previously stored
                        -- scope-less (e.g. added as a topic feed first).
                        scope = COALESCE(news_feeds.scope, EXCLUDED.scope)
                RETURNING id
                """
            ),
            {
                "url": url,
                "title": title,
                "source": source,
                "dv": discovered_via,
                "scope": scope,
            },
        )
    ).first()
    return int(row[0])


async def _attach_topic_feed(session: AsyncSession, topic_id: int, feed_id: int) -> None:
    await session.execute(
        text(
            """
            INSERT INTO topic_feeds (topic_id, feed_id)
            VALUES (:tid, :fid)
            ON CONFLICT (topic_id, feed_id) DO NOTHING
            """
        ),
        {"tid": topic_id, "fid": feed_id},
    )


async def ensure_house_feeds(session: AsyncSession) -> None:
    """Seed the curated national + global scope feeds (idempotent) and, when
    ``news_location`` is configured and no local feed exists yet, discover one.
    Called by the worker before a house-scope fetch."""
    for url in news_feeds.NATIONAL_FEEDS:
        await _upsert_feed(session, url, discovered_via="default", scope="national")
    for url in news_feeds.GLOBAL_FEEDS:
        await _upsert_feed(session, url, discovered_via="default", scope="global")

    if settings.news_location.strip():
        has_local = (
            await session.execute(
                text("SELECT 1 FROM news_feeds WHERE scope = 'local' LIMIT 1")
            )
        ).first()
        if has_local is None:
            for feed in await news_feeds.local_feed_candidates(settings.news_location):
                await _upsert_feed(
                    session, feed.url, title=feed.title, source=feed.source,
                    discovered_via="searxng", scope="local",
                )


async def attach_category_feeds(
    session: AsyncSession, topic_id: int, category: str
) -> int:
    """Attach the curated default feeds for a category topic. Returns the
    count attached."""
    urls = news_feeds.default_feeds_for_category(category)
    for url in urls:
        fid = await _upsert_feed(session, url, discovered_via="default")
        await _attach_topic_feed(session, topic_id, fid)
    return len(urls)


async def discover_and_attach_feeds(
    session: AsyncSession, topic_id: int, phrase: str
) -> int:
    """Run the SearxNG→probe→validate discovery loop for a free-form topic
    and attach the validated feeds. Returns the count attached. Network —
    caller gates on connectivity."""
    discovered = await news_feeds.discover_feeds(phrase)
    for feed in discovered:
        fid = await _upsert_feed(
            session, feed.url, title=feed.title, source=feed.source,
            discovered_via="searxng",
        )
        await _attach_topic_feed(session, topic_id, fid)
    return len(discovered)


# ─── Item insertion / fetching ───────────────────────────────────────────


async def _insert_article(
    session: AsyncSession,
    article: news_feeds.FeedArticle,
    *,
    feed_id: int,
    person_id: int | None,
    scope: str | None,
    topic_id: int | None,
) -> bool:
    """Insert one article as a news_item, deduped. Returns True if a new row
    landed. ``ON CONFLICT DO NOTHING`` targets the dedup unique index."""
    result = await session.execute(
        text(
            """
            INSERT INTO news_items
                (person_id, scope, topic_id, feed_id, guid, url, title,
                 source, summary, published_at)
            VALUES
                (:pid, :scope, :tid, :fid, :guid, :url, :title,
                 :source, :summary, :pub)
            ON CONFLICT DO NOTHING
            """
        ),
        {
            "pid": person_id,
            "scope": scope,
            "tid": topic_id,
            "fid": feed_id,
            "guid": article.guid,
            "url": article.url,
            "title": article.title,
            "source": article.source,
            "summary": article.summary,
            "pub": article.published_at,
        },
    )
    return (result.rowcount or 0) > 0


async def _mark_feed_polled(
    session: AsyncSession, feed_id: int, *, error: str | None = None
) -> None:
    await session.execute(
        text(
            """
            UPDATE news_feeds
               SET last_polled_at = now(),
                   valid = :valid,
                   last_error = :err
             WHERE id = :id
            """
        ),
        {"id": feed_id, "valid": error is None, "err": error},
    )


async def poll_feed(
    session: AsyncSession,
    feed_id: int,
    feed_url: str,
    *,
    person_id: int | None,
    scope: str | None,
    topic_id: int | None,
    limit: int = 30,
) -> int:
    """Parse ``feed_url`` and insert its newest ``limit`` articles as deduped
    news_items for the given owner. Returns the number of NEW items. Flags the
    feed invalid on a parse failure."""
    title, articles = await news_feeds.parse_feed(feed_url)
    if not articles:
        await _mark_feed_polled(session, feed_id, error="no articles / parse failed")
        return 0
    new_count = 0
    for article in articles[:limit]:
        if not article.source and title:
            article.source = title
        if await _insert_article(
            session, article, feed_id=feed_id,
            person_id=person_id, scope=scope, topic_id=topic_id,
        ):
            new_count += 1
    await _mark_feed_polled(session, feed_id, error=None)
    return new_count


async def fetch_house_scope(session: AsyncSession, scope: str) -> int:
    """Poll every feed for a geographic scope into house-scoped items."""
    feeds = (
        await session.execute(
            text("SELECT id, url FROM news_feeds WHERE scope = :scope"),
            {"scope": scope},
        )
    ).all()
    total = 0
    for fid, url in feeds:
        total += await poll_feed(
            session, int(fid), url,
            person_id=None, scope=scope, topic_id=None,
        )
    return total


async def fetch_person_topics(session: AsyncSession, person_id: int) -> int:
    """Poll every feed attached to a person's topics into per-person items."""
    rows = (
        await session.execute(
            text(
                """
                SELECT DISTINCT nf.id, nf.url, tf.topic_id
                  FROM news_feeds nf
                  JOIN topic_feeds tf ON tf.feed_id = nf.id
                  JOIN news_topics nt ON nt.id = tf.topic_id
                 WHERE nt.person_id = :pid AND nf.valid
                """
            ),
            {"pid": person_id},
        )
    ).all()
    total = 0
    for fid, url, topic_id in rows:
        total += await poll_feed(
            session, int(fid), url,
            person_id=person_id, scope=None, topic_id=int(topic_id),
        )
    return total


# ─── Reads (digest / briefing / saved feed) ──────────────────────────────


async def unread_status(session: AsyncSession, person_id: int) -> dict[str, Any]:
    """Status-first digest data: unread count + the oldest and newest unread
    item (title + published_at + topic phrase). Empty dict fields when there
    are no unread items."""
    count = (
        await session.execute(
            text(
                "SELECT COUNT(*) FROM news_items "
                "WHERE person_id = :pid AND read_at IS NULL"
            ),
            {"pid": person_id},
        )
    ).scalar_one()
    oldest = (
        await session.execute(
            text(
                """
                SELECT ni.title, ni.published_at, nt.topic
                  FROM news_items ni
                  LEFT JOIN news_topics nt ON nt.id = ni.topic_id
                 WHERE ni.person_id = :pid AND ni.read_at IS NULL
                 ORDER BY ni.published_at ASC NULLS LAST, ni.id ASC
                 LIMIT 1
                """
            ),
            {"pid": person_id},
        )
    ).first()
    newest = (
        await session.execute(
            text(
                """
                SELECT ni.title, ni.published_at, nt.topic
                  FROM news_items ni
                  LEFT JOIN news_topics nt ON nt.id = ni.topic_id
                 WHERE ni.person_id = :pid AND ni.read_at IS NULL
                 ORDER BY ni.published_at DESC NULLS LAST, ni.id DESC
                 LIMIT 1
                """
            ),
            {"pid": person_id},
        )
    ).first()

    def _pack(r: Any) -> dict[str, Any] | None:
        if r is None:
            return None
        return {"title": r[0], "published_at": r[1], "topic": r[2]}

    return {"unread": int(count), "oldest": _pack(oldest), "newest": _pack(newest)}


async def read_person_items(
    session: AsyncSession,
    person_id: int,
    *,
    limit: int,
    order: str = "newest",
    unread_only: bool = True,
    mark_read: bool = True,
) -> list[dict[str, Any]]:
    """Return up to ``limit`` of a person's items (newest or oldest first),
    optionally only unread, and mark the returned rows read. Each dict carries
    id/title/summary/source/url/published_at."""
    direction = "ASC" if order == "oldest" else "DESC"
    unread_clause = "AND read_at IS NULL" if unread_only else ""
    rows = (
        await session.execute(
            text(
                f"""
                SELECT id, title, summary, source, url, published_at
                  FROM news_items
                 WHERE person_id = :pid {unread_clause}
                 ORDER BY published_at {direction} NULLS LAST, id {direction}
                 LIMIT :limit
                """
            ),
            {"pid": person_id, "limit": limit},
        )
    ).all()
    items = [
        {
            "id": int(r[0]),
            "title": r[1],
            "summary": r[2],
            "source": r[3],
            "url": r[4],
            "published_at": r[5],
        }
        for r in rows
    ]
    if mark_read and items:
        ids = [it["id"] for it in items]
        await session.execute(
            text("UPDATE news_items SET read_at = now() WHERE id = ANY(:ids)"),
            {"ids": ids},
        )
    return items


async def read_scope_items(
    session: AsyncSession, scope: str, *, limit: int
) -> list[dict[str, Any]]:
    """Newest ``limit`` house-scope items for a geography (general briefing)."""
    rows = (
        await session.execute(
            text(
                """
                SELECT id, title, summary, source, url, published_at
                  FROM news_items
                 WHERE scope = :scope
                 ORDER BY published_at DESC NULLS LAST, id DESC
                 LIMIT :limit
                """
            ),
            {"scope": scope, "limit": limit},
        )
    ).all()
    return [
        {
            "id": int(r[0]),
            "title": r[1],
            "summary": r[2],
            "source": r[3],
            "url": r[4],
            "published_at": r[5],
        }
        for r in rows
    ]


async def read_subject_items(
    session: AsyncSession,
    subject: str,
    *,
    person_id: int | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Cached items matching an ad-hoc subject: title/summary/topic ILIKE.
    Scoped to the person's items when known, else house items. Read verbs use
    this (no network)."""
    like = f"%{subject.lower()}%"
    if person_id is not None:
        # Qualify with the ``ni`` alias — this query LEFT JOINs news_topics,
        # which ALSO has a person_id column, so an unqualified reference is
        # an AmbiguousColumnError at runtime. (scope only exists on
        # news_items, but qualify it too for consistency.)
        owner_clause = "ni.person_id = :pid"
        params: dict[str, Any] = {"pid": person_id, "like": like, "limit": limit}
    else:
        owner_clause = "ni.scope IS NOT NULL"
        params = {"like": like, "limit": limit}
    rows = (
        await session.execute(
            text(
                f"""
                SELECT ni.id, ni.title, ni.summary, ni.source, ni.url, ni.published_at
                  FROM news_items ni
                  LEFT JOIN news_topics nt ON nt.id = ni.topic_id
                 WHERE {owner_clause}
                   AND (LOWER(COALESCE(ni.title, '')) LIKE :like
                        OR LOWER(COALESCE(ni.summary, '')) LIKE :like
                        OR LOWER(COALESCE(nt.topic, '')) LIKE :like)
                 ORDER BY ni.published_at DESC NULLS LAST, ni.id DESC
                 LIMIT :limit
                """
            ),
            params,
        )
    ).all()
    return [
        {
            "id": int(r[0]),
            "title": r[1],
            "summary": r[2],
            "source": r[3],
            "url": r[4],
            "published_at": r[5],
        }
        for r in rows
    ]


async def fetch_subject_live(
    session: AsyncSession,
    subject: str,
    *,
    person_id: int | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Fetch-verb path for an ad-hoc subject: discover feeds for the subject,
    parse them live, persist as per-person items (topic_id NULL) when the
    speaker is known, and return the newest ``limit``. Network — caller gates
    on connectivity + the single verbal confirm.

    For an anonymous speaker we can't persist a per-person item (the schema
    requires a scope), so we return the parsed articles directly without
    storing them."""
    discovered = await news_feeds.discover_feeds(subject)
    live_articles: list[news_feeds.FeedArticle] = []
    for feed in discovered:
        if person_id is not None:
            fid = await _upsert_feed(
                session, feed.url, title=feed.title, source=feed.source,
                discovered_via="searxng",
            )
            await poll_feed(
                session, fid, feed.url,
                person_id=person_id, scope=None, topic_id=None,
            )
        else:
            _title, articles = await news_feeds.parse_feed(feed.url)
            live_articles.extend(articles)

    if person_id is not None:
        return await read_subject_items(
            session, subject, person_id=person_id, limit=limit,
        )
    # Anonymous: sort the live articles newest-first and shape them.
    live_articles.sort(
        key=lambda a: (a.published_at is not None, a.published_at), reverse=True
    )
    return [
        {
            "id": None,
            "title": a.title,
            "summary": a.summary,
            "source": a.source,
            "url": a.url,
            "published_at": a.published_at,
        }
        for a in live_articles[:limit]
    ]


async def favorite_item(session: AsyncSession, item_id: int, favorited: bool = True) -> bool:
    """Toggle an item's favorited flag (favorited items are retention-exempt).
    Returns True when a row was updated."""
    result = await session.execute(
        text("UPDATE news_items SET favorited = :fav WHERE id = :id"),
        {"id": item_id, "fav": favorited},
    )
    return (result.rowcount or 0) > 0


# ─── Per-person LLM briefing ─────────────────────────────────────────────


async def summarize_person_briefing(session: AsyncSession, person_id: int) -> str | None:
    """Condense a person's recent unread items into a short spoken digest via
    the local Ollama and upsert ``news_briefings``. Returns the briefing text,
    or None when there's nothing to summarize."""
    rows = (
        await session.execute(
            text(
                """
                SELECT ni.title, ni.summary, ni.source, nt.topic
                  FROM news_items ni
                  LEFT JOIN news_topics nt ON nt.id = ni.topic_id
                 WHERE ni.person_id = :pid AND ni.read_at IS NULL
                 ORDER BY ni.published_at DESC NULLS LAST, ni.id DESC
                 LIMIT 20
                """
            ),
            {"pid": person_id},
        )
    ).all()
    if not rows:
        return None

    lines: list[str] = []
    for title, summary, source, topic in rows:
        bits = [b for b in (title, (summary or "")[:300]) if b]
        tag = f"[{topic}] " if topic else ""
        lines.append(f"- {tag}{' — '.join(bits)}" + (f" ({source})" if source else ""))
    article_block = "\n".join(lines)

    prompt = (
        "You are a news anchor giving a short spoken morning briefing. "
        "Summarize the following headlines into a concise, natural spoken "
        "digest of a few sentences. Group related items, lead with the most "
        "important, and do not invent facts beyond what's given.\n\n"
        f"Headlines:\n{article_block}\n\nSpoken briefing:"
    )
    try:
        briefing = await get_ollama_client().qa(prompt)
    except Exception as e:  # noqa: BLE001
        log.warning("news: briefing summarize failed for person %s: %s", person_id, e)
        return None

    briefing = (briefing or "").strip()
    if not briefing:
        return None
    await session.execute(
        text(
            """
            INSERT INTO news_briefings (person_id, briefing, generated_at)
            VALUES (:pid, :briefing, now())
            ON CONFLICT (person_id) DO UPDATE
                SET briefing = EXCLUDED.briefing,
                    generated_at = EXCLUDED.generated_at
            """
        ),
        {"pid": person_id, "briefing": briefing},
    )
    return briefing


async def get_briefing(session: AsyncSession, person_id: int) -> dict[str, Any] | None:
    row = (
        await session.execute(
            text(
                "SELECT briefing, generated_at FROM news_briefings WHERE person_id = :pid"
            ),
            {"pid": person_id},
        )
    ).first()
    if row is None:
        return None
    return {"briefing": row[0], "generated_at": row[1]}


# ─── Retention ───────────────────────────────────────────────────────────


async def retention_sweep(session: AsyncSession, retention_days: int) -> int:
    """Delete items older than the retention window, EXCEPT favorited ones.
    Returns the number deleted."""
    result = await session.execute(
        text(
            """
            DELETE FROM news_items
             WHERE fetched_at < now() - (:days * interval '1 day')
               AND NOT favorited
            """
        ),
        {"days": retention_days},
    )
    return result.rowcount or 0
