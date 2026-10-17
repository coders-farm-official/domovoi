"""One test database per pytest-xdist worker (``pytest -n N``).

The suite truncates tables around every test, so two processes on one
database corrupt each other's runs. Under xdist each worker therefore gets
its own copy of the run's database (the "lane", e.g. ``domovoi8_test``):

* named after the lane and the worker: ``domovoi8_test`` + ``gw3`` →
  ``domovoi8_gw3_test`` (still ending in ``_test``, so the conftest's
  safety belt holds for it like for any other test database);
* made on demand with ``CREATE DATABASE … TEMPLATE <lane>``, so it starts
  with exactly the lane's schema (Flyway history included) and data;
* kept and reused on the next run, unless the lane's Flyway history has
  changed since it was copied (a new migration applied to the lane), in
  which case it is dropped and copied again. The lane is the only database
  anyone migrates.

Workers make their copies one at a time, under a Postgres advisory lock:
``CREATE DATABASE … TEMPLATE`` refuses while anything else is connected to
the lane, and reading the lane's history is a connection to it. That is
also why a serial run on the same lane must not run alongside a parallel
one — which was already the rule (one pytest run per lane at a time).

A serial run (no ``-n``, or ``-n 0``) never comes here: it uses the lane
itself, exactly as before.

This module imports nothing from ``domovoi``: the conftest calls it before
``domovoi.config`` is first imported.

Dropping the copies (they are cheap to remake)::

    python -m domovoi.tests.xdist_db --drop            # the lane TEST_DATABASE_URL names
    python -m domovoi.tests.xdist_db --list
"""

from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import os
import re
import sys
import time

from sqlalchemy.engine import URL, make_url

_WORKER_RE = re.compile(r"^gw\d+$")
_COMMENT_PREFIX = "pytest-xdist copy of "


def worker_id() -> str | None:
    """The xdist worker this process is (``gw0`` …), or None. xdist sets
    the variable before the worker reads any conftest."""
    wid = os.environ.get("PYTEST_XDIST_WORKER", "")
    return wid if _WORKER_RE.match(wid) else None


def worker_db_name(lane: str, wid: str) -> str:
    """``domovoi8_test`` + ``gw3`` → ``domovoi8_gw3_test``. Idempotent: a
    name that already is a worker copy (a child pytest a test starts,
    which inherits both the worker's database and PYTEST_XDIST_WORKER) is
    returned as is."""
    if lane.endswith(f"_{wid}_test"):
        return lane
    if not lane.endswith("_test"):
        raise ValueError(f"lane database {lane!r} does not end in _test")
    return f"{lane[: -len('_test')]}_{wid}_test"


def _connect_kwargs(url: URL, database: str) -> dict:
    return {
        "user": url.username,
        "password": url.password,
        "host": url.host or "127.0.0.1",
        "port": url.port or 5432,
        "database": database,
    }


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


async def _lane_fingerprint(asyncpg, url: URL, lane: str) -> str:
    """What the lane's Flyway history says, as one comparable string."""
    conn = await asyncpg.connect(**_connect_kwargs(url, lane), timeout=10)
    try:
        exists = await conn.fetchval("SELECT to_regclass('public.flyway_schema_history') IS NOT NULL")
        if not exists:
            return "no-flyway-history"
        return await conn.fetchval(
            "SELECT count(*)::text || ':' || coalesce(md5(string_agg("
            "coalesce(version, '') || '/' || coalesce(checksum::text, '') || '/' || success::text,"
            " ',' ORDER BY installed_rank)), '') FROM flyway_schema_history"
        )
    finally:
        await conn.close()


async def _ensure(url: URL, lane: str, target: str) -> str:
    """Make ``target`` a current copy of ``lane``; return what was done."""
    import asyncpg

    admin = await asyncpg.connect(**_connect_kwargs(url, "postgres"), timeout=10)
    try:
        # One worker at a time, per lane (hashtext is stable across sessions).
        await admin.execute("SELECT pg_advisory_lock(hashtext($1))", f"domovoi-xdist-db:{lane}")
        try:
            try:
                fingerprint = await _lane_fingerprint(asyncpg, url, lane)
            except asyncpg.exceptions.InvalidCatalogNameError:
                raise RuntimeError(
                    f"lane database {lane} does not exist: create it and migrate it with "
                    f"Flyway first (domovoi/README.md, \"Test database setup\")"
                ) from None
            want = f"{_COMMENT_PREFIX}{lane} at {fingerprint}"
            have = await admin.fetchval(
                "SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname = $1",
                target,
            )
            if have == want:
                return "reused"
            # Missing, stale (the lane migrated since), or not ours to trust.
            await admin.execute(f"DROP DATABASE IF EXISTS {_quote_ident(target)} WITH (FORCE)")
            # The lane must have no other session for TEMPLATE; a moment's
            # connection (someone's quick look) is waited out, a pytest on
            # the lane is not.
            for attempt in range(10):
                try:
                    await admin.execute(
                        f"CREATE DATABASE {_quote_ident(target)} TEMPLATE {_quote_ident(lane)}"
                    )
                    break
                except asyncpg.exceptions.ObjectInUseError:
                    if attempt == 9:
                        raise RuntimeError(
                            f"can't copy {lane} for this worker: other sessions are connected "
                            f"to it (a pytest run on the lane itself?). One run per lane at a time."
                        ) from None
                    await asyncio.sleep(1)
            await admin.execute(
                f"COMMENT ON DATABASE {_quote_ident(target)} IS "
                + "'" + want.replace("'", "''") + "'"
            )
            return "created"
        finally:
            await admin.execute("SELECT pg_advisory_unlock(hashtext($1))", f"domovoi-xdist-db:{lane}")
    finally:
        await admin.close()


