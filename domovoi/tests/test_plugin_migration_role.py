"""Plugin migrations run as a per-plugin NOLOGIN role with the search
path pinned to the plugin schema (design §6.2, PLG-3).

Two tiers:

* DB-free — the extended SQL lint, and the exact statement sequence the
  runner issues per file (recorded from a fake asyncpg connection):
  ``SET LOCAL search_path = "plugin_<slug>"`` with NO ``public``, then
  ``SET LOCAL ROLE plugin_<slug>``, the file, ``RESET ROLE``, the ledger
  row. These never hide behind ``requires_db``.
* DB-backed — an unqualified statement naming a core table fails and the
  core rows are untouched; explicit ``public.`` DML and ``COPY … TO
  PROGRAM`` are refused by Postgres even when the lint is bypassed; the
  objects a file creates are owned by the plugin role; a catch-up on a
  schema an earlier runner created as the application user still works.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text

from domovoi.db.session import engine
from domovoi.plugins_runtime.migrations import (
    MigrationFile,
    PluginMigrationRunner,
    SqlLintError,
    sql_lint,
)
from domovoi.tests.conftest import requires_db

SLUG = "migrole"
SCHEMA = f"plugin_{SLUG}"
ROLE = f"plugin_{SLUG}"


# ─── the lint (DB-free) ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "sql,fragment",
    [
        ("COPY (SELECT 1) TO PROGRAM 'id';", "COPY"),
        ("COPY things FROM '/etc/hostname';", "COPY"),
        ("copy things to stdout;", "COPY"),
        ("DO $$ BEGIN PERFORM 1; END $$;", "DO blocks"),
        ("do $x$ begin null; end $x$ language plpgsql;", "DO blocks"),
        ("ALTER SYSTEM SET work_mem = '1GB';", "ALTER SYSTEM"),
        ("CREATE ROLE sneaky LOGIN SUPERUSER;", "ROLE"),
        ("create user sneaky;", "ROLE"),
        ("ALTER ROLE domovoi SUPERUSER;", "ROLE"),
        ("SET ROLE domovoi;", "SET/RESET ROLE"),
        ("set local role postgres;", "SET/RESET ROLE"),
        ("RESET ROLE;", "SET/RESET ROLE"),
        ("SET SESSION AUTHORIZATION domovoi;", "SESSION AUTHORIZATION"),
        ("RESET ALL;", "RESET ALL"),
        ("LOAD 'evil';", "LOAD"),
        ("SELECT set_config('search_path', 'public', true);", "set_config"),
        ("SELECT pg_catalog.set_config('search_path', 'public', false);", "set_config"),
        ("DELETE FROM public.admin_sessions;", "DML against the public schema"),
        ("INSERT INTO public.plugins (slug) VALUES ('x');", "DML against the public schema"),
        ("UPDATE public.admin_auth SET password_hash = 'x';", "DML against the public schema"),
        ("TRUNCATE public.intents_log;", "DML against the public schema"),
        ("truncate table only public.intents_log;", "DML against the public schema"),
        ('DROP TABLE "public".plugins;', "public"),
        ("CREATE TABLE t (id INT); DELETE FROM public.people;", "DML against the public schema"),
        # A forbidden statement after an innocent one is still found.
        ("CREATE TABLE t (id INT);\nCOPY t TO PROGRAM 'id';", "COPY"),
    ],
)
def test_sql_lint_rejects_statements_outside_the_plugin_schema(sql: str, fragment: str) -> None:
    violations = sql_lint(sql, SLUG)
    assert violations, f"lint missed: {sql!r}"
    assert any(fragment.lower() in v.lower() for v in violations), violations


@pytest.mark.parametrize(
    "sql",
    [
        # DML in the plugin's own schema is a normal migration step.
        "CREATE TABLE things (id INT); INSERT INTO things VALUES (1);",
        "UPDATE things SET id = 2 WHERE id = 1;",
        "DELETE FROM things WHERE id = 2;",
        "TRUNCATE things;",
        # Column and identifier names that merely resemble keywords.
        "CREATE TABLE t (copy INT, do_it BOOLEAN, load_at TIMESTAMPTZ, role_name TEXT);",
        "CREATE INDEX t_copy_idx ON t (copy);",
        "ALTER TABLE t ADD COLUMN copy_count INT;",
        # Extension operators live in public; referencing them is fine.
        "CREATE INDEX t_name_trgm ON t USING gin (role_name public.gin_trgm_ops);",
        # Prose in comments and literals is not code.
        "-- COPY this file elsewhere; DO not edit\nCREATE TABLE t (note TEXT DEFAULT 'set role none');",
        "COMMENT ON TABLE t IS 'ALTER SYSTEM is mentioned here';",
    ],
)
def test_sql_lint_allows_own_schema_work(sql: str) -> None:
    assert sql_lint(sql, SLUG) == [], sql_lint(sql, SLUG)


# ─── the statement sequence (DB-free, fake connection) ────────────────────


class FakeDriver:
    """Records every statement; answers the runner's catalogue reads."""

    def __init__(
        self,
        *,
        ledger: dict[int, tuple[str, str]] | None = None,
        role_exists: bool = True,
        superuser: bool = True,
        member: bool = False,
        foreign_relations: list[tuple[str, str]] | None = None,
    ) -> None:
        self.calls: list[str] = []
        self.ledger = ledger or {}
        self.role_exists = role_exists
        self.superuser = superuser
        self.member = member
        self.foreign_relations = foreign_relations or []

    async def execute(self, sql: str, *args: Any) -> None:
        self.calls.append(sql)

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.calls.append(sql)
        if "FROM pg_class" in sql:
            return [{"relname": n, "relkind": k} for n, k in self.foreign_relations]
        if f'FROM "{SCHEMA}".schema_history' in sql:
            return [
                {"version": v, "filename": f, "checksum": c}
                for v, (f, c) in self.ledger.items()
            ]
        return []

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        self.calls.append(sql)
        if "pg_roles WHERE rolname = $1" in sql:
            return {"?column?": 1} if self.role_exists else None
        if "current_user AS who" in sql:
            return {"who": "domovoi", "super": self.superuser, "member": self.member}
        return None


