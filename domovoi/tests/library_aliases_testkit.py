"""Shared test helper for the V019 tables (library_aliases,
library_alias_lookups).

The two tables are deliberately NOT in conftest's TABLES_TO_TRUNCATE: a
test lane patched by hand may not have V019, and every test there would
fail on the missing relation (the V016 / V018 precedent,
timer_fires_testkit.py). Tests that use them call :func:`apply_v019`,
which creates them if needed and empties them. (conftest's
``TRUNCATE library_tracks, people ... CASCADE`` also empties
library_aliases on a lane that has it — it references both.)
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

V019 = (
    Path(__file__).resolve().parents[2]
    / "domovoi" / "db" / "migrations" / "V019__library_aliases.sql"
)


def v019_statements() -> list[str]:
    """V019's statements one by one, comments stripped. The file holds
    several, and a prepared statement takes one. All IF NOT EXISTS, so a
    lane that already has V019 takes it as a no-op."""
    lines = V019.read_text(encoding="utf-8").splitlines()
    sql = "\n".join(line.split("--", 1)[0] for line in lines)
    return [st.strip() for st in sql.split(";") if st.strip()]


async def apply_v019() -> None:
    """Apply V019 (idempotent) and empty its two tables."""
    from domovoi.db.session import engine

    async with engine.begin() as conn:
        for statement in v019_statements():
            await conn.exec_driver_sql(statement)
        await conn.execute(text(
            "TRUNCATE library_aliases, library_alias_lookups RESTART IDENTITY"
        ))


async def clear_library_aliases() -> None:
    """Empty the two V019 tables when this lane has them; a no-op when it
    doesn't."""
    from domovoi.db.session import engine

    async with engine.begin() as conn:
        present = (await conn.execute(
            text("SELECT to_regclass('public.library_aliases') IS NOT NULL")
        )).scalar_one()
        if present:
            await conn.execute(text(
                "TRUNCATE library_aliases, library_alias_lookups RESTART IDENTITY"
            ))
