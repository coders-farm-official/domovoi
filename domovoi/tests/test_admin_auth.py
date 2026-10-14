"""Admin auth (design §7.2/§7.3, v1 scope amendment).

Covers the whole lightweight model end-to-end on BOTH processes:

* first-run setup-code flow (403 without/with wrong code; file deleted
  on success; 409 once claimed; ``--reset-admin`` regeneration);
* login + per-source exponential backoff (429 while throttled);
* Bearer-only mutations (a cookie-only mutation 403s; the cookie may
  render GET state);
* the §7.3 gated-route matrix on the core AND the web app (config
  reads+writes, satellite code push, Letta resync, plugin mutations);
* 30-day SLIDING token expiry (use extends ``expires_at``);
* the outbound-fetch tier on add-by-url (admin session OR fulfiller
  ``url_matcher`` allowlist + per-source rate limit).

The web app is exercised without its lifespan (no poll loops); the
core hop behind web proxies is pointed at a dead port so gate verdicts
are distinguishable from proxy results (502 = gate passed, hop dead).
"""

from __future__ import annotations

import asyncio
import io
import logging
import sys
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

import domovoi.main as core_main
from domovoi import admin_auth
from domovoi.acquisitions import ACQUISITIONS
from domovoi.db.session import engine
from domovoi.main import app as core_app
from domovoi.tests.conftest import _DB_OK, TABLES_TO_TRUNCATE, requires_db
from web.backend.main import app as web_app

STRONG_PW = "correct-horse-battery"
COOKIE = admin_auth.COOKIE_NAME


@pytest_asyncio.fixture(autouse=True)
async def _auth_isolation(tmp_path, monkeypatch):
    """Fresh auth state per test AND after it — an admin_auth row must
    never leak into unrelated tests (they rely on the pre-setup
    LAN-trust grace)."""
    monkeypatch.setattr(admin_auth, "CONFIG_DIR", tmp_path)
    # Web→core proxy hops must fail fast at a dead port, never reach a
    # dev core that happens to be running on :6370.
    monkeypatch.setenv("DOMOVOI_URL", "http://127.0.0.1:9")
    admin_auth.LOGIN_BACKOFF.reset()
    admin_auth.URL_FETCH_LIMITER.reset()
    if not _DB_OK:
        # The DB-free tests in this module must run (not error) without
        # Postgres — the requires_db ones skip on their own.
        yield
        return
    async with engine.begin() as conn:
        await conn.execute(
            text(f"TRUNCATE {', '.join(TABLES_TO_TRUNCATE)} RESTART IDENTITY CASCADE")
        )
    yield
    admin_auth.LOGIN_BACKOFF.reset()
    admin_auth.URL_FETCH_LIMITER.reset()
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE admin_auth, admin_sessions CASCADE"))


def _web() -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=web_app),
        base_url="http://test",
        headers={"X-Requested-With": "domovoi-tests"},
    )


def _core() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=core_app), base_url="http://test")


async def _claim_admin(client: AsyncClient, password: str = STRONG_PW) -> str:
    """Complete first-run setup through the real endpoint; returns the
    session token."""
    code = admin_auth.generate_setup_code()
    admin_auth.write_setup_code(code)
    r = await client.post(
        "/api/auth/setup", json={"setup_code": code, "password": password}
    )
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ─── Setup-code flow (§7.2) ────────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_setup_without_code_403() -> None:
    async with _web() as client:
        r = await client.post(
            "/api/auth/setup",
            json={"setup_code": "guess", "password": STRONG_PW},
        )
    assert r.status_code == 403
    async with engine.begin() as conn:
        rows = (await conn.execute(text("SELECT 1 FROM admin_auth"))).all()
    assert rows == []


@requires_db
@pytest.mark.asyncio
async def test_setup_wrong_code_403_then_backoff() -> None:
    admin_auth.write_setup_code(admin_auth.generate_setup_code())
    async with _web() as client:
        r = await client.post(
            "/api/auth/setup",
            json={"setup_code": "not-the-code", "password": STRONG_PW},
        )
        assert r.status_code == 403
        # The failed attempt started the source's exponential backoff.
        r2 = await client.post(
            "/api/auth/setup",
            json={"setup_code": "not-the-code", "password": STRONG_PW},
        )
    assert r2.status_code == 429
    assert "retry-after" in {k.lower() for k in r2.headers.keys()}


