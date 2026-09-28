"""Catch-up over a plugin schema created before per-plugin roles (PLG-3).

Before PLG-3 every plugin migration ran as the application user, so an
installed plugin's tables — and the sequences behind their ``SERIAL``,
``BIGSERIAL`` and ``IDENTITY`` columns — are owned by that user. The
first catch-up after PLG-3 re-owns them to the plugin role before the new
file runs. Postgres refuses ``ALTER SEQUENCE … OWNER`` on a sequence a
column owns (the table's ``ALTER TABLE … OWNER`` carries it along), so
the adopt step has to leave those out — otherwise the plugin's next
migration fails, a bundled plugin drops off the dashboard, and an
external upgrade rolls back.

These tests build that pre-PLG-3 shape on real Postgres, as the
application user, with no plugin role in existence, then run a catch-up.
A fake connection can't stand in here: the refusal is Postgres's.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from domovoi.db.session import engine
from domovoi.plugins_runtime.migrations import PluginMigrationRunner
from domovoi.tests.conftest import requires_db

pytestmark = [pytest.mark.asyncio, requires_db]

# Its own slug: roles are cluster-wide, and the other plugin-migration
# modules create and drop theirs.
SLUG = "migadopt"
SCHEMA = f"plugin_{SLUG}"
ROLE = f"plugin_{SLUG}"

# Every kind of object the adopt step re-owns, plus all three ways a
# column comes to own a sequence (SERIAL, BIGSERIAL, IDENTITY) and one
# free-standing sequence that must be re-owned on its own.
V001 = """
CREATE TABLE stations (id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE plays (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    station_id BIGINT NOT NULL REFERENCES stations(id)
);
CREATE TABLE tags (id SERIAL PRIMARY KEY, label TEXT);
CREATE SEQUENCE ticket_seq;
CREATE VIEW station_names AS SELECT name FROM stations;
CREATE TYPE mood AS ENUM ('calm', 'loud');
CREATE FUNCTION station_count() RETURNS BIGINT LANGUAGE sql
    AS 'SELECT count(*) FROM stations';
"""
V002 = """
ALTER TABLE stations ADD COLUMN url TEXT;
ALTER TABLE plays ADD COLUMN at TIMESTAMPTZ NOT NULL DEFAULT now();
"""
V003 = """
CREATE INDEX plays_at_idx ON plays (at);
ALTER TABLE tags ADD COLUMN mood mood;
"""
FILES = {1: ("V001__init.sql", V001), 2: ("V002__more.sql", V002), 3: ("V003__index.sql", V003)}


async def _driver_do(fn):
    async with engine.connect() as conn:
        raw = await conn.get_raw_connection()
        return await fn(raw.driver_connection)


async def _drop_everything() -> None:
    async def go(drv) -> None:
        await drv.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
        if await drv.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", ROLE):
            # The runner's USAGE-on-public grant is a dependent object;
            # DROP OWNED BY revokes it so DROP ROLE can go through.
            await drv.execute(f'DROP OWNED BY "{ROLE}"')
            await drv.execute(f'DROP ROLE IF EXISTS "{ROLE}"')

    await _driver_do(go)


@pytest_asyncio.fixture
async def migdir(tmp_path: Path):
    await _drop_everything()          # a crashed earlier run must not leak in
    d = tmp_path / "migrations"
    d.mkdir()
    yield d
    await _drop_everything()


def _write(migdir: Path, upto: int) -> None:
    for v in range(1, upto + 1):
        name, sql = FILES[v]
        (migdir / name).write_text(sql, encoding="utf-8")


async def _build_pre_role_schema(applied: int) -> None:
    """What the pre-PLG-3 runner left behind: schema, objects and ledger
    all owned by the application user, files 1..``applied`` recorded."""

    async def go(drv) -> None:
        assert not await drv.fetchval(
            "SELECT 1 FROM pg_roles WHERE rolname = $1", ROLE
        ), "the plugin role must not exist yet — this is the pre-PLG-3 shape"
        await drv.execute(f'CREATE SCHEMA "{SCHEMA}"')
        await drv.execute(
            f'CREATE TABLE "{SCHEMA}".schema_history (version INT PRIMARY KEY, '
            "filename TEXT NOT NULL, checksum TEXT NOT NULL, "
            "applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        for v in range(1, applied + 1):
            name, sql = FILES[v]
            await drv.execute("BEGIN")
            await drv.execute(f'SET LOCAL search_path = "{SCHEMA}"')
            await drv.execute(sql)
            await drv.execute(
                f'INSERT INTO "{SCHEMA}".schema_history (version, filename, checksum) '
                "VALUES ($1, $2, $3)",
                v, name, hashlib.sha256(sql.encode("utf-8")).hexdigest(),
            )
            await drv.execute("COMMIT")

    await _driver_do(go)


async def _owners() -> dict[str, str]:
    """Owner of every relation (tables, views, sequences, indexes), type
    and routine in the plugin schema, keyed by name."""

    async def go(drv) -> dict[str, str]:
        rels = await drv.fetch(
            "SELECT c.relname AS name, pg_get_userbyid(c.relowner) AS owner "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = $1 AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f', 'i')",
            SCHEMA,
        )
        types = await drv.fetch(
            "SELECT t.typname AS name, pg_get_userbyid(t.typowner) AS owner "
            "FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace "
            "WHERE n.nspname = $1 AND t.typtype = 'e'",
            SCHEMA,
        )
        procs = await drv.fetch(
            "SELECT p.proname AS name, pg_get_userbyid(p.proowner) AS owner "
            "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = $1",
            SCHEMA,
        )
        return {r["name"]: r["owner"] for r in [*rels, *types, *procs]}

    return await _driver_do(go)


def _plugin_objects(owners: dict[str, str]) -> dict[str, str]:
    """Everything but the runner's own ledger (and its index)."""
    return {k: v for k, v in owners.items() if not k.startswith("schema_history")}


async def _ledger() -> list[int]:
    async def go(drv) -> list[int]:
        rows = await drv.fetch(
            f'SELECT version FROM "{SCHEMA}".schema_history ORDER BY version'
        )
        return [r["version"] for r in rows]

    return await _driver_do(go)


# The column-owned sequences, by the names Postgres gives them.
COLUMN_SEQUENCES = {"stations_id_seq", "plays_id_seq", "tags_id_seq"}


@pytest.mark.parametrize(
    "pre_applied",
    [
        1,   # V001 as the app user, V002 is the first file under the role
        2,   # the long-running install: V001+V002 pre-role, V003 pending
    ],
)
async def test_catchup_over_a_pre_role_schema_with_serial_and_identity(
    migdir: Path, pre_applied: int
) -> None:
    await _build_pre_role_schema(pre_applied)
    before = _plugin_objects(await _owners())
    assert COLUMN_SEQUENCES | {"ticket_seq", "stations", "plays", "tags"} <= set(before)
    assert ROLE not in before.values()

    _write(migdir, pre_applied + 1)
    runner = PluginMigrationRunner(SLUG, migdir)
    (names,) = (await runner.apply_all()).values()

    assert names == [FILES[pre_applied + 1][0]]
    assert await _ledger() == list(range(1, pre_applied + 2))
    after = _plugin_objects(await _owners())
    wrong = {name: owner for name, owner in after.items() if owner != ROLE}
    assert not wrong, f"not re-owned to {ROLE}: {wrong}"
    # The column-owned sequences moved with their tables, and the
    # free-standing one was re-owned on its own.
    assert COLUMN_SEQUENCES | {"ticket_seq"} <= set(after)
    # The ledger stays the runner's.
    assert (await _owners())["schema_history"] != ROLE

    # The role can use what it now owns: every id default draws from a
    # sequence the role owns.
    async def insert_as_role(drv) -> int:
        await drv.execute("BEGIN")
        try:
            await drv.execute(f'SET LOCAL ROLE "{ROLE}"')
            await drv.execute(f'SET LOCAL search_path = "{SCHEMA}"')
            sid = await drv.fetchval("INSERT INTO stations (name) VALUES ('x') RETURNING id")
            await drv.execute("INSERT INTO plays (station_id) VALUES ($1)", sid)
            await drv.execute("INSERT INTO tags (label) VALUES ('t')")
            n = await drv.fetchval("SELECT station_count()")
        finally:
            await drv.execute("ROLLBACK")
        return n

    assert await _driver_do(insert_as_role) == 1


async def test_catchup_over_a_pre_role_schema_is_idempotent(migdir: Path) -> None:
    await _build_pre_role_schema(1)
    _write(migdir, 2)
    runner = PluginMigrationRunner(SLUG, migdir)
    (first,) = (await runner.apply_all()).values()
    assert first == ["V002__more.sql"]
    owned = await _owners()

    # Nothing pending: nothing runs, nothing changes.
    (again,) = (await runner.apply_all()).values()
    assert again == []
    assert await _owners() == owned

    # The adopt step itself, run twice more over a schema the role already
    # owns, is a clean no-op (it runs whenever a later file is pending).
    async def adopt_twice(drv) -> None:
        await runner._adopt_schema_objects(drv)
        await runner._adopt_schema_objects(drv)

    await _driver_do(adopt_twice)
    assert await _owners() == owned

    # And the next migration still lands.
    _write(migdir, 3)
    (third,) = (await runner.apply_all()).values()
    assert third == ["V003__index.sql"]
    assert await _ledger() == [1, 2, 3]
    wrong = {n: o for n, o in _plugin_objects(await _owners()).items() if o != ROLE}
    assert not wrong, wrong


class _FailOnSecondReown:
    """Pass-through to a real asyncpg connection that fails the second
    ``OWNER TO`` statement — a mid-step failure on real Postgres."""

    def __init__(self, drv: Any) -> None:
        self._drv = drv
        self.reowns = 0

    async def execute(self, sql: str, *args: Any):
        if "OWNER TO" in sql:
            self.reowns += 1
            if self.reowns == 2:
                raise RuntimeError("simulated failure mid-adopt")
        return await self._drv.execute(sql, *args)

    async def fetch(self, sql: str, *args: Any):
        return await self._drv.fetch(sql, *args)

    async def fetchrow(self, sql: str, *args: Any):
        return await self._drv.fetchrow(sql, *args)

    async def fetchval(self, sql: str, *args: Any):
        return await self._drv.fetchval(sql, *args)


async def test_a_failed_adopt_leaves_ownership_unchanged(migdir: Path) -> None:
    """The adopt step is one transaction: when a re-own fails part-way,
    the ones before it are rolled back too, so the schema is never left
    half the application user's and half the role's."""
    await _build_pre_role_schema(1)
    before = await _owners()
    runner = PluginMigrationRunner(SLUG, migdir)

    async def go(drv) -> None:
        await runner._ensure_role(drv)
        proxy = _FailOnSecondReown(drv)
        with pytest.raises(RuntimeError, match="simulated failure mid-adopt"):
            await runner._adopt_schema_objects(proxy)
        assert proxy.reowns == 2
        # The connection is usable again (the transaction was closed).
        assert await drv.fetchval("SELECT 1") == 1

    await _driver_do(go)
    assert await _owners() == before
