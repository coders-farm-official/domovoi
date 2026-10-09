"""Nobody approves a parked satellite before first-run setup (REV-32,
2026-10-08).

With strict pairing the default, a satellite that connects to a core
nobody has claimed yet parks and waits for approval. The approve route was
admin tier WITH the pre-setup grace, so on an unclaimed core any LAN host
could park a device of its own and approve it with no credential at all
(seen by the verification lane, A1-05) — and the pairing it minted
outlived the setup. Approve and reject are security tier now, at both
hops: Bearer-only, and 501 until an admin password exists.

DB-free: the auth primitives and the approval repository are faked.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from domovoi import main as core_main
from domovoi.main import app as core_app
from domovoi.tests.auth_testkit import HEADER, bearer, install_fake_db, web_app

ADMIN_TOKEN = "approval-admin-session"
DEVICE_TOKEN = "approval-household-token"
ROOM = "attic"


class _FakeApprovals:
    calls: list[tuple[str, str]] = []

    def __init__(self, session: Any) -> None:
        pass

    async def approve(self, room_id: str, code: str) -> str:
        type(self).calls.append(("approve", room_id))
        return "approved"

    async def reject(self, room_id: str) -> bool:
        type(self).calls.append(("reject", room_id))
        return True


@pytest.fixture
def repo(monkeypatch):
    @asynccontextmanager
    async def fake_scope():
        yield object()

    monkeypatch.setattr(core_main, "session_scope", fake_scope)
    monkeypatch.setattr(core_main, "SatelliteApprovalRepository", _FakeApprovals)
    core_main.APPROVAL_CODE_LIMITER.reset()
    _FakeApprovals.calls = []
    yield _FakeApprovals
    core_main.APPROVAL_CODE_LIMITER.reset()


async def _core_post(path: str, headers: dict[str, str] | None = None, json=None):
    async with AsyncClient(transport=ASGITransport(app=core_app), base_url="http://test") as c:
        return await c.post(path, headers=headers or {}, json=json)


APPROVE = f"/v1/admin/satellites/approvals/{ROOM}/approve"
REJECT = f"/v1/admin/satellites/approvals/{ROOM}/reject"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [APPROVE, REJECT], ids=["approve", "reject"])
@pytest.mark.parametrize(
    "headers", [{}, {HEADER: DEVICE_TOKEN}, bearer("anything")],
    ids=["nothing", "household-token", "a-bearer"],
)
async def test_an_unclaimed_core_approves_and_rejects_nothing(
    monkeypatch, repo, path, headers
) -> None:
    install_fake_db(monkeypatch, admin=False, device_token=DEVICE_TOKEN)
    r = await _core_post(path, headers, json={"code": "123456"})
    assert r.status_code == 501, r.text
    assert "first-run" in r.json()["detail"]
    assert repo.calls == []


@pytest.mark.asyncio
async def test_after_setup_an_admin_still_approves(monkeypatch, repo) -> None:
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN})
    r = await _core_post(APPROVE, bearer(ADMIN_TOKEN), json={"code": "123456"})
    assert r.status_code == 200, r.text
    assert r.json() == {"approved": True, "room_id": ROOM}
    r = await _core_post(REJECT, bearer(ADMIN_TOKEN))
    assert r.status_code == 200, r.text
    assert repo.calls == [("approve", ROOM), ("reject", ROOM)]


@pytest.mark.asyncio
async def test_after_setup_the_household_token_does_not_approve(monkeypatch, repo) -> None:
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN)
    r = await _core_post(APPROVE, {HEADER: DEVICE_TOKEN}, json={"code": "123456"})
    assert r.status_code == 401, r.text
    assert repo.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("verb", ["approve", "reject"])
async def test_the_dashboard_hop_refuses_before_setup_too(monkeypatch, verb) -> None:
    """The refusal lands at the first hop, before anything is forwarded."""
    from web.backend.api import satellites as sat_api

    install_fake_db(monkeypatch, admin=False)
    forwarded: list[str] = []

    async def post_admin(path, body=None, headers=None, **kw):
        forwarded.append(path)
        return 200, {}

    monkeypatch.setattr(sat_api, "post_admin", post_admin)
    async with AsyncClient(
        transport=ASGITransport(app=web_app), base_url="http://test",
        headers={"X-Requested-With": "domovoi-tests"},
    ) as c:
        r = await c.post(f"/api/satellites/approvals/{ROOM}/{verb}", json={"code": "123456"})
    assert r.status_code == 501, r.text
    assert forwarded == []
