"""Shared test helper for the V017 fire ledger (timer_fires,
timer_fire_deliveries, timer_own_only_rooms).

The three tables are deliberately NOT in conftest's TABLES_TO_TRUNCATE: a
test lane patched by hand may not have V017, and every test there would
fail on the missing relation. Tests that use them call :func:`apply_v017`
instead — the V016 precedent (test_command_captures_api.py) — which
creates them if needed and empties them.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

V017 = (
    Path(__file__).resolve().parents[2]
    / "domovoi" / "db" / "migrations" / "V017__timer_fires.sql"
)


def v017_statements() -> list[str]:
    """V017's statements one by one, comments stripped. The file holds
    several, and a prepared statement takes one. All IF NOT EXISTS, so a
    lane that already has V017 takes it as a no-op."""
    lines = V017.read_text(encoding="utf-8").splitlines()
    sql = "\n".join(line.split("--", 1)[0] for line in lines)
    return [st.strip() for st in sql.split(";") if st.strip()]


async def apply_v017() -> None:
    """Apply V017 (idempotent) and empty its three tables."""
    from domovoi.db.session import engine

    async with engine.begin() as conn:
        for statement in v017_statements():
            await conn.exec_driver_sql(statement)
        await conn.execute(text(
            "TRUNCATE timer_fire_deliveries, timer_fires, timer_own_only_rooms "
            "RESTART IDENTITY"
        ))
