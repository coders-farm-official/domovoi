"""The security tier fails closed before first-run setup (CORE-5).

``require_admin_security`` is the Bearer-only gate that answers 501 until
an admin password exists — the posture plugin management has always had —
and it now covers config write, service restart, satellite code push,
pairing preseed / reset and satellite delete on BOTH processes. The daily
surface and admin READS keep the pre-setup LAN grace, and
``--reset-admin`` returns the install to the closed state rather than
reopening the tier.

DB-free: the gate matrix through a mini app over fake primitives.
DB-backed (``requires_db``): the real routes on the real core + web apps.
The structural half — WHICH routes wear the gate — is
``test_route_auth_matrix``.
"""

from __future__ import annotations

import pytest

from domovoi import admin_auth
from domovoi.tests.auth_testkit import (
    COOKIE,
    _db,  # noqa: F401 — fixture
    bearer,
    claim_admin,
    core_app,
    core_client,
    install_fake_db,
    mini_client,
    web_client,
)
from domovoi.tests.conftest import requires_db

# ═══ DB-FREE ══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_require_admin_security_matrix(monkeypatch) -> None:
    state = install_fake_db(monkeypatch, admin=False, sessions={"admin-token"})
    async with mini_client() as c:
        # Before setup: closed, whatever is presented.
        assert (await c.post("/security")).status_code == 501
        assert (await c.post("/security", headers=bearer("admin-token"))).status_code == 501
        assert (await c.get("/security-read")).status_code == 501
        state.admin = True
        assert (await c.post("/security", headers=bearer("admin-token"))).status_code == 200
        assert (await c.post("/security")).status_code == 401
        assert (await c.post("/security", headers=bearer("nope"))).status_code == 401
        assert (await c.get("/security-read", headers=bearer("admin-token"))).status_code == 200
        assert (await c.get("/security-read")).status_code == 401
    async with mini_client(cookies={COOKIE: "admin-token"}) as c:
        # The cookie renders the read, never a mutation.
        assert (await c.post("/security")).status_code == 403
        assert (await c.get("/security-read")).status_code == 200


@pytest.mark.asyncio
async def test_require_admin_security_fails_closed_when_the_check_raises(monkeypatch) -> None:
    install_fake_db(monkeypatch, admin=True, sessions={"admin-token"})

    async def boom(_s):
        raise RuntimeError("db down")

    monkeypatch.setattr(admin_auth, "has_admin_auth", boom)
    async with mini_client() as c:
        assert (await c.post("/security", headers=bearer("admin-token"))).status_code == 401


# ═══ DB-BACKED ════════════════════════════════════════════════════════════


@requires_db
@pytest.mark.asyncio
async def test_security_tier_returns_501_before_setup_and_daily_routes_pass(_db) -> None:
    closed = [
        ("POST", "/v1/admin/config", {"changes": {}}),
        ("POST", "/v1/admin/version/restart", None),
        ("POST", "/v1/admin/satellite/upgrade", {"room_id": "kitchen"}),
        ("POST", "/v1/admin/satellites/kitchen/pairing/preseed", {}),
        ("DELETE", "/v1/admin/satellites/kitchen/pairing", None),
        ("DELETE", "/v1/admin/satellites/kitchen", None),
    ]
    async with core_app.router.lifespan_context(core_app):
        core_app.state.active_sessions = {}
        async with core_client() as core:
            for method, path, body in closed:
                r = await core.request(method, path, json=body)
                assert r.status_code == 501, f"{method} {path}: {r.status_code} {r.text}"
            # The daily surface keeps its LAN grace.
            r = await core.post(
                "/v1/intent", json={"transcript": "what time is it", "room_id": "kitchen"}
            )
            assert r.status_code == 200, r.text
            r = await core.post("/v1/admin/music/pause/kitchen")
            assert r.status_code not in (401, 403, 501), r.text
            # And so do admin READS.
            assert (await core.get("/v1/admin/config")).status_code == 200
    # The web mirrors answer 501 themselves, before the hop.
    async with web_client() as web:
        web_closed = [
            ("PATCH", "/api/config/editable", {"changes": {}}),
            ("POST", "/api/config/version/restart", None),
            ("POST", "/api/satellites/kitchen/upgrade", None),
            ("POST", "/api/satellites/kitchen/pairing/reset", None),
            ("DELETE", "/api/satellites/kitchen", None),
        ]
        for method, path, body in web_closed:
            r = await web.request(method, path, json=body)
            assert r.status_code == 501, f"{method} {path}: {r.status_code} {r.text}"


@requires_db
@pytest.mark.asyncio
async def test_security_tier_opens_only_to_a_bearer_after_setup(_db) -> None:
    async with web_client() as web:
        admin = await claim_admin(web)
    async with core_client() as core:
        r = await core.post("/v1/admin/config", json={"changes": {}})
        assert r.status_code == 401
        r = await core.post("/v1/admin/config", json={"changes": {}}, headers=bearer(admin))
        assert r.status_code == 200, r.text
        r = await core.delete("/v1/admin/satellites/nowhere/pairing", headers=bearer(admin))
        assert r.status_code == 200, r.text
    # --reset-admin puts the install back before setup: the security tier
    # closes again instead of reopening under the grace.
    await admin_auth.reset_admin()
    async with core_client() as core:
        assert (await core.post("/v1/admin/config", json={"changes": {}})).status_code == 501
        assert (await core.get("/v1/admin/config")).status_code == 200
