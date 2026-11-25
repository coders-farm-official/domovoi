"""The two live events every socket gets carry no chat content
(PRIVATE-CHATS.md §2.4, the 1.5.0 leak fixes):

* ``chat.changed``: an opaque digest that moves when any chat changes,
  never the house's thread count, a time or an id;
* ``help_deliveries.changed``: never a chat's title (the kind only, for
  the apps' notification), the rest read over HTTP.
"""

from __future__ import annotations

import json
import re
import uuid

import pytest
from sqlalchemy import text

from domovoi.tests.conftest import requires_db
from web.backend import realtime


@requires_db
@pytest.mark.asyncio
async def test_the_chat_event_is_content_free_and_still_moves() -> None:
    from domovoi.db.session import session_scope

    before = await realtime._snapshot_chat()
    assert set(before) == {"changed"}
    assert re.fullmatch(r"[0-9a-f]{16}", before["changed"])
    async with session_scope() as s:
        tid = (await s.execute(text("INSERT INTO chat_threads (title) VALUES ('Secret plans') RETURNING id"))
               ).scalar_one()
        count = (await s.execute(text("SELECT COUNT(*) FROM chat_threads"))).scalar_one()
        await s.commit()
    try:
        after = await realtime._snapshot_chat()
        assert after != before, "a new chat must move the digest (clients refetch on it)"
        flat = json.dumps(after)
        assert "Secret" not in flat and str(tid) not in flat and f'"{count}"' not in flat
        assert not any(isinstance(v, (int, float)) for v in after.values())
        assert await realtime._snapshot_chat() == after, "the same chats give the same digest"
    finally:
        async with session_scope() as s:
            await s.execute(text("DELETE FROM chat_threads WHERE id = :t"), {"t": tid})
            await s.commit()
    assert await realtime._snapshot_chat() != after


@requires_db
@pytest.mark.asyncio
async def test_the_deliveries_push_never_carries_a_chat_title() -> None:
    from domovoi.db.session import session_scope

    secret = "Photos: the surprise party " + uuid.uuid4().hex[:6]
    help_secret = "Help: my private question " + uuid.uuid4().hex[:6]
    async with session_scope() as s:
        pid = (await s.execute(text("INSERT INTO people (name) VALUES (:n) RETURNING id"),
                               {"n": "Test " + uuid.uuid4().hex[:6]})).scalar_one()
        ids = []
        for title in (secret, help_secret):
            tid = (await s.execute(text("INSERT INTO chat_threads (title, person_id) VALUES (:t, :p) RETURNING id"),
                                   {"t": title, "p": pid})).scalar_one()
            did = (await s.execute(text(
                "INSERT INTO help_deliveries (person_id, thread_id, title) VALUES (:p, :t, :ti) RETURNING id"),
                {"p": pid, "t": tid, "ti": title})).scalar_one()
            ids.append((tid, did))
        await s.commit()
    try:
        rows = await realtime._snapshot_help_deliveries()
        ours = {r["id"]: r for r in rows if r["id"] in {d for _, d in ids}}
        assert len(ours) == 2
        flat = json.dumps(rows)
        assert "surprise" not in flat and "private question" not in flat
        photo, helped = ours[ids[0][1]], ours[ids[1][1]]
        assert photo["title"] == realtime.HELP_DELIVERIES_PHOTOS_TITLE
        assert helped["title"] == ""
        # What the 1.4.x / 1.5.0 apps need to post from the push.
        for r in (photo, helped):
            assert set(r) == {"id", "person_id", "thread_id", "title", "created_at"}
            assert r["person_id"] == pid
    finally:
        async with session_scope() as s:
            for tid, _ in ids:
                await s.execute(text("DELETE FROM chat_threads WHERE id = :t"), {"t": tid})
            await s.execute(text("DELETE FROM people WHERE id = :p"), {"p": pid})
            await s.commit()


def test_the_module_docs_say_what_the_two_events_carry() -> None:
    doc = realtime.__doc__ or ""
    assert "Content-free" in doc and "never the chat's" in " ".join(doc.split())
