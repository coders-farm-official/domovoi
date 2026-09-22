"""The household device tier: one token per install, presented as
``X-Device-Token``, accepted by ``require_device`` alongside an admin
Bearer.

Covers, DB-free: the private-file writer (0600), the request-side helpers,
``auth_forward_headers`` carrying the token to the core, the boot hook
mirroring the row to ``~/.domovoi/device-token.txt``, and the
``require_device`` matrix through a mini app over fake primitives.

DB-backed (``requires_db``): a token row + file after the first boot of
EITHER process, the admin read / rotate endpoints on the core and their
web mirror against the same table, rotation at setup, and a route wearing
``require_device`` against the real tables.
"""

from __future__ import annotations

import asyncio
import os
import stat
import sys
from contextlib import asynccontextmanager
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from domovoi import admin_auth
from domovoi.db.session import session_scope
from domovoi.tests.auth_testkit import (
    COOKIE,
    HEADER,
    _db,  # noqa: F401 — fixture
    _db_sync,  # noqa: F401 — fixture
    bearer,
    claim_admin,
    core_app,
    core_client,
    db_device_token,
    install_fake_db,
    make_request,
    mini_client,
    web_app,
    web_client,
)
from domovoi.tests.conftest import requires_db
from web.backend.domovoi_client import auth_forward_headers

# ═══ DB-FREE ══════════════════════════════════════════════════════════════