def _file(version: int, sql: str) -> MigrationFile:
    import hashlib

    name = f"V{version:03d}__t.sql"
    return MigrationFile(
        version=version, filename=name, path=Path(name),
        checksum=hashlib.sha256(sql.encode()).hexdigest(), sql=sql,
    )


def _runner() -> PluginMigrationRunner:
    return PluginMigrationRunner(SLUG, Path("unused"), database_urls=["postgresql://x/y_test"])


@pytest.mark.asyncio
async def test_each_file_runs_after_set_local_role_with_the_schema_only_path() -> None:
    drv = FakeDriver()
    sql = "CREATE TABLE things (id INT);"
    applied = await _runner()._apply_files(drv, [_file(1, sql)])
    assert applied == ["V001__t.sql"]
    i = drv.calls.index("BEGIN")
    assert drv.calls[i:i + 7] == [
        "BEGIN",
        f'SET LOCAL search_path = "{SCHEMA}"',      # the plugin schema ONLY
        f'SET LOCAL ROLE "{ROLE}"',
        sql,
        "RESET ROLE",
        f'INSERT INTO "{SCHEMA}".schema_history (version, filename, checksum) '
        "VALUES ($1, $2, $3)",
        "COMMIT",
    ]
    assert not any("public" in c for c in drv.calls if c.startswith("SET LOCAL search_path"))


@pytest.mark.asyncio
async def test_role_is_created_once_and_granted_its_two_schemas() -> None:
    drv = FakeDriver(role_exists=False)
    await _runner()._apply_files(drv, [_file(1, "SELECT 1;")])
    assert f'CREATE ROLE "{ROLE}" NOLOGIN' in drv.calls
    assert f'GRANT USAGE ON SCHEMA public TO "{ROLE}"' in drv.calls
    assert f'GRANT ALL ON SCHEMA "{SCHEMA}" TO "{ROLE}"' in drv.calls
    # Every grant precedes the first file's transaction.
    assert drv.calls.index(f'GRANT ALL ON SCHEMA "{SCHEMA}" TO "{ROLE}"') < drv.calls.index("BEGIN")

    again = FakeDriver(role_exists=True)
    await _runner()._apply_files(again, [_file(1, "SELECT 1;")])
    assert not any(c.startswith("CREATE ROLE") for c in again.calls)


@pytest.mark.asyncio
async def test_a_non_superuser_application_user_is_made_a_member() -> None:
    drv = FakeDriver(superuser=False, member=False)
    await _runner()._apply_files(drv, [_file(1, "SELECT 1;")])
    assert f'GRANT "{ROLE}" TO CURRENT_USER' in drv.calls

    already = FakeDriver(superuser=False, member=True)
    await _runner()._apply_files(already, [_file(1, "SELECT 1;")])
    assert f'GRANT "{ROLE}" TO CURRENT_USER' not in already.calls

    root = FakeDriver(superuser=True, member=False)
    await _runner()._apply_files(root, [_file(1, "SELECT 1;")])
    assert f'GRANT "{ROLE}" TO CURRENT_USER' not in root.calls


