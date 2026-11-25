"""The database schema's version, for an admin's About & updates (1.5.0).

"Database schema: V086": the highest Flyway migration applied successfully
(``flyway_schema_history``, ``success``). Read-only, a plain number, and
only ever shown to an admin (``GET /v1/admin/version`` adds it only for
an admin's request). Cached: the schema changes only with an update,
which restarts the core, so one read a minute is plenty. When the table
can't be read (a database Flyway never ran on, as in the tests) the answer
is None and nothing is shown.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from sqlalchemy import text

log = logging.getLogger(__name__)

#: How long one read is reused.
CACHE_SEC = 60.0

_cache: tuple[float, int | None] | None = None

_DIGITS = re.compile(r"^\d+$")


def label(version: int | None) -> str | None:
    """``86`` -> ``"V086"`` (the migration files' own spelling)."""
    return None if version is None else f"V{version:03d}"


def highest(versions: list[Any]) -> int | None:
    """The highest whole-number version among ``versions``; others (a
    repeatable migration has none, a dotted one isn't this repo's) are
    skipped."""
    nums = [int(str(v)) for v in versions if v is not None and _DIGITS.match(str(v))]
    return max(nums) if nums else None


async def read(scope: Any = None) -> int | None:
    """The highest successfully applied migration, cached; None when it
    can't be read. Never raises."""
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < CACHE_SEC:
        return _cache[1]
    value: int | None = None
    try:
        if scope is None:
            from domovoi.db.session import session_scope as scope
        async with scope() as s:
            rows = (await s.execute(text(
                "SELECT DISTINCT version FROM flyway_schema_history WHERE success AND version IS NOT NULL"
            ))).scalars().all()
        value = highest(list(rows))
    except Exception as e:  # noqa: BLE001 — no table, no database: show nothing
        log.debug("schema version unreadable: %s", e)
        value = None
    _cache = (now, value)
    return value


def reset_cache() -> None:
    global _cache
    _cache = None


__all__ = ["CACHE_SEC", "highest", "label", "read", "reset_cache"]