def test_write_private_file_asks_for_0600(tmp_path, monkeypatch) -> None:
    """The file is CREATED with mode 0600 (never world-readable for an
    instant) and an existing one is tightened before the rewrite."""
    seen: dict[str, Any] = {}
    real_open = os.open
    real_chmod = os.chmod

    def spy_open(path, flags, mode=0o777, *a, **kw):
        seen["open_mode"] = mode
        seen["flags"] = flags
        return real_open(path, flags, mode, *a, **kw)

    def spy_chmod(path, mode, *a, **kw):
        seen.setdefault("chmods", []).append(mode)
        return real_chmod(path, mode, *a, **kw)

    monkeypatch.setattr(os, "open", spy_open)
    monkeypatch.setattr(os, "chmod", spy_chmod)
    target = tmp_path / "nested" / "secret.txt"
    admin_auth.write_private_file(target, "one\n")
    assert seen["open_mode"] == 0o600
    assert seen["flags"] & os.O_CREAT and seen["flags"] & os.O_TRUNC
    assert target.read_text(encoding="utf-8") == "one\n"
    # Rewrite of an existing file: chmod first, then truncate.
    admin_auth.write_private_file(target, "two\n")
    assert seen["chmods"] == [0o600]
    assert target.read_text(encoding="utf-8") == "two\n"
    if sys.platform != "win32":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_device_token_file_goes_through_the_private_writer(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(admin_auth, "CONFIG_DIR", tmp_path)
    modes: list[int] = []
    real_open = os.open

    def spy_open(path, flags, mode=0o777, *a, **kw):
        modes.append(mode)
        return real_open(path, flags, mode, *a, **kw)

    monkeypatch.setattr(os, "open", spy_open)
    admin_auth.write_device_token_file("a" * 64)
    assert modes == [0o600]
    assert admin_auth.read_device_token_file() == "a" * 64
    assert admin_auth.device_token_path() == tmp_path / "device-token.txt"


def test_auth_forward_headers_carries_the_device_token() -> None:
    req = make_request({HEADER: "abc123", "Authorization": "Bearer t"})
    fwd = auth_forward_headers(req)
    assert fwd["X-Device-Token"] == "abc123"
    assert fwd["Authorization"] == "Bearer t"
    assert fwd["X-Forwarded-For"] == "192.168.1.50"
    assert "X-Device-Token" not in auth_forward_headers(make_request())


def test_device_token_from_request_strips_whitespace() -> None:
    assert admin_auth.device_token_from_request(make_request({HEADER: "  tok  "})) == "tok"
    assert admin_auth.device_token_from_request(make_request({HEADER: "   "})) is None
    assert admin_auth.device_token_from_request(make_request()) is None


@pytest.mark.asyncio
async def test_require_device_matrix_after_setup(monkeypatch) -> None:
    install_fake_db(monkeypatch, admin=True, sessions={"admin-token"}, device_token="dev-token")
    async with mini_client() as c:
        assert (await c.post("/device", headers={HEADER: "dev-token"})).status_code == 200
        assert (await c.post("/device", headers=bearer("admin-token"))).status_code == 200
        assert (await c.post("/device")).status_code == 401
        assert (await c.post("/device", headers={HEADER: "stale-token"})).status_code == 401
        assert (await c.post("/device", headers=bearer("stale-admin"))).status_code == 401
        # A valid device token beside a broken Bearer is still a valid device.
        r = await c.post("/device", headers={HEADER: "dev-token", **bearer("stale-admin")})
        assert r.status_code == 200
    async with mini_client(cookies={COOKIE: "admin-token"}) as c:
        # The dashboard cookie alone renders nothing on this tier.
        assert (await c.post("/device")).status_code == 403


@pytest.mark.asyncio
async def test_require_device_keeps_the_pre_setup_grace(monkeypatch) -> None:
    install_fake_db(monkeypatch, admin=False, device_token="dev-token")
    async with mini_client() as c:
        assert (await c.post("/device")).status_code == 200
        assert (await c.post("/device", headers={HEADER: "anything"})).status_code == 200


@pytest.mark.asyncio
async def test_require_device_fails_closed_when_the_check_raises(monkeypatch) -> None:
    install_fake_db(monkeypatch, admin=True, device_token="dev-token")

    async def boom(_s, _c):
        raise RuntimeError("db down")

    monkeypatch.setattr(admin_auth, "validate_device_token", boom)
    async with mini_client() as c:
        assert (await c.post("/device", headers={HEADER: "dev-token"})).status_code == 401


@pytest.mark.asyncio
async def test_ensure_device_token_mirrors_the_row_to_the_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(admin_auth, "CONFIG_DIR", tmp_path)
    install_fake_db(monkeypatch, admin=False)

    async def fake_ensure_row(_s):
        return "b" * 64

    monkeypatch.setattr(admin_auth, "ensure_device_token_row", fake_ensure_row)
    assert await admin_auth.ensure_device_token() == "b" * 64
    assert admin_auth.read_device_token_file() == "b" * 64
    # A stale mirror is overwritten with the row's value.
    admin_auth.write_device_token_file("stale")
    assert await admin_auth.ensure_device_token() == "b" * 64
    assert admin_auth.read_device_token_file() == "b" * 64


@pytest.mark.asyncio
async def test_ensure_device_token_is_never_fatal(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(admin_auth, "CONFIG_DIR", tmp_path)

    @asynccontextmanager
    async def dead_scope():
        raise ConnectionRefusedError("no database")
        yield  # pragma: no cover

    monkeypatch.setattr(admin_auth, "session_scope", dead_scope)
    assert await admin_auth.ensure_device_token() is None
    assert not admin_auth.device_token_path().exists()


# ═══ DB-BACKED ════════════════════════════════════════════════════════════


# ─── A token row + the 0600 file after the first boot of either process ──


@requires_db
@pytest.mark.asyncio
async def test_core_boot_creates_the_token_row_and_file(_db) -> None:
    assert await db_device_token() is None
    async with core_app.router.lifespan_context(core_app):
        pass
    token = await db_device_token()
    assert token and len(token) == 64
    assert admin_auth.read_device_token_file() == token
    if sys.platform != "win32":
        assert stat.S_IMODE(admin_auth.device_token_path().stat().st_mode) == 0o600
    # A second boot reuses the row — never a second token.
    async with core_app.router.lifespan_context(core_app):
        pass
    assert await db_device_token() == token


@requires_db
def test_web_boot_creates_the_token_row_and_file(_db_sync) -> None:
    from fastapi.testclient import TestClient

    with TestClient(web_app):
        pass
    token = asyncio.run(db_device_token())
    assert token and len(token) == 64
    assert admin_auth.read_device_token_file() == token


@requires_db
@pytest.mark.asyncio
async def test_both_processes_share_one_row(_db) -> None:
    """Whichever hook runs first mints; the other reads the same token."""
    first = await admin_auth.ensure_device_token()
    second = await admin_auth.ensure_device_token()
    assert first == second == await db_device_token()


# ─── The admin endpoints on the core ──────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_core_device_token_read_and_rotate(_db) -> None:
    async with web_client() as web:
        admin = await claim_admin(web)
    async with core_client() as core:
        r = await core.get("/v1/admin/device-token")
        assert r.status_code == 401
        r = await core.get("/v1/admin/device-token", headers=bearer(admin))
        assert r.status_code == 200, r.text
        token = r.json()["token"]
        assert r.json()["header"] == HEADER
        assert token == await db_device_token() == admin_auth.read_device_token_file()
        # Rotation is Bearer-only.
        r = await core.post("/v1/admin/device-token/rotate")
        assert r.status_code == 401
        r = await core.post("/v1/admin/device-token/rotate", headers=bearer(admin))
        assert r.status_code == 200, r.text
        rotated = r.json()["token"]
        assert rotated != token and r.json()["rotated"] is True
        assert await db_device_token() == rotated == admin_auth.read_device_token_file()
        r = await core.get("/v1/admin/device-token", headers=bearer(admin))
        assert r.json()["token"] == rotated
    # The old token is refused from now on.
    async with session_scope() as s:
        assert await admin_auth.validate_device_token(s, token) is False
        assert await admin_auth.validate_device_token(s, rotated) is True
    async with AsyncClient(
        transport=ASGITransport(app=core_app), base_url="http://test", cookies={COOKIE: admin}
    ) as core:
        # The cookie renders the read, never the rotation.
        assert (await core.get("/v1/admin/device-token")).status_code == 200
        assert (await core.post("/v1/admin/device-token/rotate")).status_code == 403


@requires_db
@pytest.mark.asyncio
async def test_device_token_endpoints_are_closed_before_setup(_db) -> None:
    await admin_auth.ensure_device_token()
    async with core_client() as core:
        assert (await core.get("/v1/admin/device-token")).status_code == 501
        assert (await core.post("/v1/admin/device-token/rotate")).status_code == 501
    async with web_client() as web:
        assert (await web.get("/api/auth/device-token")).status_code == 501
        assert (await web.post("/api/auth/device-token/rotate")).status_code == 501


@requires_db
@pytest.mark.asyncio
async def test_setup_rotates_the_token_minted_before_it(_db) -> None:
    before = await admin_auth.ensure_device_token()
    async with web_client() as web:
        await claim_admin(web)
    after = await db_device_token()
    assert after is not None and after != before
    assert admin_auth.read_device_token_file() == after


# ─── A route wearing require_device, on the real tables ───────────────────


@requires_db
@pytest.mark.asyncio
async def test_require_device_route_against_the_real_tables(_db) -> None:
    await admin_auth.ensure_device_token()
    async with mini_client() as c:
        # Pre-setup: the LAN grace holds.
        assert (await c.post("/device")).status_code == 200
    async with web_client() as web:
        admin = await claim_admin(web)
    device = await db_device_token()  # rotated at setup
    async with mini_client() as c:
        assert (await c.post("/device", headers={HEADER: device})).status_code == 200
        assert (await c.post("/device", headers=bearer(admin))).status_code == 200
        assert (await c.post("/device")).status_code == 401
        assert (await c.post("/device", headers={HEADER: "0" * 64})).status_code == 401
    # And after a rotation the old token is a stale one.
    async with core_client() as core:
        r = await core.post("/v1/admin/device-token/rotate", headers=bearer(admin))
        new = r.json()["token"]
    async with mini_client() as c:
        assert (await c.post("/device", headers={HEADER: device})).status_code == 401
        assert (await c.post("/device", headers={HEADER: new})).status_code == 200


# ─── The web mirror ───────────────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_web_device_token_mirror_behaves_like_the_core(_db) -> None:
    async with web_client() as web:
        admin = await claim_admin(web)
    async with web_client() as fresh:
        assert (await fresh.get("/api/auth/device-token")).status_code == 401
        r = await fresh.get("/api/auth/device-token", headers=bearer(admin))
        assert r.status_code == 200, r.text
        token = r.json()["token"]
        assert token == await db_device_token()
        assert (await fresh.post("/api/auth/device-token/rotate")).status_code == 401
        r = await fresh.post("/api/auth/device-token/rotate", headers=bearer(admin))
        assert r.status_code == 200, r.text
        rotated = r.json()["token"]
        assert rotated != token
    async with AsyncClient(
        transport=ASGITransport(app=web_app), base_url="http://test",
        headers={"X-Requested-With": "domovoi-tests"}, cookies={COOKIE: admin}
    ) as web:
        assert (await web.get("/api/auth/device-token")).json()["token"] == rotated
        assert (await web.post("/api/auth/device-token/rotate")).status_code == 403
    # Same table: the core now serves the token the web rotated.
    async with core_client() as core:
        r = await core.get("/v1/admin/device-token", headers=bearer(admin))
        assert r.json()["token"] == rotated
    assert admin_auth.read_device_token_file() == rotated
