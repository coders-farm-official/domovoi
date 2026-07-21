"""Plugin migration runner (design §6.2): ledger, checksum drift,
SQL-lint rejections, fresh-install both-or-neither rollback, catch-up."""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text

from domovoi.db.session import engine
from domovoi.plugins_runtime.migrations import (
    MigrationChecksumError,
    MigrationError,
    PluginMigrationRunner,
    SqlLintError,
    discover_migrations,
    sql_lint,
)
from domovoi.tests.conftest import requires_db

pytestmark = [pytest.mark.asyncio, requires_db]

SLUG = "migtest"
SCHEMA = f"plugin_{SLUG}"

V001 = """
CREATE TABLE things (
    id BIGSERIAL PRIMARY KEY,
    label TEXT NOT NULL
);
CREATE INDEX things_label_idx ON things (label);
"""

V002 = """
ALTER TABLE things ADD COLUMN created_at TIMESTAMPTZ NOT NULL DEFAULT now();
"""


@pytest_asyncio.fixture
async def migdir(tmp_path: Path):
    """A migrations dir + guaranteed schema cleanup."""
    d = tmp_path / "migrations"
    d.mkdir()
    yield d
    async with engine.begin() as conn:
        await conn.execute(text(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE'))


def _runner(migdir: Path) -> PluginMigrationRunner:
    # conftest pins DATABASE_URL at the _test DB, so default_database_urls()
    # yields exactly one target — no _test_test sibling.
    return PluginMigrationRunner(SLUG, migdir)


async def _schema_exists() -> bool:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT 1 FROM information_schema.schemata "
                    "WHERE schema_name = :s"
                ),
                {"s": SCHEMA},
            )
        ).first()
    return row is not None


# ─── apply + ledger ─────────────────────────────────────────────────────────

async def test_apply_all_creates_schema_ledger_and_tables(migdir: Path) -> None:
    (migdir / "V001__init.sql").write_text(V001, encoding="utf-8")
    (migdir / "V002__created_at.sql").write_text(V002, encoding="utf-8")
    runner = _runner(migdir)
    applied = await runner.apply_all()
    (url, names), = applied.items()
    assert names == ["V001__init.sql", "V002__created_at.sql"]

    async with engine.connect() as conn:
        ledger = (
            await conn.execute(
                text(
                    f'SELECT version, filename, checksum FROM "{SCHEMA}".schema_history '
                    f"ORDER BY version"
                )
            )
        ).all()
        assert [(r.version, r.filename) for r in ledger] == [
            (1, "V001__init.sql"), (2, "V002__created_at.sql"),
        ]
        assert all(len(r.checksum) == 64 for r in ledger)
        # The migration ran with search_path preset — table landed in the
        # plugin schema, unqualified.
        cols = (
            await conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = :s AND table_name = 'things'"
                ),
                {"s": SCHEMA},
            )
        ).scalars().all()
    assert {"id", "label", "created_at"} <= set(cols)

    assert await runner.ledger_max_version() == 2

    # Idempotent catch-up: nothing new to apply.
    again = await runner.apply_all()
    assert list(again.values()) == [[]]


async def test_checksum_drift_refuses_catchup(migdir: Path) -> None:
    f = migdir / "V001__init.sql"
    f.write_text(V001, encoding="utf-8")
    runner = _runner(migdir)
    await runner.apply_all()
    # Drift: the applied file changes on disk.
    f.write_text(V001 + "\n-- sneaky edit\nCREATE TABLE extra (id INT);",
                 encoding="utf-8")
    with pytest.raises(MigrationChecksumError, match="V001__init.sql"):
        await runner.apply_all()


async def test_fresh_install_failure_drops_schema(migdir: Path) -> None:
    (migdir / "V001__init.sql").write_text(V001, encoding="utf-8")
    (migdir / "V002__broken.sql").write_text(
        "ALTER TABLE does_not_exist ADD COLUMN x INT;", encoding="utf-8"
    )
    with pytest.raises(Exception):
        await _runner(migdir).apply_all()
    # Both-or-neither on a brand-new schema (§3.2 step 10).
    assert not await _schema_exists()


