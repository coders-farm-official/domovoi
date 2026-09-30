"""Shared test helper for the V018 fire ledger (timer_fires,
timer_fire_deliveries, timer_own_only_rooms).

The three tables are deliberately NOT in conftest's TABLES_TO_TRUNCATE: a
test lane patched by hand may not have V018, and every test there would
fail on the missing relation. Tests that use them call :func:`apply_v018`
instead — the V016 precedent (test_command_captures_api.py) — which
creates them if needed and empties them.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

V018 = (
    Path(__file__).resolve().parents[2]
    / "domovoi" / "db" / "migrations" / "V018__timer_fires.sql"
)


def v018_statements() -> list[str]:
    """V018's statements one by one, comments stripped. The file holds
    several, and a prepared statement takes one. All IF NOT EXISTS, so a
    lane that already has V018 takes it as a no-op."""
    lines = V018.read_text(encoding="utf-8").splitlines()
    sql = "\n".join(line.split("--", 1)[0] for line in lines)
    return [st.strip() for st in sql.split(";") if st.strip()]


async def apply_v018() -> None:
    """Apply V018 (idempotent) and empty its three tables."""
    from domovoi.db.session import engine

    async with engine.begin() as conn:
        for statement in v018_statements():
            await conn.exec_driver_sql(statement)
        await conn.execute(text(
            "TRUNCATE timer_fire_deliveries, timer_fires, timer_own_only_rooms "
            "RESTART IDENTITY"
        ))


async def clear_fire_ledger() -> None:
    """Empty the three V018 tables when this lane has them; a no-op when
    it doesn't. For tests that run the real core lifespan: its timer
    watcher records every timer it fires in the ledger, and a fire left
    unsettled there is resumed by the next lifespan in the run, which
    would then announce it to that test's satellites."""
    from domovoi.db.session import engine

    async with engine.begin() as conn:
        present = (await conn.execute(
            text("SELECT to_regclass('public.timer_fires') IS NOT NULL")
        )).scalar_one()
        if present:
            await conn.execute(text(
                "TRUNCATE timer_fire_deliveries, timer_fires, timer_own_only_rooms "
                "RESTART IDENTITY"
            ))
