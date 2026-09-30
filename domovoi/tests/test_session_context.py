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
    # Cap at 8 means 4 exchanges max; past it, the older half goes.
    for i in range(5):
        await repo.record_exchange(sid, f"q{i}", f"a{i}", recent_turns_cap=8)
    await db_session.commit()

    ctx = await repo.get_context(sid)
    turns = ctx["recent_turns"]
    # 5 exchanges = 10 entries > 8: the newest 4 entries stay.
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


# ─── History trimming (DB-free) ──────────────────────────────────────────


def _turns(n: int) -> list[dict]:
    return [{"role": "user" if i % 2 == 0 else "assistant", "text": f"t{i}"} for i in range(n)]


def test_history_under_the_cap_is_kept_whole() -> None:
    from domovoi.db.repositories import trim_recent_turns

    assert trim_recent_turns(_turns(20), 20) == _turns(20)


def test_history_past_the_cap_keeps_its_newer_half() -> None:
    from domovoi.db.repositories import trim_recent_turns

    kept = trim_recent_turns(_turns(22), 20)
    assert [t["text"] for t in kept] == [f"t{i}" for i in range(12, 22)]
    assert kept[0]["role"] == "user"                 # whole exchanges


def test_between_cuts_the_history_only_grows() -> None:
    """The Q&A prompt's history must keep its start from one turn to the
    next (Ollama reuses a cached prompt up to the first changed token):
    at cap 20 it changes on one turn in five, not on every turn."""
    from domovoi.db.repositories import trim_recent_turns

    history: list[dict] = []
    starts_changed = 0
    for i in range(40):
        before = list(history)
        history = trim_recent_turns(history + [{"role": "user", "text": f"q{i}"},
                                               {"role": "assistant", "text": f"a{i}"}], 20)
        assert 2 <= len(history) <= 20
        if before and history[: len(before)] != before:
            starts_changed += 1
    assert starts_changed <= 40 // 5 + 1


def test_the_smallest_caps_keep_the_last_exchange() -> None:
    from domovoi.db.repositories import trim_recent_turns

    assert [t["text"] for t in trim_recent_turns(_turns(4), 2)] == ["t2", "t3"]
    assert [t["text"] for t in trim_recent_turns(_turns(6), 4)] == ["t4", "t5"]