@requires_db
@pytest.mark.asyncio
async def test_setup_success_deletes_code_and_conflicts_after() -> None:
    async with _web() as client:
        token = await _claim_admin(client)
        assert token
        # Code file consumed — a stale code must not linger.
        assert admin_auth.read_setup_code() is None
        # The tier is claimed: a second setup (even with a fresh code)
        # conflicts.
        admin_auth.write_setup_code(admin_auth.generate_setup_code())
        r = await client.post(
            "/api/auth/setup",
            json={
                "setup_code": admin_auth.read_setup_code(),
                "password": "another-password",
            },
        )
        assert r.status_code == 409
    async with engine.begin() as conn:
        n = (await conn.execute(text("SELECT COUNT(*) FROM admin_auth"))).scalar_one()
    assert n == 1


@requires_db
@pytest.mark.asyncio
async def test_setup_password_min_length_enforced() -> None:
    code = admin_auth.generate_setup_code()
    admin_auth.write_setup_code(code)
    async with _web() as client:
        r = await client.post(
            "/api/auth/setup", json={"setup_code": code, "password": "short"}
        )
    assert r.status_code == 422


@requires_db
@pytest.mark.asyncio
async def test_boot_hook_writes_code_only_while_unclaimed() -> None:
    code = await admin_auth.ensure_setup_code_if_unclaimed()
    assert code is not None
    assert admin_auth.read_setup_code() == code
    # Idempotent across restarts: the operator's already-printed code
    # must survive.
    assert await admin_auth.ensure_setup_code_if_unclaimed() == code
    async with _web() as client:
        await _claim_admin(client)
    # Claimed ⇒ no code, and a stale file would be removed.
    admin_auth.write_setup_code("stale-code")
    assert await admin_auth.ensure_setup_code_if_unclaimed() is None
    assert admin_auth.read_setup_code() is None


@requires_db
@pytest.mark.asyncio
async def test_reset_admin_clears_credential_and_sessions() -> None:
    async with _web() as client:
        token = await _claim_admin(client)
        new_code = await admin_auth.reset_admin()
        assert admin_auth.read_setup_code() == new_code
        # Old token dead, tier unclaimed again (plugin mgmt fails closed
        # with 501 pre-setup, so probe via the web status endpoint).
        r = await client.get("/api/auth/status", headers=_bearer(token))
        body = r.json()
        assert (body["setup_complete"], body["authenticated"]) == (False, False)
        # …and the cookie the setup set resumes nothing (2026-10-14).
        assert body["resume_in_sec"] is None
        r = await client.post("/api/auth/resume", headers={"Origin": "http://test"})
        assert r.status_code == 401


# ─── The setup code stays out of the logs (owner decision, 2026-10-11) ─────
# Printed only when stdout is a terminal. Under systemd stdout IS the
# journal, and the journal travels with every log bundle, so there the core
# says where the code is and never what it is. DB-free: the boot hook's
# "is it claimed?" read is stubbed.

# Not a real code — a sentinel nothing else in the process would print.
FAKE_CODE = "fake-sentinel-code-never-real-test-only-words-here"


class _Stdout(io.StringIO):
    """A stdout stand-in that answers ``isatty`` as told."""

    def __init__(self, tty: bool) -> None:
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


@pytest.fixture
def _unclaimed_no_db(monkeypatch):
    @asynccontextmanager
    async def _no_db():
        yield None

    async def _no_admin(_session) -> bool:
        return False

    monkeypatch.setattr(admin_auth, "session_scope", _no_db)
    monkeypatch.setattr(admin_auth, "has_admin_auth", _no_admin)
    # A fresh file inside its window: the hook reuses it, so the code under
    # test is the sentinel, never a generated one.
    admin_auth.write_setup_code(FAKE_CODE)


