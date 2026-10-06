"""Run the hermetic harness for scripts/linux/apply-update.sh.

The update unit's script is bash, so its tests are too
(scripts/linux/tests/test-apply-update.sh: throwaway git repos plus PATH
shims for systemctl, docker, pg_dump, pg_restore, psql and curl). This
wrapper keeps them in the suite. On Windows it needs Git for Windows' bash;
System32's bash.exe is the WSL launcher, which would run the harness in a
different machine entirely, so it is never used.

The shims can't judge SQL, so the queries the script sends through the
Postgres container run here against the real test database: the same text,
through the driver, inside a transaction that is rolled back.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from domovoi.db.session import engine
from domovoi.tests.conftest import requires_db

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "linux" / "apply-update.sh"
HARNESS = REPO_ROOT / "scripts" / "linux" / "tests" / "test-apply-update.sh"


def _find_bash() -> str | None:
    if sys.platform != "win32":
        return shutil.which("bash")
    candidates = []
    git = shutil.which("git")
    if git:
        # <root>\cmd\git.exe or <root>\mingw64\bin\git.exe → <root>\bin\bash.exe
        for up in (Path(git).parent.parent, Path(git).parent.parent.parent):
            candidates.append(up / "bin" / "bash.exe")
    candidates.append(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe")
    for c in candidates:
        if c.is_file() and "system32" not in str(c).lower():
            return str(c)
    return None


BASH = _find_bash()
requires_bash = pytest.mark.skipif(BASH is None, reason="no usable bash (Git Bash on Windows)")


def test_scripts_are_lf_only():
    """A CRLF in a bash script is a syntax error on the Linux host."""
    for path in (SCRIPT, HARNESS):
        assert b"\r\n" not in path.read_bytes(), f"{path.name} has CRLF line endings"


@requires_bash
def test_script_parses():
    proc = subprocess.run([BASH, "-n", str(SCRIPT)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


@requires_bash
def test_apply_update_harness():
    proc = subprocess.run(
        [BASH, str(HARNESS)],
        capture_output=True,
        text=True,
        # About 450 s under Git Bash on the dev box, where every fork is slow.
        timeout=900,
        env={**os.environ, "HARNESS_PYTHON": sys.executable},
    )
    assert proc.returncode == 0, proc.stdout[-6000:] + proc.stderr[-2000:]
    assert " 0 failed" in proc.stdout


# ─── the script's SQL, on real Postgres ───────────────────────────────────


def _script_sql(name: str) -> str:
    """The value of NAME="..." in the script. Only plain double-quoted
    one-liners, which bash leaves as written (test_sql_survives_bash
    proves it for each)."""
    m = re.search(rf'^{name}="(.*)"$', SCRIPT.read_text(encoding="utf-8"), re.M)
    assert m, f"{name} not found in {SCRIPT.name}"
    return m.group(1)


SQL_NAMES = ("LEDGER_SQL", "PLUGINS_SQL", "REENABLE_SQL", "APPLIED_SQL")


@requires_bash
@pytest.mark.parametrize("name", SQL_NAMES)
def test_sql_survives_bash(name):
    proc = subprocess.run(
        [BASH, "-c", 'eval "$(grep "^$1=" "$2")"; printf %s "${!1}"', "_", name,
         SCRIPT.as_posix()],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == _script_sql(name)


async def _in_rolled_back_tx(statements: list[str], query: str) -> list[str]:
    """Run STATEMENTS then QUERY on the raw driver connection (the exact
    text, no SQLAlchemy parameter handling), roll everything back, and
    return the query's first column."""
    async with engine.connect() as conn:
        raw = (await conn.get_raw_connection()).driver_connection
        tx = raw.transaction()
        await tx.start()
        try:
            for stmt in statements:
                await raw.execute(stmt)
            return [r[0] for r in await raw.fetch(query)]
        finally:
            await tx.rollback()


def _ledger_ddl(schema: str, rows: int) -> list[str]:
    ddl = [
        f'CREATE SCHEMA "{schema}"',
        f'CREATE TABLE "{schema}".schema_history (version INT PRIMARY KEY, '
        "filename TEXT NOT NULL, checksum TEXT NOT NULL)",
    ]
    ddl += [
        f"INSERT INTO \"{schema}\".schema_history VALUES ({v}, 'V{v:03d}__x.sql', 'c')"
        for v in range(1, rows + 1)
    ]
    return ddl


