"""A satellite upgrade is one at a time, and everyone can see it running.

Kamron, 2026-10-13: Settings → Satellites' Upgrade button stayed enabled
after a successful upgrade request. The core's upgrade request is fire and
forget (the satellite syncs, restarts and says hello again), so nothing on
the server said an upgrade was under way: a reload, another tab or the app
offered it again, and a second press sent a second ``upgrade`` frame into
a sync already running.

The core now keeps a record per room (domovoi/satellite_upgrades.py):

* ``POST /v1/admin/satellite/upgrade`` records it BEFORE the frame goes
  out, answers with it (``upgrade``), and refuses a second request for
  the room while it runs (409). A frame that never went out (502) leaves
  no record.
* The satellite's next hello ends it: ``done`` on the expected sha,
  ``rolled_back`` on another; a satellite that never comes back within
  the window is ``no_news``. Finished records stay a while, then go.
* ``GET /v1/admin/snapshot`` carries ``satellite_upgrades``, and the web's
  satellite rows carry ``upgrade``, which the dashboard and the app lock
  the button on.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from domovoi import admin_auth
from domovoi import satellite_upgrades as su
from domovoi.db.session import engine, session_scope
from domovoi.streaming import StreamSession
from domovoi.tests.conftest import TABLES_TO_TRUNCATE, requires_db

# ─── the record itself ────────────────────────────────────────────────────


def _started(table, room="kitchen", *, now=1000.0, window=300.0, expected="new1234", from_sha="old1234"):
    return su.start(table, room, expected_sha=expected, from_sha=from_sha, now=now, window=window)


def test_the_window_covers_a_rollback_and_never_shrinks_below_three_minutes() -> None:
    assert su.window_sec(90) == 300.0
    assert su.window_sec(10) == 180.0
    assert su.window_sec("nonsense") == 300.0


def test_a_started_upgrade_is_running_until_its_window_ends() -> None:
    table: dict = {}
    rec = _started(table)
    assert su.running(table, "kitchen", 1000.0) is rec
    assert su.running(table, "kitchen", 1299.0) is rec
    assert su.running(table, "garage", 1000.0) is None
    # Past the window: no news, and nothing is running any more.
    assert su.running(table, "kitchen", 1300.0) is None
    assert table["kitchen"]["state"] == su.NO_NEWS
    assert table["kitchen"]["finished_at"] == 1300.0


def test_the_hello_on_the_expected_sha_is_done_and_another_is_a_rollback() -> None:
    table: dict = {}
    _started(table)
    su.on_hello(table, "kitchen", "new1234", 1050.0)
    assert table["kitchen"]["state"] == su.DONE
    assert table["kitchen"]["back_sha"] == "new1234"
    assert table["kitchen"]["finished_at"] == 1050.0
    # The new code came up, then the watchdog put the old code back.
    su.on_hello(table, "kitchen", "old1234", 1200.0)
    assert table["kitchen"]["state"] == su.ROLLED_BACK
    assert table["kitchen"]["back_sha"] == "old1234"
    # One more reconnect on the same code is no news: the time stays.
    su.on_hello(table, "kitchen", "old1234", 1210.0)
    assert table["kitchen"]["finished_at"] == 1200.0


def test_a_satellite_that_never_synced_before_and_comes_back_unlabelled_rolled_back() -> None:
    table: dict = {}
    _started(table, from_sha=None)
    su.on_hello(table, "kitchen", None, 1050.0)
    assert table["kitchen"]["state"] == su.ROLLED_BACK


def test_a_late_hello_still_says_how_it_went_then_records_age_out() -> None:
    table: dict = {}
    _started(table)
    assert su.running(table, "kitchen", 1400.0) is None          # no_news at 1300
    su.on_hello(table, "kitchen", "new1234", 1450.0)             # ... then it came back
    assert table["kitchen"]["state"] == su.DONE
    view = su.view(table, 1450.0 + su.KEEP_FINISHED_SEC)
    assert view["kitchen"]["state"] == "done"
    assert su.view(table, 1451.0 + su.KEEP_FINISHED_SEC) == {}
    assert table == {}
    # A hello for a room nobody upgraded is nothing.
    su.on_hello(table, "kitchen", "new1234", 5000.0)
    assert table == {}


def test_the_view_is_plain_json_with_iso_times() -> None:
    table: dict = {}
    _started(table, now=1_760_000_000.0)
    v = su.view(table, 1_760_000_010.0)["kitchen"]
    assert v["state"] == "running"
    assert v["expected_sha"] == "new1234" and v["from_sha"] == "old1234" and v["back_sha"] is None
    assert datetime.fromisoformat(v["started_at"]).timestamp() == 1_760_000_000.0
    assert datetime.fromisoformat(v["deadline_at"]).timestamp() == 1_760_000_300.0
    assert v["finished_at"] is None


def test_forget_drops_only_its_own_record() -> None:
    table: dict = {}
    first = _started(table)
    second = _started(table, now=1001.0)
    su.forget(table, "kitchen", first)
    assert table["kitchen"] is second
    su.forget(table, "kitchen", second)
    assert table == {}


# ─── the hello ends it ────────────────────────────────────────────────────


@pytest.fixture
def _paired_hello(monkeypatch: pytest.MonkeyPatch):
    async def _accept(self, ctrl):
        self.token_authenticated = True
        return True

    monkeypatch.setattr(StreamSession, "_validate_pairing", _accept)


class _State:
    def __init__(self, upgrades=None) -> None:
        self.satellite_full_duplex: dict = {}
        self.satellite_synced_sha: dict = {}
        self.satellite_sat_type: dict = {}
        self.satellite_mic_enabled: dict = {}
        if upgrades is not None:
            self.satellite_upgrades = upgrades


class _WS:
    def __init__(self, upgrades=None) -> None:
        self.app = type("App", (), {})()
        self.app.state = _State(upgrades)


@pytest.mark.asyncio
@pytest.mark.usefixtures("_paired_hello")
async def test_the_satellites_hello_ends_the_upgrade() -> None:
    import time

    table: dict = {}
    su.start(table, "kitchen", expected_sha="new1234", from_sha="old1234",
             now=time.time(), window=300.0)
    ws = _WS(table)
    await StreamSession(ws, "kitchen")._on_control({"type": "hello", "synced_sha": "new1234"})
    assert table["kitchen"]["state"] == su.DONE
    assert ws.app.state.satellite_synced_sha["kitchen"] == "new1234"


@pytest.mark.asyncio
@pytest.mark.usefixtures("_paired_hello")
async def test_a_hello_without_any_upgrade_state_still_works() -> None:
    ws = _WS()                     # an app state from before the record
    await StreamSession(ws, "kitchen")._on_control({"type": "hello", "synced_sha": "abc"})
    assert ws.app.state.satellite_synced_sha["kitchen"] == "abc"


# ─── the web's satellite row carries it ──────────────────────────────────


async def _row_for(snapshot: dict):
    from web.backend.api import satellites as web_sats

    row = {"room_id": "kitchen", "control_port": None, "http_port": None, "last_connected_at": None}
    return await web_sats._satellite_for(row, snapshot, {}, {}, {})


@pytest.mark.asyncio
async def test_the_web_row_carries_the_upgrade() -> None:
    table: dict = {}
    _started(table, now=1_760_000_000.0)
    snap = {"active_rooms": [], "satellite_upgrades": su.view(table, 1_760_000_005.0)}
    sat = await _row_for(snap)
    assert sat.upgrade is not None
    assert sat.upgrade.state == "running"
    assert sat.upgrade.expected_sha == "new1234"
    assert sat.upgrade.started_at.timestamp() == 1_760_000_000.0


@pytest.mark.asyncio
async def test_the_web_row_has_no_upgrade_from_an_older_core_or_a_bad_entry() -> None:
    assert (await _row_for({})).upgrade is None
    assert (await _row_for({"satellite_upgrades": {"kitchen": "running"}})).upgrade is None
    assert (await _row_for({"satellite_upgrades": {"kitchen": {"expected_sha": "x"}}})).upgrade is None
    bad_time = {"satellite_upgrades": {"kitchen": {"state": "running", "started_at": "yesterday"}}}
    assert (await _row_for(bad_time)).upgrade is None


# ─── the endpoint: one at a time ─────────────────────────────────────────


@pytest_asyncio.fixture
async def _clean_db():
    async with engine.begin() as conn:
        await conn.execute(
            text(f"TRUNCATE {', '.join(TABLES_TO_TRUNCATE)} RESTART IDENTITY CASCADE")
        )
    yield


async def _admin_headers() -> dict[str, str]:
    async with session_scope() as s:
        await admin_auth.set_password(s, "correct-horse-battery")
        token = await admin_auth.create_session(s, "test")
    return {"Authorization": f"Bearer {token}"}


def _fake_session(room: str, upgrade_mock) -> object:
    return type("FakeSession", (), {"room_id": room, "request_upgrade": upgrade_mock})()


@requires_db
@pytest.mark.asyncio
@pytest.mark.usefixtures("_clean_db")
async def test_a_second_upgrade_while_one_runs_is_refused() -> None:
    from domovoi.main import app

    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        upgrade_mock = AsyncMock()
        app.state.active_sessions = {"kitchen": _fake_session("kitchen", upgrade_mock),
                                     "garage": _fake_session("garage", AsyncMock())}
        app.state.satellite_synced_sha = {"kitchen": "old1234"}
        headers = await _admin_headers()
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            first = await client.post("/v1/admin/satellite/upgrade", json={"room_id": "kitchen"},
                                      headers=headers)
            second = await client.post("/v1/admin/satellite/upgrade", json={"room_id": "kitchen"},
                                       headers=headers)
            other_room = await client.post("/v1/admin/satellite/upgrade", json={"room_id": "garage"},
                                           headers=headers)
            snap = (await client.get("/v1/admin/snapshot")).json()
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["requested"] is True
    assert body["upgrade"]["state"] == "running"
    assert body["upgrade"]["expected_sha"] == body["expected_sha"]
    assert body["upgrade"]["from_sha"] == "old1234"
    assert second.status_code == 409, second.text
    assert "already upgrading" in second.json()["detail"]
    upgrade_mock.assert_awaited_once()             # one frame, not two
    assert other_room.status_code == 200           # per room, not per house
    assert snap["satellite_upgrades"]["kitchen"]["state"] == "running"
    assert snap["satellite_upgrades"]["kitchen"]["started_at"] == body["upgrade"]["started_at"]
    assert snap["satellite_upgrades"]["garage"]["state"] == "running"


@requires_db
@pytest.mark.asyncio
@pytest.mark.usefixtures("_clean_db")
async def test_a_frame_that_never_went_out_leaves_nothing_running() -> None:
    from domovoi.main import app

    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        broken = AsyncMock(side_effect=RuntimeError("socket closed"))
        app.state.active_sessions = {"kitchen": _fake_session("kitchen", broken)}
        headers = await _admin_headers()
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            failed = await client.post("/v1/admin/satellite/upgrade", json={"room_id": "kitchen"},
                                       headers=headers)
            snap = (await client.get("/v1/admin/snapshot")).json()
            app.state.active_sessions = {"kitchen": _fake_session("kitchen", AsyncMock())}
            retry = await client.post("/v1/admin/satellite/upgrade", json={"room_id": "kitchen"},
                                      headers=headers)
    assert failed.status_code == 502
    assert snap["satellite_upgrades"] == {}
    assert retry.status_code == 200, retry.text


@requires_db
@pytest.mark.asyncio
@pytest.mark.usefixtures("_clean_db")
async def test_after_the_satellite_is_back_it_can_be_upgraded_again() -> None:
    from domovoi.main import app

    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        app.state.active_sessions = {"kitchen": _fake_session("kitchen", AsyncMock())}
        headers = await _admin_headers()
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            first = await client.post("/v1/admin/satellite/upgrade", json={"room_id": "kitchen"},
                                      headers=headers)
            import time

            su.on_hello(app.state.satellite_upgrades, "kitchen", "something-else", time.time())
            snap = (await client.get("/v1/admin/snapshot")).json()
            again = await client.post("/v1/admin/satellite/upgrade", json={"room_id": "kitchen"},
                                      headers=headers)
    assert first.status_code == 200
    assert snap["satellite_upgrades"]["kitchen"]["state"] == "rolled_back"
    assert again.status_code == 200, again.text