def test_stdout_is_terminal_is_false_whenever_in_doubt(monkeypatch) -> None:
    monkeypatch.setattr(sys, "stdout", None)
    assert admin_auth.stdout_is_terminal() is False
    closed = io.StringIO()
    closed.close()  # isatty() on a closed stream raises ValueError
    monkeypatch.setattr(sys, "stdout", closed)
    assert admin_auth.stdout_is_terminal() is False

    class _Raises:
        def isatty(self) -> bool:
            raise OSError("stdout went away")

    monkeypatch.setattr(sys, "stdout", _Raises())
    assert admin_auth.stdout_is_terminal() is False
    monkeypatch.setattr(sys, "stdout", _Stdout(tty=False))
    assert admin_auth.stdout_is_terminal() is False
    monkeypatch.setattr(sys, "stdout", _Stdout(tty=True))
    assert admin_auth.stdout_is_terminal() is True


@pytest.mark.asyncio
async def test_boot_hook_on_a_terminal_prints_the_banner_with_the_code(
    _unclaimed_no_db, monkeypatch, caplog, capfd
) -> None:
    out = _Stdout(tty=True)
    monkeypatch.setattr(sys, "stdout", out)
    with caplog.at_level(logging.DEBUG):
        assert await admin_auth.ensure_setup_code_if_unclaimed() == FAKE_CODE
    assert f"Setup code: {FAKE_CODE}" in out.getvalue()
    # Even here the LOG line carries the path, not the code, and nothing
    # else (stderr, the real descriptors) does.
    assert FAKE_CODE not in caplog.text
    assert str(admin_auth.setup_code_path()) in caplog.text
    fd_out, fd_err = capfd.readouterr()
    assert FAKE_CODE not in fd_out + fd_err


@pytest.mark.asyncio
async def test_boot_hook_off_a_terminal_logs_where_the_code_is_never_the_code(
    _unclaimed_no_db, monkeypatch, caplog, capfd
) -> None:
    out = _Stdout(tty=False)
    monkeypatch.setattr(sys, "stdout", out)
    with caplog.at_level(logging.DEBUG):
        assert await admin_auth.ensure_setup_code_if_unclaimed() == FAKE_CODE
    assert out.getvalue() == ""  # no banner at all
    assert FAKE_CODE not in caplog.text
    # Not on stderr either, nor written past sys.stdout to the descriptors:
    # under systemd both streams are the journal.
    fd_out, fd_err = capfd.readouterr()
    assert FAKE_CODE not in fd_out + fd_err
    records = [r for r in caplog.records if r.name == admin_auth.__name__]
    lines = [r.getMessage() for r in records]
    assert len(lines) == 1, lines
    # A warning, so LOG_LEVEL=WARNING still says where the code is.
    assert records[0].levelno == logging.WARNING
    assert str(admin_auth.setup_code_path()) in lines[0]
    assert "dashboard" in lines[0] and "set up admin" in lines[0]
    # The file itself still holds the code: that is where it is read from.
    assert admin_auth.read_setup_code() == FAKE_CODE


def test_reset_admin_cli_prints_the_code_only_on_a_terminal(monkeypatch, caplog, capfd) -> None:
    async def _fake_reset() -> str:
        return FAKE_CODE

    monkeypatch.setattr(admin_auth, "reset_admin", _fake_reset)

    on_tty = _Stdout(tty=True)
    monkeypatch.setattr(sys, "stdout", on_tty)
    core_main._reset_admin_cli()
    assert f"new setup code: {FAKE_CODE}" in on_tty.getvalue()

    capfd.readouterr()  # the terminal run's output is not this check's
    redirected = _Stdout(tty=False)
    monkeypatch.setattr(sys, "stdout", redirected)
    with caplog.at_level(logging.DEBUG):
        core_main._reset_admin_cli()
    said = redirected.getvalue()
    assert FAKE_CODE not in said
    assert FAKE_CODE not in caplog.text
    fd_out, fd_err = capfd.readouterr()
    assert FAKE_CODE not in fd_out + fd_err
    assert "admin credential and all sessions cleared." in said
    assert str(admin_auth.setup_code_path()) in said
    assert "dashboard" in said


