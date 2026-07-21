from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from domovoi.config import settings

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
engine = create_async_engine(settings.database_url, echo=False, poolclass=NullPool)
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
