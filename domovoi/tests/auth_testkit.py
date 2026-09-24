"""Shared helpers for the auth-tier test modules (``test_security_tier``,
``test_device_token``, ``test_auth_hygiene``). Not a test module — no
``test_`` prefix — so pytest never collects it directly.

Two kinds of helper live here:

* DB-FREE: a minimal ASGI ``Request`` builder, a fake DB layer that
  monkeypatches the ``domovoi.admin_auth`` primitives, and a tiny FastAPI
  app whose routes wear the real gates (``require_device``,
  ``require_admin_security[_read]``) so the dependency logic is exercised
  end-to-end without Postgres.
* DB-BACKED: the ``_db`` fixture (fresh ``admin_auth`` /
  ``admin_sessions`` / ``household_device_tokens`` state, config dir in
  tmp, dead core hop for the web proxies) and clients for the real core
  and web apps.
"""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from starlette.requests import Request

from domovoi import admin_auth
from domovoi.db.session import engine
from domovoi.main import app as core_app
from web.backend.main import app as web_app

STRONG_PW = "correct-horse-battery"
COOKIE = admin_auth.COOKIE_NAME
HEADER = admin_auth.DEVICE_TOKEN_HEADER


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def make_request(
    headers: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
    client_host: str = "192.168.1.50",
) -> Request:
    """A minimal ASGI-scope Request for unit tests of the request-side
    helpers (no app, no transport)."""
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    if cookies:
        raw.append((b"cookie", "; ".join(f"{k}={v}" for k, v in cookies.items()).encode()))
    return Request({"type": "http", "headers": raw, "client": (client_host, 12345)})


def backdate(path, seconds: float) -> None:
    """Move a file's mtime ``seconds`` into the past (the setup-code
    window is measured from it)."""
    old = time.time() - seconds
    os.utime(path, (old, old))


# ─── DB-free: fake primitives + a mini app wearing the real gates ─────────


class FakeState:
    def __init__(
        self, *, admin: bool, sessions: set[str] | None = None, device_token: str | None = None
    ) -> None:
        self.admin = admin
        self.sessions = sessions or set()
        self.device_token = device_token


def install_fake_db(
    monkeypatch, *, admin: bool, sessions: set[str] | None = None, device_token: str | None = None
) -> FakeState:
    """Replace the DB-touching primitives ``check_admin_request`` /
    ``check_device_request`` call with in-memory fakes. Returns the state
    so a test can flip ``admin`` mid-way."""
    state = FakeState(admin=admin, sessions=sessions, device_token=device_token)
    # Wrong device tokens now cost a per-source backoff that lives in a
    # module global; without this a test that presented a stale token
    # would leak its ladder into whatever ran next.
    admin_auth.DEVICE_TOKEN_BACKOFF.reset()

    @asynccontextmanager
    async def fake_scope():
        yield object()

    async def has_admin_auth(_s):
        return state.admin

    async def validate_token(_s, token):
        return token in state.sessions

    async def validate_device_token(_s, candidate):
        return bool(candidate) and candidate == state.device_token

    monkeypatch.setattr(admin_auth, "session_scope", fake_scope)
    monkeypatch.setattr(admin_auth, "has_admin_auth", has_admin_auth)
    monkeypatch.setattr(admin_auth, "validate_token", validate_token)
    monkeypatch.setattr(admin_auth, "validate_device_token", validate_device_token)
    return state


def mini_app() -> FastAPI:
    app = FastAPI()

    @app.post("/device", dependencies=[Depends(admin_auth.require_device)])
    async def _device() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/security", dependencies=[Depends(admin_auth.require_admin_security)])
    async def _security() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/security-read", dependencies=[Depends(admin_auth.require_admin_security_read)])
    async def _security_read() -> dict[str, bool]:
        return {"ok": True}

    return app


def mini_client(**kw) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=mini_app()), base_url="http://test", **kw)


# ─── DB-backed: fresh auth state + clients for the real apps ──────────────

_AUTH_TABLES = "admin_auth, admin_sessions, household_device_tokens"


@pytest_asyncio.fixture
async def _db(tmp_path, monkeypatch):
    """Fresh auth + device-token state, config dir in tmp, dead core hop
    for the web proxies. Only the DB-backed tests request it."""
    monkeypatch.setattr(admin_auth, "CONFIG_DIR", tmp_path)
    monkeypatch.setenv("DOMOVOI_URL", "http://127.0.0.1:9")
    admin_auth.LOGIN_BACKOFF.reset()
    admin_auth.DEVICE_TOKEN_BACKOFF.reset()
    core_app.state.config_apply_lock = asyncio.Lock()
    core_app.state.active_sessions = {}
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {_AUTH_TABLES} CASCADE"))
    yield
    admin_auth.LOGIN_BACKOFF.reset()
    admin_auth.DEVICE_TOKEN_BACKOFF.reset()
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {_AUTH_TABLES} CASCADE"))


@pytest.fixture
def _db_sync(tmp_path, monkeypatch):
    """Sync twin of ``_db`` for TestClient-driven (threaded) tests."""
    monkeypatch.setattr(admin_auth, "CONFIG_DIR", tmp_path)
    monkeypatch.setenv("DOMOVOI_URL", "http://127.0.0.1:9")
    admin_auth.LOGIN_BACKOFF.reset()
    admin_auth.DEVICE_TOKEN_BACKOFF.reset()

    async def _truncate():
        async with engine.begin() as conn:
            await conn.execute(text(f"TRUNCATE {_AUTH_TABLES} CASCADE"))

    asyncio.run(_truncate())
    yield
    asyncio.run(_truncate())


def web_client(**kw) -> AsyncClient:
    """The web app, spoken to the way a browser speaks to it: every
    write carries ``X-Requested-With`` (WEB-6 refuses one without it).
    Pass ``headers={}`` to drop it deliberately."""
    return AsyncClient(
        transport=ASGITransport(app=web_app),
        base_url="http://test",
        headers={"X-Requested-With": "domovoi-tests"},
        **kw,
    )


def core_client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=core_app), base_url="http://test")


async def claim_admin(client: AsyncClient, password: str = STRONG_PW) -> str:
    """Complete first-run setup through the real web endpoint; returns
    the session token."""
    code = admin_auth.generate_setup_code()
    admin_auth.write_setup_code(code)
    r = await client.post("/api/auth/setup", json={"setup_code": code, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


async def db_device_token() -> str | None:
    async with engine.begin() as conn:
        row = (await conn.execute(text("SELECT token FROM household_device_tokens"))).first()
    return row[0] if row else None