# ─── Login + backoff (§7.3) ────────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_login_wrong_password_401_then_429() -> None:
    async with _web() as client:
        await _claim_admin(client)
        r = await client.post("/api/auth/login", json={"password": "wrong-password"})
        assert r.status_code == 401
        # Immediate retry from the same source — even with the RIGHT
        # password — is throttled (1 s first step).
        r2 = await client.post("/api/auth/login", json={"password": STRONG_PW})
        assert r2.status_code == 429
        assert int(r2.headers["retry-after"]) >= 1


def test_backoff_is_exponential_capped_and_per_source() -> None:
    b = admin_auth.LoginBackoff()
    assert b.retry_after("a") == 0.0
    b.record_failure("a")
    assert 0.0 < b.retry_after("a") <= admin_auth.BACKOFF_BASE_SEC
    b.record_failure("a")
    b.record_failure("a")
    # 3 consecutive failures ⇒ 4 s window.
    assert b.retry_after("a") > 2.0
    # Host B is untouched — per source, never shared (§7.3).
    assert b.retry_after("b") == 0.0
    # Cap at 5 min no matter how many failures.
    for _ in range(30):
        b.record_failure("a")
    assert b.retry_after("a") <= admin_auth.BACKOFF_CAP_SEC
    # Success clears the source.
    b.record_success("a")
    assert b.retry_after("a") == 0.0


def _request(client_host: str | None, headers: dict[str, str] | None = None):
    """Minimal ASGI-scope Request for request_source() unit tests."""
    from starlette.requests import Request

    scope = {
        "type": "http",
        "headers": [
            (k.lower().encode(), v.encode()) for k, v in (headers or {}).items()
        ],
        "client": (client_host, 12345) if client_host is not None else None,
    }
    return Request(scope)


def test_xff_ignored_from_untrusted_peer(monkeypatch) -> None:
    """A spoofable X-Forwarded-For from an UNTRUSTED immediate peer must
    not become the throttle identity — otherwise a LAN attacker rotates it
    for unlimited fresh zero-failure buckets. The key is the real peer."""
    monkeypatch.setattr(admin_auth, "TRUSTED_PROXIES", set())
    req = _request("192.168.1.50", {"X-Forwarded-For": "10.0.0.9, 1.2.3.4"})
    assert admin_auth.request_source(req) == "192.168.1.50"


def test_xff_honored_only_from_trusted_peer(monkeypatch) -> None:
    """When the immediate peer IS a configured trusted proxy, its
    forwarded first hop becomes the identity."""
    monkeypatch.setattr(admin_auth, "TRUSTED_PROXIES", {"127.0.0.1"})
    trusted = _request("127.0.0.1", {"X-Forwarded-For": "10.0.0.9, 1.2.3.4"})
    assert admin_auth.request_source(trusted) == "10.0.0.9"
    # A different (untrusted) peer with the same header is still keyed on
    # the real peer.
    untrusted = _request("192.168.1.50", {"X-Forwarded-For": "10.0.0.9"})
    assert admin_auth.request_source(untrusted) == "192.168.1.50"


def test_global_login_ceiling_trips_across_sources(monkeypatch) -> None:
    """The per-source backoff can be sidestepped by rotating the source
    key; the global ceiling is the v1 backstop. Once aggregate failures
    cross the ceiling within the window, even a brand-new source with a
    clean per-source record is throttled."""
    monkeypatch.setattr(admin_auth, "GLOBAL_LOGIN_MAX_FAILURES", 3)
    b = admin_auth.LoginBackoff()
    # Distinct sources so per-source backoff stays 0 for each — only the
    # global aggregate accumulates.
    for i in range(3):
        assert b.retry_after(f"src{i}") == 0.0
        b.record_failure(f"src{i}")
    # A never-before-seen source is now throttled purely by the ceiling.
    assert b.retry_after("fresh-source") > 0.0
    # And a single per-source success does NOT drain the global backstop.
    b.record_success("src0")
    assert b.retry_after("another-fresh") > 0.0


