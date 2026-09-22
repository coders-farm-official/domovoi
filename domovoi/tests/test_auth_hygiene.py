"""Credential hygiene (OPS-4): the setup-code file is written 0600 and
refused after its window; a session is refused past the 90-day absolute
cap however recently it was used; changing the password revokes every
other session; a login attempt is counted BEFORE the password verify.

DB-free: the writer, the window, the boot hook, the backoff reservation.
DB-backed (``requires_db``): the same through the real web endpoints and
the core's gate.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from domovoi import admin_auth
from domovoi.db.session import engine
from domovoi.tests.auth_testkit import (
    STRONG_PW,
    _db,  # noqa: F401 — fixture
    backdate,
    bearer,
    claim_admin,
    core_client,
    install_fake_db,
    make_request,
    web_client,
)
from domovoi.tests.conftest import requires_db

# ═══ DB-FREE ══════════════════════════════════════════════════════════════


# ─── Setup-code file: 0600 + a window ─────────────────────────────────────


def test_setup_code_goes_through_the_private_writer(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(admin_auth, "CONFIG_DIR", tmp_path)
    modes: list[int] = []
    real_open = os.open

    def spy_open(path, flags, mode=0o777, *a, **kw):
        modes.append(mode)
        return real_open(path, flags, mode, *a, **kw)

    monkeypatch.setattr(os, "open", spy_open)
    admin_auth.write_setup_code("acorn-apple-arrow-autumn-badge-baker-basil-beach")
    assert modes == [0o600]
    assert admin_auth.read_setup_code() == "acorn-apple-arrow-autumn-badge-baker-basil-beach"


def test_setup_code_is_refused_after_its_window(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(admin_auth, "CONFIG_DIR", tmp_path)
    code = admin_auth.generate_setup_code()
    admin_auth.write_setup_code(code)
    assert admin_auth.verify_setup_code(code) is True
    assert admin_auth.setup_code_expired() is False
    backdate(admin_auth.setup_code_path(), admin_auth.SETUP_CODE_TTL_SEC + 60)
    assert admin_auth.setup_code_expired() is True
    assert admin_auth.verify_setup_code(code) is False
    # No file at all: not "expired", simply absent — and never verifies.
    admin_auth.delete_setup_code()
    assert admin_auth.setup_code_expired() is False
    assert admin_auth.setup_code_age_sec() is None
    assert admin_auth.verify_setup_code(code) is False


@pytest.mark.asyncio
async def test_boot_hook_replaces_an_expired_code(tmp_path, monkeypatch, capsys) -> None:
    """A restart inside the window keeps the code the operator already
    read; a restart after it prints a fresh one."""
    monkeypatch.setattr(admin_auth, "CONFIG_DIR", tmp_path)
    install_fake_db(monkeypatch, admin=False)
    first = await admin_auth.ensure_setup_code_if_unclaimed()
    assert first is not None
    assert await admin_auth.ensure_setup_code_if_unclaimed() == first
    backdate(admin_auth.setup_code_path(), admin_auth.SETUP_CODE_TTL_SEC + 60)
    fresh = await admin_auth.ensure_setup_code_if_unclaimed()
    assert fresh is not None and fresh != first
    assert admin_auth.read_setup_code() == fresh
    assert admin_auth.setup_code_expired() is False
    assert fresh in capsys.readouterr().out


# ─── Backoff: the attempt is counted BEFORE the verify ────────────────────


def test_reservation_counts_before_verify_and_is_released_on_success() -> None:
    b = admin_auth.LoginBackoff()
    b.reserve("a")
    # Immediately after the reservation — i.e. while the verify would be
    # running — a second attempt from the same source is already throttled.
    assert b.retry_after("a") > 0.0
    assert len(b._global) == 1
    b.record_success("a")
    assert b.retry_after("a") == 0.0
    # A released reservation leaves the global window too.
    assert b._global == []


def test_confirmed_failure_after_reservation_is_not_double_counted() -> None:
    b = admin_auth.LoginBackoff()
    b.reserve("a")
    b.record_failure("a")
    assert b._failures["a"][0] == 1
    assert len(b._global) == 1
    # And a plain record_failure (no reservation) still counts on its own.
    b.record_failure("a")
    assert b._failures["a"][0] == 2
    assert len(b._global) == 2


def test_released_reservations_never_trip_the_global_ceiling(monkeypatch) -> None:
    monkeypatch.setattr(admin_auth, "GLOBAL_LOGIN_MAX_FAILURES", 3)
    b = admin_auth.LoginBackoff()
    for i in range(6):
        b.reserve(f"src{i}")
        b.record_success(f"src{i}")
    assert b.retry_after("fresh") == 0.0
    # Confirmed failures still do.
    for i in range(3):
        b.reserve(f"bad{i}")
        b.record_failure(f"bad{i}")
    assert b.retry_after("fresh") > 0.0


def test_enforce_login_backoff_reserves_the_attempt(monkeypatch) -> None:
    b = admin_auth.LoginBackoff()
    monkeypatch.setattr(admin_auth, "LOGIN_BACKOFF", b)
    req = make_request()
    source = admin_auth.enforce_login_backoff(req)
    assert source == "192.168.1.50"
    # The attempt is on the books before any password work happens: a
    # concurrent attempt from the same source is throttled right away.
    assert b.retry_after(source) > 0.0
    with pytest.raises(Exception) as exc:
        admin_auth.enforce_login_backoff(req)
    assert getattr(exc.value, "status_code", None) == 429
    b.record_success(source)
    assert b.retry_after(source) == 0.0


def test_loopback_is_a_trusted_proxy_by_default() -> None:
    """The web process forwards each dashboard caller's real address from
    the same box, so that address — not 127.0.0.1 — is the throttle key;
    any other peer's X-Forwarded-For is still ignored."""
    assert {"127.0.0.1", "::1"} <= admin_auth.TRUSTED_PROXIES
    via_web = make_request({"X-Forwarded-For": "192.168.1.77"}, client_host="127.0.0.1")
    assert admin_auth.request_source(via_web) == "192.168.1.77"
    direct = make_request({"X-Forwarded-For": "192.168.1.77"}, client_host="192.168.1.50")
    assert admin_auth.request_source(direct) == "192.168.1.50"


