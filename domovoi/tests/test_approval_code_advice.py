"""What an operator who missed the approval code is told (F-049).

The six digits are pinned at BOTH ends, deliberately:

* ``SatelliteApprovalRepository.request`` upserts with
  ``code = COALESCE(satellite_approvals.code, EXCLUDED.code)`` — a retry
  keeps whatever the customer was already shown;
* the Pi persists its code to ``~/.domovoi/approval_code`` and re-offers
  it on every hello (``satellite/client.py`` ``_remember_approval_code``).

So restarting the satellite re-announces the SAME code, and rejecting the
row on the dashboard only lets the device recreate it with the same
number. The 429 used to advise a power-cycle "for a fresh code", which is
the one thing that cannot work; Kamron lost a hardware setup session to
it. The pinning is correct and stays — only the words change.

This module pins the words, so nobody restores the old advice:

* the core's 429 states the real throttle (5 per room per 300 s, in
  memory, self-clearing) and the three routes that actually recover a
  missed code — wait, the Pi's sidecar, the server's row;
* neither the core nor the dashboard offers a power-cycle "for a fresh
  code" anywhere;
* the Satellites page no longer renders the device's RECONNECT count as
  "N attempts" next to a code box, where it read as a budget running down.

No database: the approval repository is faked, exactly as in
test_satellite_approval_gate.py, and the JSX halves are source assertions.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from domovoi import main as core_main
from domovoi.main import app as core_app
from domovoi.tests.auth_testkit import bearer, install_fake_db

ADMIN_TOKEN = "admin-token"
APPROVE_URL = "/v1/admin/satellites/approvals/kitchen/approve"

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC = REPO_ROOT / "web" / "static"


class _FakeApprovals:
    """Never reached by a throttled request; present so the route can be
    driven without Postgres when it is."""

    def __init__(self, session: Any) -> None:
        pass

    async def approve(self, room_id: str, code: str) -> str:
        return "mismatch"


@pytest.fixture
def throttled(monkeypatch):
    @asynccontextmanager
    async def fake_scope():
        yield object()

    monkeypatch.setattr(core_main, "session_scope", fake_scope)
    monkeypatch.setattr(core_main, "SatelliteApprovalRepository", _FakeApprovals)
    core_main.APPROVAL_CODE_LIMITER.reset()
    yield
    core_main.APPROVAL_CODE_LIMITER.reset()


async def _detail_of_the_throttled_attempt() -> str:
    """Spend the budget for one room, then return the 429's detail."""
    transport = ASGITransport(app=core_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        for _ in range(core_main.APPROVAL_CODE_MAX_ATTEMPTS):
            await c.post(
                APPROVE_URL, json={"code": "000000"}, headers=bearer(ADMIN_TOKEN)
            )
        r = await c.post(
            APPROVE_URL, json={"code": "000000"}, headers=bearer(ADMIN_TOKEN)
        )
    assert r.status_code == 429, r.text
    return r.json()["detail"]


@pytest.mark.asyncio
async def test_the_throttle_message_states_the_real_throttle(
    monkeypatch, throttled
) -> None:
    """The numbers come from the constants, so the sentence cannot drift
    away from the limiter it describes."""
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN})
    detail = await _detail_of_the_throttled_attempt()
    assert str(core_main.APPROVAL_CODE_MAX_ATTEMPTS) in detail
    assert str(int(core_main.APPROVAL_CODE_WINDOW_SEC)) in detail
    # In memory, so it clears on its own — the operator is not waiting on
    # anyone with a shell.
    assert "memory" in detail
    assert "clears itself" in detail


@pytest.mark.asyncio
async def test_the_throttle_message_names_the_three_ways_back_to_the_code(
    monkeypatch, throttled
) -> None:
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN})
    detail = await _detail_of_the_throttled_attempt()
    # 1. Wait: the satellite re-announces on every reconnect
    #    (satellite/client.py, the awaiting_approval branch).
    assert "every retry" in detail
    # 2. The Pi's sidecar.
    assert "~/.domovoi/approval_code" in detail
    # 3. The row on the server.
    assert "satellite_approvals" in detail
    # And it does not claim the code can change.
    assert "does not change" in detail


@pytest.mark.asyncio
async def test_no_message_offers_a_power_cycle_for_a_fresh_code(
    monkeypatch, throttled
) -> None:
    """A power-cycle cannot mint a new code — the upsert COALESCEs and the
    Pi replays its sidecar. Nothing may suggest otherwise."""
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN})
    detail = await _detail_of_the_throttled_attempt()
    lowered = detail.lower()
    assert "power-cycle" not in lowered
    assert "fresh code" not in lowered


def test_the_dashboard_never_offers_a_power_cycle_for_a_fresh_code() -> None:
    for jsx in sorted(STATIC.glob("*.jsx")):
        assert "fresh code" not in jsx.read_text(encoding="utf-8"), jsx.name


def test_the_reconnect_count_is_not_rendered_as_a_budget() -> None:
    """``· 20 attempts`` beside a code box read as "20 tries left, going
    down" — it is neither a limit nor the approval-code counter."""
    src = (STATIC / "satellites.jsx").read_text(encoding="utf-8")
    assert "${a.attempts} attempts" not in src
    assert "asked {a.attempts} times" in src
    # Said plainly for anyone who hovers it, too.
    assert "not a limit" in src