@requires_db
@pytest.mark.asyncio
async def test_login_success_sets_strict_cookie_and_live_token() -> None:
    async with _web() as client:
        await _claim_admin(client)
        admin_auth.LOGIN_BACKOFF.reset()
        r = await client.post(
            "/api/auth/login", json={"password": STRONG_PW, "label": "test box"}
        )
        assert r.status_code == 200
        token = r.json()["token"]
        set_cookie = r.headers.get("set-cookie", "")
        assert COOKIE in set_cookie
        assert "samesite=strict" in set_cookie.lower()
        assert "httponly" in set_cookie.lower()
        # Only the sha256 is stored — never the raw token.
        async with engine.begin() as conn:
            hashes = [
                row[0]
                for row in (
                    await conn.execute(text("SELECT token_hash FROM admin_sessions"))
                ).all()
            ]
        assert admin_auth.token_sha256(token) in hashes
        assert token not in hashes
    # The token authorizes a gated core read — same table, other process.
    async with _core() as core:
        r = await core.get("/v1/admin/config", headers=_bearer(token))
        assert r.status_code == 200


# ─── Bearer-only mutations / cookie renders GET state (§7.3) ──────────────


@requires_db
@pytest.mark.asyncio
async def test_cookie_only_mutation_403s_but_cookie_get_renders() -> None:
    async with _web() as client:
        token = await _claim_admin(client)
        # The browser cookie the setup set: its own secret, not the Bearer.
        cookie = client.cookies.get(COOKIE)
    assert cookie and cookie != token
    core_app.state.config_apply_lock = asyncio.Lock()
    cookie_only = {"cookies": {COOKIE: cookie}}
    async with AsyncClient(
        transport=ASGITransport(app=core_app), base_url="http://test", **cookie_only
    ) as core:
        # GET state renders via the cookie…
        r = await core.get("/v1/admin/config")
        assert r.status_code == 200
        # …but a cookie can never authorize a mutation (CSRF stance):
        r = await core.post("/v1/admin/config", json={"changes": {}})
        assert r.status_code == 403
        # Bearer authorizes the same mutation.
        r = await core.post(
            "/v1/admin/config", json={"changes": {}}, headers=_bearer(token)
        )
        assert r.status_code == 200, r.text
    async with AsyncClient(
        transport=ASGITransport(app=web_app), base_url="http://test",
        headers={"X-Requested-With": "domovoi-tests"}, **cookie_only
    ) as web:
        # Same stance on the web process.
        r = await web.patch("/api/config/editable", json={"changes": {}})
        assert r.status_code == 403
        r = await web.get("/api/config/editable")
        assert r.status_code not in (401, 403)  # gate passed (502: hop dead)


# ─── Gated-route matrix (§7.3) ─────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_gated_route_matrix_core() -> None:
    async with _web() as client:
        token = await _claim_admin(client)
    core_app.state.config_apply_lock = asyncio.Lock()
    core_app.state.active_sessions = {}
    gated = [
        ("GET", "/v1/admin/config", None),
        ("POST", "/v1/admin/config", {"changes": {}}),
        ("POST", "/v1/admin/satellite/upgrade", {"room_id": "kitchen"}),
        ("POST", "/v1/admin/chat/resync", {}),
        ("POST", "/v1/plugins/nonexistent/enable", {}),
    ]
    async with _core() as core:
        for method, path, body in gated:
            r = await core.request(method, path, json=body)
            assert r.status_code == 401, f"{method} {path}: {r.status_code}"
        for method, path, body in gated:
            r = await core.request(method, path, json=body, headers=_bearer(token))
            # Past the gate the endpoint speaks for itself (200, 404 for
            # the unknown plugin, 503 with no satellites…) — never an
            # auth status.
            assert r.status_code not in (401, 403, 501), (
                f"{method} {path}: {r.status_code} {r.text}"
            )
        # Garbage token stays out.
        r = await core.post(
            "/v1/admin/config", json={"changes": {}},
            headers=_bearer("0" * 64),
        )
        assert r.status_code == 401