def _run(coro):
    """Run a coroutine on a private loop in a private thread, so nothing of
    it is left on the main thread for pytest-asyncio to trip over."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def worker_database_url(lane_url: str, *, create: bool) -> str:
    """The URL this xdist worker should use instead of ``lane_url``; with
    ``create``, the database is made (or refreshed) first. Outside xdist,
    ``lane_url`` unchanged."""
    wid = worker_id()
    if wid is None:
        return lane_url
    url = make_url(lane_url)
    lane = url.database or ""
    target = worker_db_name(lane, wid)
    if target == lane:
        return lane_url
    if create:
        started = time.monotonic()
        what = _run(_ensure(url, lane, target))
        if what == "created":
            print(
                f"[xdist-db] {wid}: {target} copied from {lane} in {time.monotonic() - started:.1f}s",
                file=sys.stderr,
            )
    return url.set(database=target).render_as_string(hide_password=False)


# ─── the connection budget ───────────────────────────────────────────────────

# A test that starts the core app (TestClient(app)) opens about 18
# connections at once while the lifespan brings its workers up (NullPool:
# one connection per session); measured 2026-10-12, 20 at the most per
# worker. N workers can line those bursts up.
PEAK_CONNECTIONS_PER_WORKER = 20


async def _headroom(url: URL) -> tuple[int, int]:
    import asyncpg

    conn = await asyncpg.connect(**_connect_kwargs(url, "postgres"), timeout=5)
    try:
        row = await conn.fetchrow(
            "SELECT current_setting('max_connections')::int"
            " - current_setting('superuser_reserved_connections')::int AS usable,"
            " (SELECT count(*) FROM pg_stat_activity WHERE backend_type = 'client backend') - 1 AS in_use"
        )
        return row["usable"], row["in_use"]
    finally:
        await conn.close()


def connection_warning(lane_url: str, workers: int) -> str | None:
    """A warning when ``workers`` bursting at once could run the server out
    of connections — which fails tests in this run and in any other run on
    the same server. None when there is room (or the server can't be asked)."""
    try:
        usable, in_use = _run(_headroom(make_url(lane_url)))
    except Exception:
        return None
    need = workers * PEAK_CONNECTIONS_PER_WORKER
    if in_use + need <= usable:
        return None
    fits = max(1, (usable - in_use) // PEAK_CONNECTIONS_PER_WORKER)
    return (
        f"pytest -n {workers}: each worker can open ~{PEAK_CONNECTIONS_PER_WORKER} Postgres "
        f"connections at once (a test starting the app), up to {need} in all, and {in_use} of "
        f"this server's {usable} are in use: the server can run out ('too many clients'), "
        f"failing tests in this run and in other runs on it. -n {fits} fits now."
    )


# ─── housekeeping CLI ────────────────────────────────────────────────────────


async def _copies(url: URL, lane: str) -> list[str]:
    import asyncpg

    stem = lane[: -len("_test")]
    conn = await asyncpg.connect(**_connect_kwargs(url, "postgres"), timeout=10)
    try:
        rows = await conn.fetch(
            "SELECT datname FROM pg_database WHERE datname ~ $1 ORDER BY datname",
            "^" + re.escape(stem) + r"_gw[0-9]+_test$",
        )
        return [r["datname"] for r in rows]
    finally:
        await conn.close()


async def _drop(url: URL, names: list[str]) -> None:
    import asyncpg

    conn = await asyncpg.connect(**_connect_kwargs(url, "postgres"), timeout=10)
    try:
        for name in names:
            await conn.execute(f"DROP DATABASE IF EXISTS {_quote_ident(name)} WITH (FORCE)")
    finally:
        await conn.close()


def _lane_url_from_env() -> str:
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        return explicit
    prod = os.environ.get("DATABASE_URL")
    if prod and "/" in prod:
        head, _, dbname = prod.rpartition("/")
        dbname_only, _, query = dbname.partition("?")
        return f"{head}/{dbname_only}_test" + (f"?{query}" if query else "")
    return "postgresql+asyncpg://domovoi:domovoi@localhost:6432/domovoi_test"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m domovoi.tests.xdist_db",
        description="List or drop the per-worker copies pytest -n made of a test lane.",
    )
    ap.add_argument("--url", default=None, help="the lane's URL (default: TEST_DATABASE_URL, as the conftest reads it)")
    what = ap.add_mutually_exclusive_group(required=True)
    what.add_argument("--list", action="store_true", help="print the lane's worker copies")
    what.add_argument("--drop", action="store_true", help="drop every worker copy of the lane")
    args = ap.parse_args(argv)
    raw = args.url or _lane_url_from_env()
    # The same refusal as the conftest's (db_guard.py): no database by
    # accident, and none on an installed Domovoi's port without the opt-in.
    from domovoi.tests import db_guard

    env = dict(os.environ)
    if args.url:
        env.setdefault("TEST_DATABASE_URL", args.url)
    refused = db_guard.refusal(env, raw)
    if refused:
        print(f"refusing: {refused}", file=sys.stderr)
        return 2
    url = make_url(raw)
    lane = url.database or ""
    if not lane.endswith("_test"):
        print(f"refusing: {lane!r} is not a test database", file=sys.stderr)
        return 2
    names = _run(_copies(url, lane))
    if args.drop and names:
        _run(_drop(url, names))
    for name in names:
        print(("dropped " if args.drop else "") + name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