async def test_catchup_failure_keeps_existing_schema(migdir: Path) -> None:
    (migdir / "V001__init.sql").write_text(V001, encoding="utf-8")
    runner = _runner(migdir)
    await runner.apply_all()
    (migdir / "V002__broken.sql").write_text(
        "ALTER TABLE does_not_exist ADD COLUMN x INT;", encoding="utf-8"
    )
    with pytest.raises(Exception):
        await runner.apply_all()
    # Pre-existing schema (may hold user data) survives; V001 stays applied.
    assert await _schema_exists()
    assert await runner.ledger_max_version() == 1


async def test_drop_schema(migdir: Path) -> None:
    (migdir / "V001__init.sql").write_text(V001, encoding="utf-8")
    runner = _runner(migdir)
    await runner.apply_all()
    assert await _schema_exists()
    await runner.drop_schema()
    assert not await _schema_exists()
    assert await runner.ledger_max_version() == 0


# ─── discovery lint ─────────────────────────────────────────────────────────

def test_discover_rejects_gaps(tmp_path: Path) -> None:
    d = tmp_path / "m"
    d.mkdir()
    (d / "V001__a.sql").write_text("SELECT 1;", encoding="utf-8")
    (d / "V003__c.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(MigrationError, match="gapless"):
        discover_migrations(d)


def test_discover_rejects_bad_filenames(tmp_path: Path) -> None:
    d = tmp_path / "m"
    d.mkdir()
    (d / "V1__a.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(MigrationError, match="V###__name.sql"):
        discover_migrations(d)


# ─── SQL lint (§6.2 — tripwire) ─────────────────────────────────────────────

@pytest.mark.parametrize(
    "sql,fragment",
    [
        ("CREATE SCHEMA sneaky;", "CREATE SCHEMA"),
        ("create extension pg_trgm;", "CREATE EXTENSION"),
        ("CREATE TABLE public.hijack (id INT);", "public"),
        ("ALTER TABLE public.library_tracks ADD COLUMN x INT;", "public"),
        ("DROP TABLE public.playlists;", "public"),
        (
            "CREATE TABLE t (x BIGINT REFERENCES public.playlists(id));",
            "REFERENCES",
        ),
        (
            "CREATE TABLE t (x BIGINT REFERENCES plugin_other.stuff(id));",
            "plugin_other",
        ),
        ("SELECT * FROM plugin_radio.radio_stations;", "plugin_radio"),
        # The runner owns search_path; a migration resetting it can slip
        # unqualified DDL into another schema (§6.2).
        ("SET search_path = public; CREATE TABLE t (id INT);", "search_path"),
        ("SET LOCAL search_path TO public;", "search_path"),
        ("set session search_path = public, pg_catalog;", "search_path"),
    ],
)
def test_sql_lint_rejections(sql: str, fragment: str) -> None:
    violations = sql_lint(sql, SLUG)
    assert violations, f"lint missed: {sql!r}"
    assert any(fragment.lower() in v.lower() for v in violations)


def test_sql_lint_allows_own_schema_and_comments() -> None:
    ok = f"""
    -- a comment mentioning public.playlists and CREATE SCHEMA is fine
    CREATE TABLE detections (
        id BIGSERIAL PRIMARY KEY,
        station_id BIGINT NOT NULL REFERENCES plugin_{SLUG}.things(id),
        note TEXT DEFAULT 'says public. in a string'
    );
    """
    assert sql_lint(ok, SLUG) == []


async def test_lint_runs_inside_apply_all(migdir: Path) -> None:
    (migdir / "V001__evil.sql").write_text(
        "CREATE TABLE public.hijack (id INT);", encoding="utf-8"
    )
    with pytest.raises(SqlLintError, match="V001__evil.sql"):
        await _runner(migdir).apply_all()
    assert not await _schema_exists()


async def test_set_search_path_migration_rejected_by_apply_all(migdir: Path) -> None:
    """A migration that resets search_path (to smuggle unqualified DDL into
    the public schema) is refused before any SQL runs (§6.2)."""
    (migdir / "V001__reset.sql").write_text(
        "SET search_path = public;\nCREATE TABLE hijack (id INT);",
        encoding="utf-8",
    )
    with pytest.raises(SqlLintError, match="search_path"):
        await _runner(migdir).apply_all()
    assert not await _schema_exists()
