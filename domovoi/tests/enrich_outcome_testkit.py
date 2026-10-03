"""Shared test helper for V020 (``library_tracks.enrich_outcome``).

A test lane patched by hand may not have V020 yet (the V016 / V018 / V019
precedent, ``library_aliases_testkit.py``). The enricher's SQL reads and
writes the column, so every DB test that runs it calls :func:`apply_v020`
first: V020 is ``ADD COLUMN IF NOT EXISTS`` plus a guarded UPDATE, so a
lane that already has it takes it as a no-op. ``library_tracks`` itself is
in conftest's TABLES_TO_TRUNCATE, so its rows are emptied per test as
usual. Not a test module (no ``test_`` prefix).
"""

from __future__ import annotations

from pathlib import Path

V020 = (
    Path(__file__).resolve().parents[2]
    / "domovoi" / "db" / "migrations" / "V020__library_enrich_outcome.sql"
)


def v020_statements() -> list[str]:
    """V020's statements one by one, comments stripped (a prepared
    statement takes one)."""
    lines = V020.read_text(encoding="utf-8").splitlines()
    sql = "\n".join(line.split("--", 1)[0] for line in lines)
    return [st.strip() for st in sql.split(";") if st.strip()]


async def apply_v020() -> None:
    """Apply V020 (idempotent)."""
    from domovoi.db.session import engine

    async with engine.begin() as conn:
        for statement in v020_statements():
            await conn.exec_driver_sql(statement)
