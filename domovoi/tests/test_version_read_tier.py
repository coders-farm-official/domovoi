"""`GET /v1/admin/version` is a household read, not a LAN one (CORE-21,
audit A8-04).

The route used to answer anything that reached port 6370. Beside the
running commit it carries the update unit's last run — its error text (the
tail of a failed step's pip, docker, git or pg_dump output) and step log —
whether this host can restart itself, the restart mode and the rolled-back
SHA. Now it wears the device tier's read half: the household token, an
admin Bearer, or the dashboard cookie. The dashboard's version panel sends
the token and the web hop forwards it; the update installer reads the token
file as the service user (scripts/linux/tests/test-install-update-unit.sh
covers that side).

DB-free: the auth primitives are faked (``install_fake_db``) and the
version probe is stubbed, because the gate answers before either matters.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from domovoi import admin_auth, git_version
from domovoi.main import app as core_app
from domovoi.tests.auth_testkit import (
    COOKIE,
    HEADER,
    bearer,
    install_fake_db,
    web_client,
)

ADMIN_TOKEN = "admin-token"
DEVICE_TOKEN = "device-token"
VERSION = "/v1/admin/version"

STATE = {
    "sha": "abc1234",
    "running_sha": "abc1234",
    "checkout_sha": "abc1234",
    "restart_required": False,
    "restart_capable": True,
    "restart_mode": "update",
    "last_update": {"status": "failed", "error": "pip: resolution failed for ..."},
    "bad_sha": "b" * 40,
}


def _client() -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=core_app, raise_app_exceptions=False),
        base_url="http://test",
    )


@pytest.fixture
def version_probe(monkeypatch) -> None:
    async def fake_state() -> dict[str, Any]:
        return dict(STATE)

    monkeypatch.setattr(git_version, "version_state", fake_state)


def _gates(method: str, path: str) -> list:
    from domovoi.tests.route_walk import iter_route_contexts

    def calls(dependant):
        for dep in getattr(dependant, "dependencies", ()) if dependant else ():
            if dep.call is not None:
                yield dep.call
            yield from calls(dep)

    for rc in iter_route_contexts(core_app.routes):
        if getattr(rc, "path", None) == path and method in (getattr(rc, "methods", None) or set()):
            return list(calls(getattr(rc, "dependant", None)))
    raise AssertionError(f"{method} {path} is not a core route")


def test_the_version_read_is_on_the_device_read_tier() -> None:
    assert admin_auth.require_device_read in _gates("GET", VERSION)


@pytest.mark.asyncio
async def test_the_version_read_refuses_a_caller_with_no_credential(
    monkeypatch, version_probe
) -> None:
    """The audit's request, after setup: no header at all. Nothing of the
    update record — error text, steps, bad SHA — comes back."""
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN)
    async with _client() as c:
        r = await c.get(VERSION)
        assert r.status_code == 401, r.text
        assert "last_update" not in r.text and "bad_sha" not in r.text
        # A wrong token is no better than none.
        assert (await c.get(VERSION, headers={HEADER: "not-it"})).status_code == 401


@pytest.mark.asyncio
async def test_the_version_read_takes_the_token_the_bearer_or_the_cookie(
    monkeypatch, version_probe
) -> None:
    """Every credential the dashboard, the app and the installer hold."""
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN)
    async with _client() as c:
        r = await c.get(VERSION, headers={HEADER: DEVICE_TOKEN})
        assert r.status_code == 200, r.text
        assert r.json()["running_sha"] == "abc1234"
        assert (await c.get(VERSION, headers=bearer(ADMIN_TOKEN))).status_code == 200
    async with _client() as c:
        c.cookies.set(COOKIE, ADMIN_TOKEN)
        assert (await c.get(VERSION)).status_code == 200


@pytest.mark.asyncio
async def test_the_version_read_keeps_the_pre_setup_grace(monkeypatch, version_probe) -> None:
    """A fresh box's dashboard has no credential yet and still shows what
    it runs, like the rest of the daily surface."""
    install_fake_db(monkeypatch, admin=False)
    async with _client() as c:
        assert (await c.get(VERSION)).status_code == 200


# ─── The web hop forwards the caller's credential; the core decides ──────


@pytest.mark.asyncio
async def test_the_web_hop_forwards_the_credential_and_bridges_the_refusal(
    monkeypatch,
) -> None:
    from web.backend.api import config as config_api

    seen: list[dict[str, str]] = []

    async def fake_core(path, timeout=10.0, headers=None):
        assert path == VERSION
        seen.append(dict(headers or {}))
        if (headers or {}).get(HEADER) != DEVICE_TOKEN:
            return 401, {"detail": f"{HEADER} or admin session required"}
        return 200, dict(STATE, plugins_pending_restart=[])

    monkeypatch.setattr(config_api, "get_admin", fake_core)
    async with web_client() as client:
        refused = await client.get("/api/config/version")
        allowed = await client.get("/api/config/version", headers={HEADER: DEVICE_TOKEN})
    assert refused.status_code == 401, refused.text
    assert "bad_sha" not in refused.text
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["running_sha"] == "abc1234"
    assert HEADER not in seen[0] and seen[1][HEADER] == DEVICE_TOKEN
