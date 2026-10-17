"""Which Postgres the core test suite may connect to: never one by accident.

The suite TRUNCATEs tables, and before 2026-10-17 a run with neither
``TEST_DATABASE_URL`` nor ``DATABASE_URL`` set fell back to a built-in
``localhost:6432/domovoi_test``. Port 6432 is where an installed Domovoi
publishes its own Postgres (the Windows installer's, the Linux server's,
and the dev compose file's alike), so on a machine that runs Domovoi for
real an agent's first run reached for the household's live database and
tried to log in to it.

So, before anything connects (domovoi/tests/conftest.py, and the
``xdist_db`` command that drops worker copies):

* with neither variable set, the run stops and says which to set;
* a test database on port 6432 needs a second, explicit word
  (``DOMOVOI_TEST_DB_ON_6432=1``: "6432 here is my development Postgres,
  the compose one, not an install"). The dev compose workflow keeps working
  with that one line; nothing else reaches 6432.

The ``_test`` name check in the conftest stays as it is, on top.
Stdlib only; imports nothing of domovoi.
"""

from __future__ import annotations

from typing import Mapping
from urllib.parse import urlsplit

#: Where an installed Domovoi's Postgres listens (AGENTS.md, convention 3).
LIVE_PORT = 6432
#: The explicit opt-in for a development Postgres on that port.
ALLOW_ENV = "DOMOVOI_TEST_DB_ON_6432"
_POSTGRES_DEFAULT_PORT = 5432

_HOW = (
    "Set TEST_DATABASE_URL to a migrated test database. With the dev compose "
    "Postgres: TEST_DATABASE_URL=postgresql+asyncpg://domovoi:domovoi@127.0.0.1:6432/domovoi_test "
    f"and {ALLOW_ENV}=1 (docs/CONTRIBUTING.md, \"Running the tests\")."
)


def port_of(url: str) -> int | None:
    """The TCP port a database URL names (Postgres' 5432 when it names
    none), or None when it cannot be read."""
    try:
        parts = urlsplit(url)
        if not parts.hostname:
            return None
        return parts.port or _POSTGRES_DEFAULT_PORT
    except ValueError:
        return None


def refusal(env: Mapping[str, str], resolved: str) -> str | None:
    """Why the suite must not connect to ``resolved``, or None when it may."""
    if not env.get("TEST_DATABASE_URL") and not env.get("DATABASE_URL"):
        return (
            "Refusing to pick a test database: neither TEST_DATABASE_URL nor "
            "DATABASE_URL is set, and the old built-in default "
            f"(localhost:{LIVE_PORT}) is where an installed Domovoi keeps its "
            "live database. " + _HOW
        )
    if port_of(resolved) == LIVE_PORT and env.get(ALLOW_ENV) != "1":
        return (
            f"Refusing to run tests against port {LIVE_PORT} ({resolved}): that "
            "is where an installed Domovoi publishes its live Postgres. If this "
            "is your development Postgres (`docker compose up -d postgres` from "
            f"domovoi/), say so with {ALLOW_ENV}=1; otherwise point "
            "TEST_DATABASE_URL at a test server on another port. " + _HOW
        )
    return None