@requires_db
async def test_ledger_sql_counts_every_plugin_ledger():
    rows = await _in_rolled_back_tx(
        _ledger_ddl("plugin_zzupd_three", 3)
        + _ledger_ddl("plugin_zzupd_empty", 0)
        # Not a plugin's: the name doesn't fit the slug, or the prefix.
        + _ledger_ddl("plugin_ZZupd", 2)
        + _ledger_ddl("zzupd_plugin", 2),
        _script_sql("LEDGER_SQL"),
    )
    assert "plugin_zzupd_three 3" in rows
    assert "plugin_zzupd_empty 0" in rows
    assert not [r for r in rows if "ZZupd" in r or r.startswith("zzupd")]
    # Every line is what the script's parser accepts.
    assert all(re.fullmatch(r"plugin_[a-z0-9_]+ [0-9]+", r) for r in rows), rows


def _plugin_row(slug: str, enabled: bool, status: str, last_error: str | None) -> str:
    err = "NULL" if last_error is None else "'" + last_error.replace("'", "''") + "'"
    return (
        "INSERT INTO plugins (slug, name, version, domovoi_api, enabled, "
        "install_source, install_dir, manifest, status, last_error) VALUES "
        f"('{slug}', '{slug}', '1.0.0', '>=1.0', {str(enabled).upper()}, "
        f"'zip', '/nowhere/{slug}', '{{}}'::jsonb, '{status}', {err})"
    )


_ROWS = [
    _plugin_row("zzupd_ok", True, "ok", None),
    _plugin_row("zzupd_broken", False, "load_error",
                "import failed:\n  No module named 'boom'\t| see above"),
    _plugin_row("zzupd_stuck", True, "load_error", "V002 failed"),
    # Switched off by hand, not by a load error.
    _plugin_row("zzupd_off", False, "ok", None),
]


@requires_db
async def test_plugins_sql_one_line_per_row():
    rows = await _in_rolled_back_tx(_ROWS, _script_sql("PLUGINS_SQL"))
    mine = [r for r in rows if r.startswith("zzupd_")]
    # Sorted by slug; last_error flattened onto the line, a | inside it kept.
    assert mine == [
        "zzupd_broken|f|load_error|import failed: No module named 'boom' | see above",
        "zzupd_off|f|ok|",
        "zzupd_ok|t|ok|",
        "zzupd_stuck|t|load_error|V002 failed",
    ]
    assert all("\n" not in r for r in rows)


@requires_db
async def test_plugins_sql_cuts_a_long_error():
    rows = await _in_rolled_back_tx(
        [_plugin_row("zzupd_long", True, "load_error", "x" * 5000)],
        _script_sql("PLUGINS_SQL"),
    )
    (line,) = [r for r in rows if r.startswith("zzupd_long|")]
    assert line == "zzupd_long|t|load_error|" + "x" * 200


def _history_row(rank: int, version: str | None, typ: str, success: bool) -> str:
    ver = "NULL" if version is None else f"'{version}'"
    return (
        "INSERT INTO flyway_schema_history (installed_rank, version, description, type, "
        "script, checksum, installed_by, execution_time, success) VALUES "
        f"({rank}, {ver}, 'zzupd', '{typ}', 'zzupd.sql', 0, 'harness', 1, {str(success).upper()})"
    )


@requires_db
async def test_applied_sql_counts_only_migrations_that_applied():
    """A plain restart leaves domovoi-db alone when APPLIED_SQL is at least
    the number of migration files in the checkout. Rows that are not an
    applied versioned migration must not count, or a pending migration
    hides behind them and the core boots on a database that lacks it."""
    query = _script_sql("APPLIED_SQL")
    (before,) = await _in_rolled_back_tx([], query)
    noise = [
        _history_row(900001, "1", "BASELINE", True),     # a baseline, not a migration
        _history_row(900002, None, "SCHEMA", True),      # Flyway creating the schema
        _history_row(900003, "999", "SQL", False),       # failed, never applied
        _history_row(900004, None, "SQL", True),         # a repeatable migration
    ]
    (with_noise,) = await _in_rolled_back_tx(noise, query)
    assert with_noise == before
    (with_one,) = await _in_rolled_back_tx(
        noise + [_history_row(900005, "998", "SQL", True)], query)
    assert with_one == before + 1


@requires_db
async def test_reenable_sql_only_switches_on_load_errors():
    # What reenable_plugins sends: the constant, then the quoted slugs.
    reenable = _script_sql("REENABLE_SQL") + " ('zzupd_broken', 'zzupd_off', 'zzupd_ok')"
    rows = await _in_rolled_back_tx(
        _ROWS + [reenable],
        "SELECT slug || '=' || enabled FROM plugins WHERE slug LIKE 'zzupd%' ORDER BY slug",
    )
    assert rows == [
        "zzupd_broken=true",   # a load error switched it off: back on
        "zzupd_off=false",     # switched off by hand: stays off
        "zzupd_ok=true",
        "zzupd_stuck=true",
    ]
