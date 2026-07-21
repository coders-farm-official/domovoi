"""Podcast feed poller — keep-N eviction test.

The LRU keep-N contract is the one piece of poller logic worth a DB test:
downloaded episodes beyond the newest-N window get their file deleted and
row flipped to 'skipped'. Feed fetch/parse itself is a thin feedparser
wrapper (network) and isn't unit-tested here.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from domovoi.tests.conftest import requires_db
from domovoi.workers.podcast_feed_poller import enforce_keep_n, enqueue_newest


async def _make_sub(session, feed_url: str, keep_n: int = 2) -> int:
    row = (
        await session.execute(
            text(
                "INSERT INTO podcast_subscriptions (feed_url, title, keep_n) "
                "VALUES (:u, 'Show', :k) RETURNING id"
            ),
            {"u": feed_url, "k": keep_n},
        )
    ).first()
    return int(row[0])


async def _add_episode(session, sub_id, idx, *, status, file_path=None):
    published = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=idx)
    await session.execute(
        text(
            """
            INSERT INTO podcast_episodes
                (subscription_id, guid, title, published_at, download_status, file_path)
            VALUES (:sid, :guid, :title, :pub, :st, :fp)
            """
        ),
        {
            "sid": sub_id, "guid": f"g{idx}", "title": f"ep{idx}",
            "pub": published, "st": status, "fp": file_path,
        },
    )


@requires_db
@pytest.mark.asyncio
async def test_enforce_keep_n_evicts_oldest_downloaded(db_session, tmp_path) -> None:
    sub_id = await _make_sub(db_session, "http://feed/keep", keep_n=2)
    # 4 downloaded episodes, published oldest→newest (idx 0..3). Each has a
    # real file so eviction can delete it.
    files = []
    for i in range(4):
        f = tmp_path / f"ep{i}.mp3"
        f.write_bytes(b"x")
        files.append(f)
        await _add_episode(db_session, sub_id, i, status="downloaded", file_path=str(f))
    await db_session.commit()

    evicted = await enforce_keep_n(db_session, sub_id, keep_n=2)
    await db_session.commit()

    # 2 oldest evicted; 2 newest kept.
    assert evicted == 2
    kept = (
        await db_session.execute(
            text(
                "SELECT title FROM podcast_episodes "
                "WHERE subscription_id = :s AND download_status = 'downloaded' "
                "ORDER BY published_at"
            ),
            {"s": sub_id},
        )
    ).all()
    assert [r[0] for r in kept] == ["ep2", "ep3"]

    skipped = (
        await db_session.execute(
            text(
                "SELECT title, file_path FROM podcast_episodes "
                "WHERE subscription_id = :s AND download_status = 'skipped' "
                "ORDER BY published_at"
            ),
            {"s": sub_id},
        )
    ).all()
    assert [r[0] for r in skipped] == ["ep0", "ep1"]
    # Evicted files deleted from disk; file_path cleared.
    assert all(r[1] is None for r in skipped)
    assert not files[0].exists() and not files[1].exists()
    assert files[2].exists() and files[3].exists()


@requires_db
@pytest.mark.asyncio
async def test_enforce_keep_n_noop_when_within_window(db_session, tmp_path) -> None:
    sub_id = await _make_sub(db_session, "http://feed/small", keep_n=5)
    for i in range(3):
        f = tmp_path / f"s{i}.mp3"
        f.write_bytes(b"x")
        await _add_episode(db_session, sub_id, i, status="downloaded", file_path=str(f))
    await db_session.commit()
    evicted = await enforce_keep_n(db_session, sub_id, keep_n=5)
    assert evicted == 0


@requires_db
@pytest.mark.asyncio
async def test_enqueue_newest_arms_skipped_in_window(db_session) -> None:
    sub_id = await _make_sub(db_session, "http://feed/enq", keep_n=2)
    for i in range(3):
        await _add_episode(db_session, sub_id, i, status="skipped")
    await db_session.commit()
    armed = await enqueue_newest(db_session, sub_id, keep_n=2)
    await db_session.commit()
    # Only the newest-2 skipped rows flip to pending.
    assert armed == 2
    pending = (
        await db_session.execute(
            text(
                "SELECT title FROM podcast_episodes "
                "WHERE subscription_id = :s AND download_status = 'pending' "
                "ORDER BY published_at"
            ),
            {"s": sub_id},
        )
    ).all()
    assert [r[0] for r in pending] == ["ep1", "ep2"]
