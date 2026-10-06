"""Shared test helper for the V021 lyrics tables (track_lyrics, the
track_lyrics_shown view, track_lyric_lines).

A CONTRACT FILE of the lyrics build: byte-identical in every builder's
branch and edited by none of them (lyrics-build/CONTRACT.md §2).

The tables are deliberately NOT in conftest's TABLES_TO_TRUNCATE: a test
lane patched by hand may not have V021, and every test there would fail on
the missing relation (the V016 / V018 / V019 precedent,
library_aliases_testkit.py). Tests that use them call :func:`apply_v021`,
which creates them if needed and empties them. (conftest's
``TRUNCATE library_tracks ... CASCADE`` also empties both tables on a lane
that has them — they reference library_tracks.)
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

V021 = (
    Path(__file__).resolve().parents[2]
    / "domovoi" / "db" / "migrations" / "V021__track_lyrics.sql"
)


def v021_statements() -> list[str]:
    """V021's statements one by one, comments stripped. The file holds
    several, and a prepared statement takes one. IF NOT EXISTS / OR
    REPLACE throughout, so a lane that already has V021 takes it as a
    no-op."""
    lines = V021.read_text(encoding="utf-8").splitlines()
    sql = "\n".join(line.split("--", 1)[0] for line in lines)
    return [st.strip() for st in sql.split(";") if st.strip()]


async def apply_v021() -> None:
    """Apply V021 (idempotent) and empty its two tables."""
    from domovoi.db.session import engine

    async with engine.begin() as conn:
        for statement in v021_statements():
            await conn.exec_driver_sql(statement)
        await conn.execute(text("TRUNCATE track_lyric_lines, track_lyrics"))


async def clear_lyrics() -> None:
    """Empty the two V021 tables when this lane has them; a no-op when it
    doesn't."""
    from domovoi.db.session import engine

    async with engine.begin() as conn:
        present = (await conn.execute(
            text("SELECT to_regclass('public.track_lyrics') IS NOT NULL")
        )).scalar_one()
        if present:
            await conn.execute(text("TRUNCATE track_lyric_lines, track_lyrics"))
