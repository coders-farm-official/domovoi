"""A migration file that ends the runner's transaction, or leaves its role
or search path, is not recorded as applied (design §6.2, PLG-3).

Each file runs in one transaction as ``plugin_<slug>`` with the search
path pinned to ``plugin_<slug>``. A ``COMMIT`` inside the file ends that
transaction, and the ``SET LOCAL`` role and path end with it: the rest of
the file runs as the application user in ``public``. The lint refuses
transaction control; these tests bypass it (``_apply_to`` directly) to
prove the runner's own check catches the escape on real Postgres, rolls
back what is still open, and writes no ledger row. The lint side is
covered DB-free in ``test_plugin_migration_role.py``.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import pytest_asyncio

from domovoi.db.session import engine
from domovoi.plugins_runtime.migrations import (
    MigrationFile,
    MigrationSandboxError,
    PluginMigrationRunner,
    SqlLintError,
    sql_lint,
)
from domovoi.tests.conftest import requires_db

pytestmark = [pytest.mark.asyncio, requires_db]

# Its own slug: roles are cluster-wide, and the other plugin-migration
# modules create and drop theirs.
SLUG = "migguard"
SCHEMA = f"plugin_{SLUG}"
ROLE = f"plugin_{SLUG}"
# Where escaped statements land: public, as the application user.
PROBES = ("migguard_escape_probe", "migguard_tail_probe", "migguard_quote_probe")


async def _driver_do(fn):
    async with engine.connect() as conn:
        raw = await conn.get_raw_connection()
        return await fn(raw.driver_connection)


async def _clean() -> None:
    async def go(drv) -> None:
        for probe in PROBES:
            await drv.execute(f'DROP TABLE IF EXISTS public."{probe}"')
        await drv.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
        if await drv.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", ROLE):
            await drv.execute(f'DROP OWNED BY "{ROLE}"')
            await drv.execute(f'DROP ROLE IF EXISTS "{ROLE}"')

    await _driver_do(go)


@pytest_asyncio.fixture
async def migdir(tmp_path: Path):
    await _clean()
    d = tmp_path / "migrations"
    d.mkdir()
    yield d
    await _clean()


def _file(sql: str) -> MigrationFile:
    return MigrationFile(
        version=1, filename="V001__t.sql", path=Path("V001__t.sql"),
        checksum=hashlib.sha256(sql.encode()).hexdigest(), sql=sql,
    )


async def _where(name: str) -> tuple[str, str] | None:
    """(schema, owner) of a table by name in the plugin schema or public,
    or None."""

    async def go(drv):
        row = await drv.fetchrow(
            "SELECT schemaname, tableowner FROM pg_tables "
            "WHERE tablename = $1 AND schemaname IN ($2, 'public')",
            name, SCHEMA,
        )
        return None if row is None else (row["schemaname"], row["tableowner"])

    return await _driver_do(go)


async def _ledger() -> list[int]:
    async def go(drv) -> list[int]:
        rows = await drv.fetch(f'SELECT version FROM "{SCHEMA}".schema_history')
        return sorted(r["version"] for r in rows)

    return await _driver_do(go)


async def test_a_commit_mid_file_is_caught_and_not_recorded(migdir: Path) -> None:
    """The tail after the COMMIT has already run as the application user
    in public (autocommit — nothing can take it back). The runner says so
    and does not record the file, so nobody believes it was applied
    inside the sandbox."""
    sql = (
        "CREATE TABLE inside_t (id INT);\n"
        "COMMIT;\n"
        f"CREATE TABLE {PROBES[0]} (id INT);\n"
    )
    runner = PluginMigrationRunner(SLUG, migdir)
    with pytest.raises(MigrationSandboxError, match="ended the runner's transaction") as exc:
        await runner._apply_to(runner.database_urls[0], [_file(sql)])   # lint bypassed
    assert "V001__t.sql" in str(exc.value)
    assert await _ledger() == []
    # Before the COMMIT: inside the sandbox. After it: the escape the
    # check exists to report.
    assert await _where("inside_t") == (SCHEMA, ROLE)
    schema, owner = await _where(PROBES[0])
    assert schema == "public" and owner != ROLE


async def test_what_is_still_open_after_an_escape_is_rolled_back(migdir: Path) -> None:
    """COMMIT; BEGIN; … — the new transaction is still open when the file
    ends, so the runner's ROLLBACK takes the escaped tail back."""
    sql = (
        "CREATE TABLE inside_t (id INT);\n"
        "COMMIT;\n"
        "BEGIN;\n"
        f"CREATE TABLE {PROBES[1]} (id INT);\n"
    )
    runner = PluginMigrationRunner(SLUG, migdir)
    with pytest.raises(MigrationSandboxError):
        await runner._apply_to(runner.database_urls[0], [_file(sql)])   # lint bypassed
    assert await _where(PROBES[1]) is None
    assert await _ledger() == []