# ═══ DB-BACKED ════════════════════════════════════════════════════════════


@requires_db
@pytest.mark.asyncio
async def test_setup_refuses_an_expired_code(_db) -> None:
    code = admin_auth.generate_setup_code()
    admin_auth.write_setup_code(code)
    backdate(admin_auth.setup_code_path(), admin_auth.SETUP_CODE_TTL_SEC + 60)
    async with web_client() as web:
        r = await web.post("/api/auth/setup", json={"setup_code": code, "password": STRONG_PW})
    assert r.status_code == 403
    async with engine.begin() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM admin_auth"))).scalar_one() == 0


@requires_db
@pytest.mark.asyncio
async def test_session_older_than_the_cap_is_refused_regardless_of_use(_db) -> None:
    async with web_client() as web:
        admin = await claim_admin(web)
    h = admin_auth.token_sha256(admin)
    async with core_client() as core:
        assert (await core.get("/v1/admin/config", headers=bearer(admin))).status_code == 200
    # Sliding never extends past created_at + 90 days.
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE admin_sessions SET created_at = now() - interval '89 days', "
                "expires_at = now() + interval '1 day' WHERE token_hash = :h"
            ),
            {"h": h},
        )
    async with core_client() as core:
        assert (await core.get("/v1/admin/config", headers=bearer(admin))).status_code == 200
    async with engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT expires_at <= created_at + interval '90 days' AS capped "
                    "FROM admin_sessions WHERE token_hash = :h"
                ),
                {"h": h},
            )
        ).one()
    assert row.capped is True
    # Past the cap: refused even with a future expires_at (recent use).
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE admin_sessions SET created_at = now() - interval '91 days', "
                "expires_at = now() + interval '30 days', last_used_at = now() "
                "WHERE token_hash = :h"
            ),
            {"h": h},
        )
    async with core_client() as core:
        assert (await core.get("/v1/admin/config", headers=bearer(admin))).status_code == 401


@requires_db
@pytest.mark.asyncio
async def test_password_change_revokes_every_other_session(_db) -> None:
    async with web_client() as web:
        first = await claim_admin(web)
        admin_auth.LOGIN_BACKOFF.reset()
        second = (await web.post("/api/auth/login", json={"password": STRONG_PW})).json()["token"]
        admin_auth.LOGIN_BACKOFF.reset()
        third = (await web.post("/api/auth/login", json={"password": STRONG_PW})).json()["token"]
        r = await web.post(
            "/api/auth/password",
            json={"old_password": STRONG_PW, "new_password": "a-brand-new-password"},
            headers=bearer(second),
        )
        assert r.status_code == 200, r.text
        assert r.json()["revoked_sessions"] == 2
    async with core_client() as core:
        assert (await core.get("/v1/admin/config", headers=bearer(first))).status_code == 401
        assert (await core.get("/v1/admin/config", headers=bearer(third))).status_code == 401
        assert (await core.get("/v1/admin/config", headers=bearer(second))).status_code == 200


@requires_db
@pytest.mark.asyncio
async def test_login_backoff_counts_the_attempt_before_the_verify(_db, monkeypatch) -> None:
    """While argon2 is running, the attempt is already on the books: a
    second request from the same source is throttled at once."""
    async with web_client() as web:
        await claim_admin(web)
    admin_auth.LOGIN_BACKOFF.reset()
    seen: dict[str, float] = {}
    real_verify = admin_auth.verify_password

    def spy_verify(password_hash, candidate):
        seen["wait_during_verify"] = admin_auth.LOGIN_BACKOFF.retry_after("127.0.0.1")
        return real_verify(password_hash, candidate)

    monkeypatch.setattr(admin_auth, "verify_password", spy_verify)
    async with web_client() as web:
        r = await web.post("/api/auth/login", json={"password": STRONG_PW})
        assert r.status_code == 200, r.text
    assert seen["wait_during_verify"] > 0.0
    # …and a successful login releases the reservation.
    assert admin_auth.LOGIN_BACKOFF.retry_after("127.0.0.1") == 0.0
