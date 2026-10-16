from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from domovoi.config import settings

# The loopback address a ``localhost`` database host is dialed at.
LOOPBACK_IP = "127.0.0.1"


def connect_url(url: str) -> str:
    """``url`` with a ``localhost`` host replaced by ``127.0.0.1``.

    Every session opens a fresh connection (NullPool, below), and a host
    NAME makes each of those connects ask ``getaddrinfo`` first. asyncio
    answers that in the event loop's default thread pool, the same pool
    every ``asyncio.to_thread`` call queues in: once that pool is full of
    long jobs, every database connect waits behind them, and with it every
    request that touches the database (``/v1/health`` included). That is
    one way the core froze on 2026-10-14 (reproduced in the
    the maintainers' functional test harness, kept outside this repo: with the pool full, a ``localhost``
    connect was still waiting after 8 s while ``127.0.0.1`` connected in
    0.02 s). An IP needs no lookup, so the connect never touches the pool.
    It is also the address Docker publishes the compose Postgres on, and
    Windows resolves ``localhost`` to ``::1`` first, a 2 s stall per
    connect against an IPv4-only listener.

    Only the literal name ``localhost`` (any case) is rewritten: another
    name may move, so it keeps resolving each time (in a pool the heavy
    work no longer shares; see ``domovoi/executors.py``). A URL with no
    host (a Unix socket) or one that can't be parsed passes through."""
    try:
        parsed = make_url(url)
    except Exception:  # noqa: BLE001 - never fail a boot over the rewrite
        return url
    if (parsed.host or "").lower() != "localhost":
        return url
    return parsed.set(host=LOOPBACK_IP).render_as_string(hide_password=False)


# NullPool: one connection per session, closed at release.
#
# Rationale: this is a low-QPS homelab service where the ~5 ms connection
# overhead per request is immaterial, and NullPool sidesteps two real pain
# points that pooling causes here:
#   1. Tests — pytest-asyncio creates a new event loop per test, but a pooled
#      connection is bound to the loop it was opened on. Cross-loop reuse
#      raises "Event loop is closed" on Windows ProactorEventLoop.
#   2. Restart robustness — after the core reconnects post-Postgres
#      restart, stale pooled connections can hand back broken sockets before
#      `pool_pre_ping` notices. NullPool guarantees a fresh connection.
# If we ever need pooling (hundreds of QPS), swap this to QueuePool and
# introduce a test-only conftest override at the same time.
engine = create_async_engine(connect_url(settings.database_url), echo=False, poolclass=NullPool)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as s:
        try:
            yield s
            await s.commit()
        except Exception:
            await s.rollback()
            raise
