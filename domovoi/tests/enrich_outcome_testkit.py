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

MIGRATIONS = Path(__file__).resolve().parents[2] / "domovoi" / "db" / "migrations"
V020 = MIGRATIONS / "V020__library_enrich_outcome.sql"
# Every enricher update is logged in library_tag_changes (V088) in the same
# statement, so a lane needs that table too; V088 is CREATE ... IF NOT
# EXISTS throughout.
V088 = MIGRATIONS / "V088__library_tag_changes.sql"


def _statements(path: Path) -> list[str]:
    """A migration's statements one by one, comments stripped (a prepared
    statement takes one)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    sql = "\n".join(line.split("--", 1)[0] for line in lines)
    return [st.strip() for st in sql.split(";") if st.strip()]


def v020_statements() -> list[str]:
    return _statements(V020)


async def apply_v088() -> None:
    """Apply V088, the tag-change log (idempotent)."""
    from domovoi.db.session import engine

    async with engine.begin() as conn:
        for statement in _statements(V088):
            await conn.exec_driver_sql(statement)


async def apply_v020() -> None:
    """Apply V020 and V088 (idempotent): what the enricher's SQL needs."""
    from domovoi.db.session import engine

    async with engine.begin() as conn:
        for statement in v020_statements():
            await conn.exec_driver_sql(statement)
    await apply_v088()
