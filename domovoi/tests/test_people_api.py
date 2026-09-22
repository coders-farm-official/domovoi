"""Endpoint tests for the people API — web/backend/api/people.py.

F-A007: ``GET /api/people/{id}/sessions`` answered 500 on every call because
the raw SQL bound ``:pid`` twice — once as ``WHERE cl.person_id = :pid``
(integer) and once as a bare ``:pid AS person_id`` projection (no type).
SQLAlchemy hands asyncpg both occurrences as the same ``$1`` and asyncpg
refuses to prepare it (AmbiguousParameterError: inconsistent types deduced
for parameter $1). It failed at prepare time, so no amount of data made it
pass — and no ``requires_db`` test had ever reached it.

The DB-free half below compiles the statement exactly the way the asyncpg
dialect does and asserts the shape asyncpg receives, so this regression is
caught even where Postgres is absent and the DB-backed half silently skips.
"""

from __future__ import annotations

import asyncio
import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import asyncpg as asyncpg_dialect

import web.backend.api.people as people_api
from domovoi.tests.conftest import requires_db
from web.backend.main import app

# A bare bind projected straight into the SELECT list: "$1 AS person_id".
# That is the exact token asyncpg cannot type when the same parameter is
# also compared against an integer column elsewhere in the statement.
_UNTYPED_PROJECTED_BIND = re.compile(r"\$\d+\s+AS\s+\w+", re.IGNORECASE)


# ─── DB-free: the statement asyncpg is asked to prepare ─────────────────────
def test_person_sessions_sql_projects_no_untyped_bind_parameter():
    compiled = people_api.PERSON_SESSIONS_SQL.compile(dialect=asyncpg_dialect.dialect())
    sql = str(compiled)

    assert not _UNTYPED_PROJECTED_BIND.search(sql), (
        "PERSON_SESSIONS_SQL projects a bare bind parameter; asyncpg cannot "
        "deduce a single type for it once the same parameter is also compared "
        "against an integer column (F-A007):\n" + sql
    )
    # The filter still binds the person id, and nothing else is bound twice.
    assert "cl.person_id = $1" in sql
    assert list(compiled.positiontup) == ["pid", "limit"]
    assert sql.count("$1") == 1


def test_person_sessions_sql_still_has_the_shape_the_handler_unpacks():
    # list_sessions() unpacks r[0..4] positionally: id, room_id, started_at,
    # last_activity, intent_count. person_id comes from the path argument.
    sql = str(people_api.PERSON_SESSIONS_SQL)
    select_list = sql.split("FROM", 1)[0]
    for col in ("s.id::text", "s.room_id", "s.started_at", "s.last_activity", "COUNT(cl.id) AS intent_count"):
        assert col in select_list, col
    assert "person_id" not in select_list


# ─── DB-backed: the endpoint round-trips seeded rows ────────────────────────
def _seed_person_with_session(turns: int) -> tuple[int, str]:
    """One person, one session, ``turns`` conversation_log rows attributed
    to them. Returns (person_id, session_id). NullPool engine: safe under a
    throwaway asyncio.run loop (same pattern as test_wake_words_api)."""
    from domovoi.db.session import engine

    sid = str(uuid.uuid4())

    async def _go() -> int:
        async with engine.begin() as conn:
            await conn.execute(
                text("TRUNCATE conversation_log, sessions, people RESTART IDENTITY CASCADE")
            )
            pid = (
                await conn.execute(
                    text("INSERT INTO people (name) VALUES ('Test Person') RETURNING id")
                )
            ).scalar_one()
            await conn.execute(
                text("INSERT INTO sessions (id, room_id) VALUES (CAST(:sid AS uuid), 'bench')"),
                {"sid": sid},
            )
            for i in range(turns):
                await conn.execute(
                    text(
                        "INSERT INTO conversation_log (session_id, room_id, person_id, user_text, assistant_text) "
                        "VALUES (CAST(:sid AS uuid), 'bench', :pid, :u, 'ok')"
                    ),
                    {"sid": sid, "pid": pid, "u": f"turn {i} pumpkin" if i == 0 else f"turn {i}"},
                )
            return int(pid)

    return asyncio.run(_go()), sid


@requires_db
def test_list_sessions_returns_the_seeded_session_with_its_turn_count():
    pid, sid = _seed_person_with_session(turns=3)
    with TestClient(app) as c:
        r = c.get(f"/api/people/{pid}/sessions?limit=50")
        assert r.status_code == 200, r.text
        rows = r.json()
        assert len(rows) == 1
        assert rows[0]["id"] == sid
        assert rows[0]["room_id"] == "bench"
        assert rows[0]["person_id"] == pid
        assert rows[0]["intent_count"] == 3

        # Sibling endpoint on the same rows, as the PPL-04 card cross-checks.
        conv = c.get(f"/api/people/{pid}/conversations?limit=200")
        assert conv.status_code == 200
        assert len(conv.json()) == 3


@requires_db
def test_list_sessions_is_empty_not_500_for_a_person_with_no_turns():
    pid, _ = _seed_person_with_session(turns=0)
    with TestClient(app) as c:
        r = c.get(f"/api/people/{pid}/sessions")
        assert r.status_code == 200, r.text
        assert r.json() == []
        # An unknown person is also an empty list, not a server error.
        r2 = c.get(f"/api/people/{pid + 1000}/sessions")
        assert r2.status_code == 200, r2.text
        assert r2.json() == []