@requires_db
@pytest.mark.asyncio
async def test_gated_route_matrix_web() -> None:
    async with _web() as client:
        token = await _claim_admin(client)
    checks = [
        ("GET", "/api/config/editable", None),
        ("PATCH", "/api/config/editable", {"changes": {}}),
        ("POST", "/api/satellites/kitchen/upgrade", None),
    ]
    # Fresh client: no cookie jar from the setup call — unauthenticated.
    async with _web() as fresh:
        for method, path, body in checks:
            r = await fresh.request(method, path, json=body)
            assert r.status_code == 401, f"{method} {path}: {r.status_code}"
        for method, path, body in checks:
            r = await fresh.request(method, path, json=body, headers=_bearer(token))
            # Gate passed — the dead core hop yields 502 from
            # bridge_response, never an auth status.
            assert r.status_code not in (401, 403), f"{method} {path}"


@requires_db
@pytest.mark.asyncio
async def test_pre_setup_grace_vs_fail_closed() -> None:
    """Before setup: daily surfaces and admin READS keep the open
    LAN-trust grace, but the SECURITY tier (config write, like plugin
    management) fails closed with 501 — install is code execution
    (§7.1) and a config write can change what the server runs."""
    core_app.state.config_apply_lock = asyncio.Lock()
    async with _core() as core:
        r = await core.get("/v1/admin/config")
        assert r.status_code == 200
        r = await core.post("/v1/admin/config", json={"changes": {}})
        assert r.status_code == 501
        r = await core.post("/v1/plugins/nonexistent/enable", json={})
        assert r.status_code == 501


# ─── Sliding expiry (§7.3) ─────────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_token_expiry_slides_on_use() -> None:
    async with _web() as client:
        token = await _claim_admin(client)
    h = admin_auth.token_sha256(token)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE admin_sessions SET expires_at = now() + interval '1 hour' "
                "WHERE token_hash = :h"
            ),
            {"h": h},
        )
    async with _core() as core:
        r = await core.get("/v1/admin/config", headers=_bearer(token))
        assert r.status_code == 200
    async with engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT expires_at > now() + interval '25 days' AS slid, "
                    "last_used_at IS NOT NULL AS used "
                    "FROM admin_sessions WHERE token_hash = :h"
                ),
                {"h": h},
            )
        ).one()
    assert row.slid is True
    assert row.used is True


@requires_db
@pytest.mark.asyncio
async def test_expired_token_rejected() -> None:
    async with _web() as client:
        token = await _claim_admin(client)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE admin_sessions SET expires_at = now() - interval '1 second' "
                "WHERE token_hash = :h"
            ),
            {"h": admin_auth.token_sha256(token)},
        )
    async with _core() as core:
        r = await core.get("/v1/admin/config", headers=_bearer(token))
    assert r.status_code == 401


# ─── Session management endpoints ──────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_session_list_revoke_and_logout() -> None:
    async with _web() as client:
        token = await _claim_admin(client)
        admin_auth.LOGIN_BACKOFF.reset()
        second = (
            await client.post(
                "/api/auth/login", json={"password": STRONG_PW, "label": "phone"}
            )
        ).json()["token"]

        r = await client.get("/api/auth/sessions", headers=_bearer(token))
        assert r.status_code == 200
        sessions = r.json()["sessions"]
        assert len(sessions) == 2
        current = [s for s in sessions if s["current"]]
        assert len(current) == 1

        # Revoke with cookie only → 403 (mutation); with bearer → gone.
        other_hash = admin_auth.token_sha256(second)
        r = await client.request(
            "DELETE", f"/api/auth/sessions/{other_hash}",
            headers={"Authorization": ""},
        )
        assert r.status_code == 403  # setup cookie rides along; no bearer
        r = await client.request(
            "DELETE", f"/api/auth/sessions/{other_hash}", headers=_bearer(token)
        )
        assert r.status_code == 200
        # The revoked bearer no longer authenticates (bearer presence
        # takes precedence over the still-valid jar cookie).
        r = await client.get("/api/auth/status", headers=_bearer(second))
        assert r.json()["authenticated"] is False

        # Logout requires the bearer and kills the calling session.
        r = await client.post("/api/auth/logout", headers={"Authorization": ""})
        assert r.status_code == 401
        r = await client.post("/api/auth/logout", headers=_bearer(token))
        assert r.status_code == 200
    async with _core() as core:
        r = await core.get("/v1/admin/config", headers=_bearer(token))
        assert r.status_code == 401