@pytest.mark.asyncio
async def test_objects_left_by_an_earlier_runner_are_re_owned_before_catchup() -> None:
    drv = FakeDriver(
        ledger={1: ("V001__t.sql", _file(1, "CREATE TABLE things (id INT);").checksum)},
        foreign_relations=[("things", "r"), ("things_id_seq", "S"), ("v", "v")],
    )
    await _runner()._apply_files(
        drv, [_file(1, "CREATE TABLE things (id INT);"), _file(2, "ALTER TABLE things ADD COLUMN x INT;")]
    )
    assert f'ALTER TABLE "{SCHEMA}"."things" OWNER TO "{ROLE}"' in drv.calls
    assert f'ALTER SEQUENCE "{SCHEMA}"."things_id_seq" OWNER TO "{ROLE}"' in drv.calls
    assert f'ALTER VIEW "{SCHEMA}"."v" OWNER TO "{ROLE}"' in drv.calls
    assert drv.calls.index(f'ALTER TABLE "{SCHEMA}"."things" OWNER TO "{ROLE}"') < drv.calls.index("BEGIN")
    # Only V002 ran.
    assert drv.calls.count("BEGIN") == 1


@pytest.mark.asyncio
async def test_nothing_pending_touches_no_role_and_no_ownership() -> None:
    sql = "CREATE TABLE things (id INT);"
    drv = FakeDriver(ledger={1: ("V001__t.sql", _file(1, sql).checksum)}, role_exists=False)
    assert await _runner()._apply_files(drv, [_file(1, sql)]) == []
    assert not any("ROLE" in c or "GRANT" in c or "OWNER TO" in c for c in drv.calls)


@pytest.mark.asyncio
async def test_a_failing_file_rolls_back_and_the_role_is_reset_by_the_transaction() -> None:
    class Boom(FakeDriver):
        async def execute(self, sql: str, *args: Any) -> None:
            await super().execute(sql, *args)
            if sql.startswith("DELETE"):
                raise RuntimeError("relation does not exist")

    drv = Boom()
    with pytest.raises(RuntimeError):
        await _runner()._apply_files(drv, [_file(1, "DELETE FROM admin_sessions;")])
    assert drv.calls[-1] == "ROLLBACK"
    assert "COMMIT" not in drv.calls


# ─── against Postgres ─────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def migdir(tmp_path: Path):
    d = tmp_path / "migrations"
    d.mkdir()
    yield d
    async with engine.begin() as conn:
        await conn.execute(text(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE'))
        await conn.execute(text("DELETE FROM admin_sessions WHERE label = 'migrole-probe'"))
    async with engine.connect() as conn:
        raw = await conn.get_raw_connection()
        await raw.driver_connection.execute(f'DROP ROLE IF EXISTS "{ROLE}"')


async def _probe_rows() -> int:
    async with engine.connect() as conn:
        return (
            await conn.execute(
                text("SELECT count(*) FROM admin_sessions WHERE label = 'migrole-probe'")
            )
        ).scalar_one()


async def _seed_probe_row() -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO admin_sessions (token_hash, label, expires_at) "
                "VALUES ('migrole-probe-hash', 'migrole-probe', now() + interval '1 day')"
            )
        )


async def _schema_exists() -> bool:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT 1 FROM information_schema.schemata WHERE schema_name = :s"),
                {"s": SCHEMA},
            )
        ).first()
    return row is not None


@requires_db
@pytest.mark.asyncio
async def test_unqualified_core_table_name_fails_and_core_rows_survive(migdir: Path) -> None:
    """``DELETE FROM admin_sessions`` inside a plugin migration names a
    core table without a schema. With the search path pinned to the
    plugin schema it is "relation does not exist", the file rolls back,
    the fresh schema is dropped, and the core row is untouched."""
    await _seed_probe_row()
    (migdir / "V001__reach.sql").write_text(
        "CREATE TABLE things (id INT);\nDELETE FROM admin_sessions;\n", encoding="utf-8"
    )
    with pytest.raises(Exception) as exc:
        await PluginMigrationRunner(SLUG, migdir).apply_all()
    assert "admin_sessions" in str(exc.value) and "does not exist" in str(exc.value)
    assert await _probe_rows() == 1
    assert not await _schema_exists()


@requires_db
@pytest.mark.asyncio
async def test_explicit_public_dml_is_refused_by_the_role_even_without_the_lint(migdir: Path) -> None:
    await _seed_probe_row()
    runner = PluginMigrationRunner(SLUG, migdir)
    files = [_file(1, "DELETE FROM public.admin_sessions;")]
    with pytest.raises(Exception) as exc:
        await runner._apply_to(runner.database_urls[0], files)     # lint bypassed on purpose
    assert "permission denied" in str(exc.value)
    assert await _probe_rows() == 1