@pytest.mark.parametrize(
    "tail,fragment",
    [
        ("RESET search_path;", "search_path"),
        ("RESET ROLE;", "finished as"),
    ],
)
async def test_leaving_the_role_or_path_rolls_the_whole_file_back(
    migdir: Path, tail: str, fragment: str
) -> None:
    sql = f"CREATE TABLE kept_t (id INT);\n{tail}\n"
    runner = PluginMigrationRunner(SLUG, migdir)
    with pytest.raises(MigrationSandboxError, match=fragment):
        await runner._apply_to(runner.database_urls[0], [_file(sql)])   # lint bypassed
    assert await _where("kept_t") is None
    assert await _ledger() == []


async def test_apply_all_refuses_a_commit_before_running_anything(migdir: Path) -> None:
    (migdir / "V001__wrapped.sql").write_text(
        "BEGIN;\nCREATE TABLE wrapped_t (id INT);\nCOMMIT;\n", encoding="utf-8"
    )
    with pytest.raises(SqlLintError, match="transaction control"):
        await PluginMigrationRunner(SLUG, migdir).apply_all()
    assert await _where("wrapped_t") is None


async def test_a_plpgsql_body_passes_the_lint_and_the_check(migdir: Path) -> None:
    """BEGIN … END inside a function body is block syntax: the lint lets
    it through, and it leaves the runner's transaction alone."""
    (migdir / "V001__trigger.sql").write_text(
        "CREATE TABLE things (id BIGSERIAL PRIMARY KEY, n INT, touched TIMESTAMPTZ);\n"
        "CREATE FUNCTION things_touch() RETURNS trigger LANGUAGE plpgsql AS $$\n"
        "BEGIN\n"
        "  IF NEW.n IS NULL THEN\n    NEW.n := 0;\n  END IF;\n"
        "  NEW.touched := now();\n"
        "  RETURN NEW;\n"
        "END;\n"
        "$$;\n"
        "CREATE TRIGGER things_touch BEFORE INSERT ON things\n"
        "  FOR EACH ROW EXECUTE FUNCTION things_touch();\n",
        encoding="utf-8",
    )
    runner = PluginMigrationRunner(SLUG, migdir)
    (names,) = (await runner.apply_all()).values()
    assert names == ["V001__trigger.sql"]
    assert await _ledger() == [1]
    assert await _where("things") == (SCHEMA, ROLE)


async def test_a_quote_inside_a_dollar_quote_hides_nothing(migdir: Path) -> None:
    """``$$'$$`` is a one-character string to the server. A lint that
    looked for quotes inside the body opened a literal there, and the SET
    ROLE excursion after it ran as the application user in public — with
    the role back before the file ended, so the runner's own check saw
    nothing. The lint reads it the way the server does and refuses the
    file before anything runs."""
    app_user = engine.url.username   # the connection's session user
    (migdir / "V001__quote.sql").write_text(
        "CREATE TABLE inside_t (id INT);\n"
        "SELECT $$'$$;\n"
        f'SET ROLE "{app_user}";\n'
        f"CREATE TABLE public.{PROBES[2]} (id INT);\n"
        f"SET ROLE {ROLE};\n"
        "SELECT $$'$$;\n",
        encoding="utf-8",
    )
    with pytest.raises(SqlLintError, match="SET/RESET ROLE"):
        await PluginMigrationRunner(SLUG, migdir).apply_all()
    assert await _where(PROBES[2]) is None
    assert await _where("inside_t") is None


async def test_literals_read_the_way_the_lint_read_them(migdir: Path) -> None:
    r"""The lint reads ``'a\'`` as a complete literal (a backslash only
    escapes inside ``E'…'``). A connection whose
    ``standard_conforming_strings`` is off would read it as an escaped
    quote and the rest of the file as the inside of a string; the runner
    pins the setting per file, so the server reads the file as the lint
    did."""
    sql = "CREATE TABLE lit_t (v TEXT);\nINSERT INTO lit_t VALUES ('a\\');\n"
    assert sql_lint(sql, SLUG) == []
    runner = PluginMigrationRunner(SLUG, migdir)

    async def go(drv) -> str:
        await drv.execute("SET standard_conforming_strings = off")
        try:
            assert await runner._apply_files(drv, [_file(sql)]) == ["V001__t.sql"]
            return await drv.fetchval(f'SELECT v FROM "{SCHEMA}".lit_t')
        finally:
            await drv.execute("RESET standard_conforming_strings")

    assert await _driver_do(go) == "a\\"
    assert await _ledger() == [1]
