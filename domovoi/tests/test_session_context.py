from __future__ import annotations

import pytest
from sqlalchemy import text
from uuid import uuid4

from domovoi.db.repositories import SessionRepository
from domovoi.models import Context, Intent
from domovoi.router import route
from domovoi.tests.conftest import requires_db


@requires_db
@pytest.mark.asyncio
async def test_record_exchange_appends_user_and_assistant_turns(db_session) -> None:
    repo = SessionRepository(db_session)
    sid = await repo.get_or_create(None, "kitchen")
    await repo.record_exchange(sid, "hello", "hi there", recent_turns_cap=20)
    await db_session.commit()

    ctx = await repo.get_context(sid)
    turns = ctx["recent_turns"]
    assert len(turns) == 2
    assert turns[0]["role"] == "user" and turns[0]["text"] == "hello"
    assert turns[1]["role"] == "assistant" and turns[1]["text"] == "hi there"
    assert ctx["last_assistant_response"] == "hi there"


@requires_db
@pytest.mark.asyncio
async def test_recent_turns_capped(db_session) -> None:
    repo = SessionRepository(db_session)
    sid = await repo.get_or_create(None, "kitchen")
    # Cap at 4 means 2 exchanges (4 turns) max.
    for i in range(5):
        await repo.record_exchange(sid, f"q{i}", f"a{i}", recent_turns_cap=4)
    await db_session.commit()

    ctx = await repo.get_context(sid)
    turns = ctx["recent_turns"]
    assert len(turns) == 4
    # Last 4 turns should be q3/a3/q4/a4.
    assert [t["text"] for t in turns] == ["q3", "a3", "q4", "a4"]
    assert ctx["last_assistant_response"] == "a4"


@requires_db
@pytest.mark.asyncio
async def test_set_context_key(db_session) -> None:
    repo = SessionRepository(db_session)
    sid = await repo.get_or_create(None, "kitchen")
    await repo.set_context_key(sid, "last_played_track", {"title": "Creep", "artist": "Radiohead"})
    await db_session.commit()

    ctx = await repo.get_context(sid)
    assert ctx["last_played_track"]["artist"] == "Radiohead"


@requires_db
@pytest.mark.asyncio
async def test_router_populates_session_context(db_session) -> None:
    intent = Intent(transcript="set a timer for 5 minutes", room_id="kitchen")
    ctx = Context(room_id="kitchen", online=True)
    response = await route(intent, ctx, db_session)
    await db_session.commit()

    session_ctx = await SessionRepository(db_session).get_context(response.session_id)
    turns = session_ctx["recent_turns"]
    assert len(turns) == 2
    assert turns[0]["text"] == "set a timer for 5 minutes"
    assert "5 minutes" in turns[1]["text"]
    assert session_ctx["last_assistant_response"] == response.text