@requires_db
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sql",
    [
        "COPY (SELECT 1) TO PROGRAM 'true';",
        "ALTER SYSTEM SET work_mem = '64MB';",
        "CREATE ROLE migrole_sneaky NOLOGIN;",
        "DROP TABLE public.plugins;",
    ],
)
async def test_privileged_statements_are_refused_by_postgres_for_the_plugin_role(
    migdir: Path, sql: str
) -> None:
    runner = PluginMigrationRunner(SLUG, migdir)
    with pytest.raises(Exception) as exc:
        await runner._apply_to(runner.database_urls[0], [_file(1, sql)])   # lint bypassed
    msg = str(exc.value).lower()
    assert "permission denied" in msg or "must be" in msg or "cannot run inside" in msg, msg


@requires_db
@pytest.mark.asyncio
async def test_migration_objects_are_owned_by_the_plugin_role(migdir: Path) -> None:
    (migdir / "V001__who.sql").write_text(
        "CREATE TABLE whoami AS SELECT current_user::text AS who;\n"
        "CREATE TABLE things (id BIGSERIAL PRIMARY KEY, label TEXT);\n",
        encoding="utf-8",
    )
    runner = PluginMigrationRunner(SLUG, migdir)
    (names,) = (await runner.apply_all()).values()
    assert names == ["V001__who.sql"]
    async with engine.connect() as conn:
        who = (await conn.execute(text(f'SELECT who FROM "{SCHEMA}".whoami'))).scalar_one()
        owners = dict(
            (
                await conn.execute(
                    text(
                        "SELECT tablename, tableowner FROM pg_tables WHERE schemaname = :s"
                    ),
                    {"s": SCHEMA},
                )
            ).all()
        )
        # The application user still reads and writes what the role made.
        await conn.execute(text(f'INSERT INTO "{SCHEMA}".things (label) VALUES (\'x\')'))
        n = (await conn.execute(text(f'SELECT count(*) FROM "{SCHEMA}".things'))).scalar_one()
    assert who == ROLE
    assert owners["whoami"] == ROLE and owners["things"] == ROLE
    assert owners["schema_history"] != ROLE        # the ledger stays the runner's
    assert n == 1
    assert await runner.ledger_max_version() == 1


@requires_db
@pytest.mark.asyncio
async def test_catchup_over_a_schema_an_earlier_runner_owned(migdir: Path) -> None:
    """Simulate a schema created before the role existed: table + ledger
    row owned by the application user. The next file must still be able
    to ALTER that table (it is re-owned first) and land as the role."""
    v1 = "CREATE TABLE things (id INT);\n"
    (migdir / "V001__init.sql").write_text(v1, encoding="utf-8")
    (migdir / "V002__more.sql").write_text(
        "ALTER TABLE things ADD COLUMN x INT;\nCREATE INDEX things_x ON things (x);\n",
        encoding="utf-8",
    )
    import hashlib

    async with engine.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{SCHEMA}"'))
        await conn.execute(text(f'CREATE TABLE "{SCHEMA}".things (id INT)'))
        await conn.execute(
            text(
                f'CREATE TABLE "{SCHEMA}".schema_history (version INT PRIMARY KEY, '
                "filename TEXT NOT NULL, checksum TEXT NOT NULL, "
                "applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
        )
        await conn.execute(
            text(
                f'INSERT INTO "{SCHEMA}".schema_history (version, filename, checksum) '
                "VALUES (1, 'V001__init.sql', :c)"
            ),
            {"c": hashlib.sha256(v1.encode()).hexdigest()},
        )
    runner = PluginMigrationRunner(SLUG, migdir)
    (names,) = (await runner.apply_all()).values()
    assert names == ["V002__more.sql"]
    async with engine.connect() as conn:
        owner = (
            await conn.execute(
                text("SELECT tableowner FROM pg_tables WHERE schemaname = :s AND tablename = 'things'"),
                {"s": SCHEMA},
            )
        ).scalar_one()
        cols = (
            await conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = :s AND table_name = 'things'"
                ),
                {"s": SCHEMA},
            )
        ).scalars().all()
    assert owner == ROLE
    assert set(cols) == {"id", "x"}


@requires_db
@pytest.mark.asyncio
async def test_purge_drops_the_role_with_the_schema(migdir: Path) -> None:
    (migdir / "V001__init.sql").write_text("CREATE TABLE things (id INT);", encoding="utf-8")
    runner = PluginMigrationRunner(SLUG, migdir)
    await runner.apply_all()
    await runner.drop_schema()
    assert not await _schema_exists()
    async with engine.connect() as conn:
        row = (
            await conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": ROLE})
        ).first()
    assert row is None