@requires_db
@pytest.mark.asyncio
async def test_change_password_reverifies_old() -> None:
    async with _web() as client:
        token = await _claim_admin(client)
        r = await client.post(
            "/api/auth/password",
            json={"old_password": "not-the-password", "new_password": "a-new-password"},
            headers=_bearer(token),
        )
        assert r.status_code == 401
        r = await client.post(
            "/api/auth/password",
            json={"old_password": STRONG_PW, "new_password": "a-new-password"},
            headers=_bearer(token),
        )
        assert r.status_code == 200
        admin_auth.LOGIN_BACKOFF.reset()
        r = await client.post("/api/auth/login", json={"password": "a-new-password"})
        assert r.status_code == 200


# ─── Outbound-fetch tier on add-by-url (§7.3 / §4.8) ──────────────────────


@requires_db
@pytest.mark.asyncio
async def test_add_by_url_requires_admin_or_fulfiller_allowlist() -> None:
    async with _web() as client:
        token = await _claim_admin(client)
    body = {"room_id": "kitchen", "url": "https://example.com/watch?v=1"}
    async with _core() as core:
        # No admin, no fulfiller whose matcher claims the URL → 403.
        r = await core.post("/v1/admin/music/add-by-url", json=body)
        assert r.status_code == 403
        # An admin session passes outright, any URL.
        r = await core.post(
            "/v1/admin/music/add-by-url", json=body, headers=_bearer(token)
        )
        assert r.status_code == 200, r.text

        # A registered fulfiller's url_matcher allowlists its own hosts
        # for the unauthenticated daily path.
        ACQUISITIONS.register_fulfiller(
            "prov", kinds={"url"},
            url_matcher=lambda u: "media.example.org" in u,
        )
        try:
            ok = {"room_id": "kitchen", "url": "https://media.example.org/item/2"}
            r = await core.post("/v1/admin/music/add-by-url", json=ok)
            assert r.status_code == 200, r.text
            bad = {"room_id": "kitchen", "url": "https://internal.host/secret"}
            r = await core.post("/v1/admin/music/add-by-url", json=bad)
            assert r.status_code == 403
        finally:
            ACQUISITIONS.unregister_fulfiller("prov")


@requires_db
@pytest.mark.asyncio
async def test_add_by_url_unauthenticated_rate_limited(monkeypatch) -> None:
    async with _web() as client:
        token = await _claim_admin(client)
    ACQUISITIONS.register_fulfiller(
        "prov", kinds={"url"}, url_matcher=lambda u: "media.example.org" in u
    )
    monkeypatch.setattr(admin_auth.URL_FETCH_LIMITER, "max_per_window", 2)
    try:
        async with _core() as core:
            for i in range(2):
                r = await core.post(
                    "/v1/admin/music/add-by-url",
                    json={
                        "room_id": "kitchen",
                        "url": f"https://media.example.org/item/{i}",
                        "dedup_key": f"prov:{i}",
                    },
                )
                assert r.status_code == 200, r.text
            r = await core.post(
                "/v1/admin/music/add-by-url",
                json={
                    "room_id": "kitchen",
                    "url": "https://media.example.org/item/9",
                    "dedup_key": "prov:9",
                },
            )
            assert r.status_code == 429
            # …while the admin path is never throttled by this limiter.
            r = await core.post(
                "/v1/admin/music/add-by-url",
                json={
                    "room_id": "kitchen",
                    "url": "https://media.example.org/item/10",
                    "dedup_key": "prov:10",
                },
                headers=_bearer(token),
            )
            assert r.status_code == 200
    finally:
        ACQUISITIONS.unregister_fulfiller("prov")
