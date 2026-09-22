"""Tests for the web backend's ``PATCH /api/playlists/{id}/order``
(``web/backend/api/playlists.py::reorder_playlist``) — F-022.

The route rewrote positions with ``UPDATE … FROM (VALUES (:t0, 0), …)``.
A bare bind in a VALUES list has nothing to infer a type from, so asyncpg
sends it as ``text`` and Postgres refuses ``pt.track_id = v.tid`` with
"operator does not exist: integer = text" — every reorder 500'd. The
statement now casts each bind to integer.

Two halves, deliberately: the DB-backed test proves the statement runs
against real Postgres, but it carries ``@requires_db`` and SKIPS (green)
on a box without one — which is exactly how the broken statement stayed
unnoticed. So the emitted SQL text is also asserted DB-free, both from the
pure builder and from the route itself via a recording fake session.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

import web.backend.api.playlists as playlists_api
from domovoi.tests.conftest import requires_db
from web.backend.main import app
from web.backend.schemas import PlaylistReorder

_BIND_RE = re.compile(r":t\d+")
_CAST_RE = re.compile(r"CAST\(:t(\d+) AS integer\)")


# ─── DB-free: the statement builder ────────────────────────────────────────


def test_reorder_statement_casts_every_track_bind_to_integer() -> None:
    sql, params = playlists_api._reorder_statement([7, 3, 5])

    assert params == {"t0": 7, "t1": 3, "t2": 5}
    # Every :tN in the text is wrapped in CAST(... AS integer) — no bare
    # bind survives for the driver to send as text.
    assert sorted(_BIND_RE.findall(sql)) == [":t0", ":t1", ":t2"]
    assert [int(n) for n in _CAST_RE.findall(sql)] == [0, 1, 2]
    assert "(:t0," not in sql and "(:t1," not in sql and "(:t2," not in sql
    # Position literals travel next to their cast bind, in submission order.
    assert "(CAST(:t0 AS integer), 0), (CAST(:t1 AS integer), 1), (CAST(:t2 AS integer), 2)" in sql
    # The shape of the rewrite is unchanged.
    assert "UPDATE playlist_tracks AS pt" in sql
    assert "SET position = v.pos" in sql
    assert "AS v(tid, pos)" in sql
    assert "WHERE pt.playlist_id = :id AND pt.track_id = v.tid" in sql


def test_reorder_statement_binds_are_real_binds_under_the_postgres_dialect() -> None:
    """The casts must not have turned the ``:tN`` markers into literal text —
    compiling under the Postgres dialect still sees every bind by name."""
    sql, params = playlists_api._reorder_statement([10, 20])
    params["id"] = 1
    compiled = text(sql).compile(dialect=postgresql.dialect())
    assert set(compiled.binds) == {"t0", "t1", "id"}
    assert set(params) == {"t0", "t1", "id"}


def test_reorder_statement_coerces_ids_to_int() -> None:
    """Belt and braces alongside the CAST: the Python side sends ints too."""
    _, params = playlists_api._reorder_statement([True, 2])  # type: ignore[list-item]
    assert params == {"t0": 1, "t1": 2}
    assert all(type(v) is int for v in params.values())


def test_reorder_statement_single_track() -> None:
    sql, params = playlists_api._reorder_statement([42])
    assert params == {"t0": 42}
    assert "(VALUES (CAST(:t0 AS integer), 0)) AS v(tid, pos)" in sql


# ─── DB-free: the route emits that statement ───────────────────────────────


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _RecordingSession:
    """Stands in for the AsyncSession: answers the current-set SELECT from
    ``current_ids`` and records every statement it is asked to execute."""

    def __init__(self, current_ids: list[int]):
        self.current_ids = current_ids
        self.executed: list[tuple[str, dict[str, Any] | None]] = []

    async def execute(self, stmt, params=None):
        sql = " ".join(str(stmt.text).split())
        self.executed.append((sql, params))
        if sql.startswith("SELECT track_id FROM playlist_tracks"):
            return _Result([(tid,) for tid in self.current_ids])
        return _Result([])


@pytest.fixture
def recording(monkeypatch):
    holder: dict[str, _RecordingSession] = {}

    def _install(current_ids: list[int]) -> _RecordingSession:
        sess = _RecordingSession(current_ids)
        holder["s"] = sess

        class _Scope:
            async def __aenter__(self):
                return sess

            async def __aexit__(self, *a):
                return False

        monkeypatch.setattr(playlists_api, "session_scope", lambda: _Scope())
        return sess

    return _install


def _reorder(playlist_id: int, track_ids: list[int]) -> None:
    asyncio.run(
        playlists_api.reorder_playlist(playlist_id, PlaylistReorder(track_ids=track_ids))
    )


def test_route_executes_the_cast_statement_with_the_new_order(recording) -> None:
    sess = recording([1, 2, 3])
    _reorder(5, [3, 1, 2])

    updates = [(s, p) for s, p in sess.executed if s.startswith("UPDATE playlist_tracks")]
    assert len(updates) == 1
    sql, params = updates[0]
    assert params == {"t0": 3, "t1": 1, "t2": 2, "id": 5}
    assert [int(n) for n in _CAST_RE.findall(sql)] == [0, 1, 2]
    assert sorted(_BIND_RE.findall(sql)) == [":t0", ":t1", ":t2"]
    # And the rest of the transaction still happens, in order.
    tails = [s for s, _ in sess.executed[sess.executed.index(updates[0]) + 1:]]
    assert tails[0].startswith("UPDATE playlists SET updated_at = NOW()")
    assert "pg_notify('playlists_changed', 'reordered')" in tails[1]


def test_route_404s_an_empty_or_unknown_playlist_before_writing(recording) -> None:
    import fastapi

    sess = recording([])
    with pytest.raises(fastapi.HTTPException) as e:
        _reorder(9, [1])
    assert e.value.status_code == 404
    assert not any(s.startswith("UPDATE") for s, _ in sess.executed)


def test_route_400s_when_the_set_differs(recording) -> None:
    import fastapi

    sess = recording([1, 2, 3])
    with pytest.raises(fastapi.HTTPException) as e:
        _reorder(5, [1, 2])
    assert e.value.status_code == 400
    assert not any(s.startswith("UPDATE") for s, _ in sess.executed)


def test_route_refuses_the_favorites_virtual_playlist(recording) -> None:
    import fastapi

    sess = recording([1])
    with pytest.raises(fastapi.HTTPException) as e:
        _reorder(playlists_api.FAVORITES_VIRTUAL_ID, [1])
    assert e.value.status_code == 400
    assert sess.executed == []


# ─── DB-backed: the statement actually runs on Postgres ────────────────────


async def _seed_playlist(n: int = 3) -> tuple[int, list[int]]:
    from web.backend.db import session_scope

    async with session_scope() as s:
        pid = (
            await s.execute(text("INSERT INTO playlists (name) VALUES ('reorder-test') RETURNING id"))
        ).scalar_one()
        tids: list[int] = []
        for i in range(n):
            tid = (
                await s.execute(
                    text(
                        "INSERT INTO library_tracks (file_path, title) "
                        "VALUES (:fp, :t) RETURNING id"
                    ),
                    {"fp": f"/reorder-test/{i}.mp3", "t": f"R{i}"},
                )
            ).scalar_one()
            await s.execute(
                text(
                    "INSERT INTO playlist_tracks (playlist_id, track_id, position) "
                    "VALUES (:p, :t, :pos)"
                ),
                {"p": pid, "t": tid, "pos": i},
            )
            tids.append(tid)
    return pid, tids


async def _positions(pid: int) -> list[int]:
    from web.backend.db import session_scope

    async with session_scope() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT track_id FROM playlist_tracks WHERE playlist_id = :p "
                    "ORDER BY position"
                ),
                {"p": pid},
            )
        ).all()
    return [int(r[0]) for r in rows]


async def _cleanup(pid: int, tids: list[int]) -> None:
    from web.backend.db import session_scope

    async with session_scope() as s:
        await s.execute(text("DELETE FROM playlists WHERE id = :p"), {"p": pid})
        for tid in tids:
            await s.execute(text("DELETE FROM library_tracks WHERE id = :t"), {"t": tid})


@requires_db
def test_reorder_over_http_rewrites_positions() -> None:
    pid, tids = asyncio.run(_seed_playlist(3))
    try:
        new_order = [tids[2], tids[0], tids[1]]
        with TestClient(app, headers={"X-Requested-With": "domovoi-tests"}) as c:
            r = c.patch(f"/api/playlists/{pid}/order", json={"track_ids": new_order})
        assert r.status_code == 204, r.text
        assert asyncio.run(_positions(pid)) == new_order
    finally:
        asyncio.run(_cleanup(pid, tids))
