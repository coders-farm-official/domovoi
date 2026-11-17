"""Plugin websocket routes on the core (SDK 1.11, design §4.11).

A ``@device_endpoint`` ``@router.websocket`` route on a plugin's core
router is served behind the per-slug gate's websocket form, and every
check runs BEFORE the socket is accepted, so a refused upgrade never
reaches the plugin's function:

* the plugin is enabled and this is its live mount;
* ``Host`` names this box on the LAN (the HTTP routes' DNS-rebinding rule)
  and a browser's ``Origin`` is a LAN page (the core's own sockets' rule);
* the HTTP device tier's credential rule, exactly: the household token
  (``X-Device-Token``, or ``?token=`` from a browser) or an admin Bearer,
  the pre-setup LAN grace, the dashboard cookie alone refused, the
  wrong-token backoff;
* room under the per-plugin and per-device socket caps.

While a socket is open it is held to the same rule: the credential that
opened it is re-read (a rotated token, an admin session that idled out or
signed out, the end of the pre-setup grace close it; the socket never
counts as admin activity), each message is held to the request-body cap
and the message rate, and disabling or re-mounting the plugin closes it.
Anything else — an unmarked or ``@open_endpoint`` socket, a Starlette
``WebSocketRoute`` — is refused at load and at mount.

The ``?token=`` query never reaches a log line: uvicorn writes the upgrade
with its query string, and the record factory (``domovoi.log_privacy``)
blanks it, whatever characters the household token holds.

DB-free except the last tests (``requires_db``): the auth primitives are
faked at the seam ``auth_testkit.install_fake_db`` patches.
"""

from __future__ import annotations

import io
import logging
import socket
import threading
import time
import urllib.parse

import anyio
import pytest
from fastapi import APIRouter, FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from domovoi import admin_auth, log_privacy, plugin_http, webkit
from domovoi.plugins_runtime.contracts import ContractReport, check_route_tiers
from domovoi.tests.auth_testkit import COOKIE, HEADER, bearer, install_fake_db

SLUG = "wsdemo"
ADMIN = "admin-session"
TOKEN = "household-token"
PATH = f"/v1/plugins/{SLUG}/live"


def _router() -> APIRouter:
    router = APIRouter()

    @router.websocket("/live")
    @webkit.device_endpoint
    async def live(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_json({"hello": True})
        try:
            while True:
                msg = await websocket.receive_text()
                await websocket.send_text(f"echo:{msg}")
        except WebSocketDisconnect:
            pass

    return router


def _forget() -> None:
    plugin_http._plugin_enabled.pop(SLUG, None)
    plugin_http._mounted.pop(SLUG, None)
    plugin_http._open_sockets.pop(SLUG, None)


@pytest.fixture
def app():
    _forget()
    app = FastAPI()
    plugin_http.mount_plugin_router(app, SLUG, _router())
    try:
        yield app
    finally:
        _forget()


@pytest.fixture
def claimed(monkeypatch):
    return install_fake_db(monkeypatch, admin=True, sessions={ADMIN}, device_token=TOKEN)


@pytest.fixture
def fast_recheck(monkeypatch):
    monkeypatch.setattr(plugin_http, "WS_RECHECK_SEC", 0.05)


def _next(ws, timeout: float = 5.0) -> dict:
    """The next message the server sent, or fail after ``timeout`` (a test
    waiting on a close that never comes must not hang the run)."""

    async def _receive() -> dict:
        with anyio.fail_after(timeout):
            return await ws._send_rx.receive()

    return ws.portal.call(_receive)


def _closed_with(ws, code: int, timeout: float = 5.0) -> None:
    """Read until the server closes the socket; the close must carry
    ``code``."""
    deadline = time.monotonic() + timeout
    while True:
        message = _next(ws, max(0.1, deadline - time.monotonic()))
        if message["type"] == "websocket.close":
            assert message.get("code") == code, message
            return


def _refused(client: TestClient, url: str, code: int = 1008, **kw) -> None:
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(url, **kw) as ws:
            ws.receive_json()
    assert exc.value.code == code, exc.value


def _wait_for(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


# ─── who may open one: the HTTP device tier's rule ────────────────────────


def test_the_household_token_in_the_query_opens_the_socket(app, claimed) -> None:
    with TestClient(app).websocket_connect(f"{PATH}?token={TOKEN}") as ws:
        assert ws.receive_json() == {"hello": True}
        assert plugin_http.open_socket_count(SLUG) == 1
        ws.send_text("hi")
        assert ws.receive_text() == "echo:hi"
    _wait_for(lambda: plugin_http.open_socket_count(SLUG) == 0)


def test_the_header_and_an_admin_bearer_open_it_too(app, claimed) -> None:
    client = TestClient(app)
    with client.websocket_connect(PATH, headers={HEADER: TOKEN}) as ws:
        assert ws.receive_json() == {"hello": True}
    with client.websocket_connect(PATH, headers=bearer(ADMIN)) as ws:
        assert ws.receive_json() == {"hello": True}


def test_no_a_wrong_or_a_cookie_only_credential_is_refused_before_accept(
    app, claimed
) -> None:
    client = TestClient(app)
    _refused(client, PATH)
    _refused(client, f"{PATH}?token=wrong")
    # The dashboard cookie reaches every port of its host, so a page on
    # another LAN service could open the socket with it: refused, as the
    # HTTP device tier refuses it for a write.
    _refused(client, PATH, headers={"cookie": f"{COOKIE}={ADMIN}"})
    # The media reads' ?device_token= is not a socket credential.
    _refused(client, f"{PATH}?device_token={TOKEN}")
    assert plugin_http.open_socket_count(SLUG) == 0


def test_wrong_tokens_share_the_device_tier_backoff(app, claimed) -> None:
    """Guesses over the socket are charged to the same per-source ledger
    as the header on HTTP; once it bites, even the right token waits."""
    client = TestClient(app)
    for _ in range(12):
        _refused(client, f"{PATH}?token=guess")
    assert admin_auth.DEVICE_TOKEN_BACKOFF.retry_after("testclient") > 0
    _refused(client, f"{PATH}?token={TOKEN}")


def test_the_pre_setup_grace_applies(app, monkeypatch) -> None:
    install_fake_db(monkeypatch, admin=False, device_token=TOKEN)
    with TestClient(app).websocket_connect(PATH) as ws:
        assert ws.receive_json() == {"hello": True}


def test_an_idle_expired_admin_session_is_refused_at_the_upgrade(app, claimed) -> None:
    claimed.sessions.discard(ADMIN)   # idled out / signed out
    _refused(TestClient(app), PATH, headers=bearer(ADMIN))


def test_a_page_off_the_lan_is_refused_and_a_lan_page_is_not(app, claimed) -> None:
    client = TestClient(app)
    _refused(client, f"{PATH}?token={TOKEN}", headers={"origin": "https://evil.example"})
    _refused(client, f"{PATH}?token={TOKEN}", headers={"origin": "null"})
    with client.websocket_connect(
        f"{PATH}?token={TOKEN}", headers={"origin": "http://192.168.1.20:6369"}
    ) as ws:
        assert ws.receive_json() == {"hello": True}


def test_a_host_that_is_not_this_box_is_refused(app, claimed) -> None:
    """The HTTP routes' Host rule (DNS rebinding) holds for the socket
    too, though LanHostMiddleware lets websocket scopes through."""
    client = TestClient(app)
    _refused(client, f"ws://domovoi.attacker.example{PATH}?token={TOKEN}")
    with client.websocket_connect(f"ws://192.168.1.20:6370{PATH}?token={TOKEN}") as ws:
        assert ws.receive_json() == {"hello": True}


def test_a_disabled_plugin_refuses_the_upgrade(app, claimed) -> None:
    plugin_http.set_plugin_enabled(SLUG, False)
    _refused(TestClient(app), f"{PATH}?token={TOKEN}")


def test_the_credential_check_failing_is_try_again_not_wrong(app, claimed, monkeypatch) -> None:
    async def down(_ws, _session=None):
        raise admin_auth.AuthCheckUnavailable()

    monkeypatch.setattr(admin_auth, "check_device_websocket", down)
    _refused(TestClient(app), f"{PATH}?token={TOKEN}", code=1013)


# ─── while it is open: the credential is read again ───────────────────────


def test_an_admin_socket_never_counts_as_admin_activity(app, claimed) -> None:
    """A socket must not keep an admin session alive: neither the upgrade
    nor anything sent over it restarts the session's idle hour."""
    with TestClient(app).websocket_connect(PATH, headers=bearer(ADMIN)) as ws:
        assert ws.receive_json() == {"hello": True}
        for i in range(5):
            ws.send_text(str(i))
            assert ws.receive_text() == f"echo:{i}"
    assert claimed.acted == []
    assert admin_auth.is_admin_activity(
        type("Conn", (), {"scope": {"type": "websocket"}})()
    ) is False


def test_an_admin_socket_closes_once_its_session_ends(app, claimed, fast_recheck) -> None:
    """Decision: refused at the upgrade once the session has idled out
    (above), AND closed (1008) within a recheck once it idles out, is
    signed out or is revoked while the socket is open. An HTTP caller is
    judged on every request; a socket must not carry an ended session's
    standing on."""
    with TestClient(app).websocket_connect(PATH, headers=bearer(ADMIN)) as ws:
        assert ws.receive_json() == {"hello": True}
        time.sleep(0.2)   # a few rechecks pass while the session is live
        ws.send_text("still")
        assert ws.receive_text() == "echo:still"
        claimed.sessions.discard(ADMIN)
        _closed_with(ws, 1008)
    _wait_for(lambda: plugin_http.open_socket_count(SLUG) == 0)


def test_a_rotated_household_token_closes_its_sockets(app, claimed, fast_recheck) -> None:
    with TestClient(app).websocket_connect(f"{PATH}?token={TOKEN}") as ws:
        assert ws.receive_json() == {"hello": True}
        claimed.device_token = "a-new-household-token"
        _closed_with(ws, 1008)


def test_setup_ends_the_pre_setup_grace_for_an_open_socket(app, monkeypatch, fast_recheck) -> None:
    state = install_fake_db(monkeypatch, admin=False, device_token=TOKEN)
    with TestClient(app).websocket_connect(PATH) as ws:
        assert ws.receive_json() == {"hello": True}
        state.admin = True
        _closed_with(ws, 1008)


def test_a_database_blip_does_not_close_it_but_an_outage_does(
    app, claimed, fast_recheck, monkeypatch
) -> None:
    calls: list[int] = []
    real = admin_auth.device_websocket_still_admitted

    async def flaky(ws, admitted):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("db blip")
        if len(calls) < 4:
            return await real(ws, admitted)
        raise RuntimeError("db down")

    monkeypatch.setattr(admin_auth, "device_websocket_still_admitted", flaky)
    with TestClient(app).websocket_connect(f"{PATH}?token={TOKEN}") as ws:
        assert ws.receive_json() == {"hello": True}
        _closed_with(ws, 1011)
    # One blip, two good reads, then three failures in a row.
    assert len(calls) == 3 + plugin_http.WS_RECHECK_MAX_FAILURES


# ─── limits ────────────────────────────────────────────────────────────────


def test_a_message_over_the_size_cap_closes_the_socket(app, claimed, monkeypatch) -> None:
    monkeypatch.setattr(plugin_http, "ws_max_message_bytes", lambda: 16)
    with TestClient(app).websocket_connect(f"{PATH}?token={TOKEN}") as ws:
        assert ws.receive_json() == {"hello": True}
        ws.send_text("x" * 16)
        assert ws.receive_text() == "echo:" + "x" * 16
        ws.send_bytes(b"y" * 17)
        _closed_with(ws, 1009)
    _wait_for(lambda: plugin_http.open_socket_count(SLUG) == 0)


def test_the_size_cap_is_the_request_body_cap() -> None:
    from domovoi.transport_guard import default_body_limit

    assert plugin_http.ws_max_message_bytes() == default_body_limit()


def test_a_flood_of_messages_closes_the_socket(app, claimed, monkeypatch) -> None:
    monkeypatch.setattr(plugin_http, "WS_MESSAGE_BURST", 3)
    monkeypatch.setattr(plugin_http, "WS_MESSAGES_PER_SEC", 0.001)
    with TestClient(app).websocket_connect(f"{PATH}?token={TOKEN}") as ws:
        assert ws.receive_json() == {"hello": True}
        for i in range(3):
            ws.send_text(str(i))
            assert ws.receive_text() == f"echo:{i}"
        ws.send_text("one too many")
        _closed_with(ws, 1008)


def test_one_device_holds_a_bounded_number_of_sockets(app, claimed, monkeypatch) -> None:
    monkeypatch.setattr(plugin_http, "WS_MAX_PER_SOURCE", 2)
    client = TestClient(app)
    with client.websocket_connect(f"{PATH}?token={TOKEN}") as a:
        assert a.receive_json() == {"hello": True}
        with client.websocket_connect(f"{PATH}?token={TOKEN}") as b:
            assert b.receive_json() == {"hello": True}
            _refused(client, f"{PATH}?token={TOKEN}", code=1013)
    _wait_for(lambda: plugin_http.open_socket_count(SLUG) == 0)
    with client.websocket_connect(f"{PATH}?token={TOKEN}") as c:
        assert c.receive_json() == {"hello": True}


def test_one_plugin_holds_a_bounded_number_of_sockets(app, claimed, monkeypatch) -> None:
    monkeypatch.setattr(plugin_http, "WS_MAX_PER_PLUGIN", 1)
    client = TestClient(app)
    with client.websocket_connect(f"{PATH}?token={TOKEN}") as a:
        assert a.receive_json() == {"hello": True}
        _refused(client, f"{PATH}?token={TOKEN}", code=1013)


# ─── disable and remount ──────────────────────────────────────────────────


def test_disable_closes_an_open_socket(app, claimed) -> None:
    with TestClient(app).websocket_connect(f"{PATH}?token={TOKEN}") as ws:
        assert ws.receive_json() == {"hello": True}
        assert plugin_http.open_socket_count(SLUG) == 1
        # From another thread than the socket's loop, as an admin request
        # handled elsewhere would: the close is handed to the right loop.
        plugin_http.set_plugin_enabled(SLUG, False)
        _closed_with(ws, 1001)
    _wait_for(lambda: plugin_http.open_socket_count(SLUG) == 0)


def test_a_remount_closes_the_previous_loads_sockets(app, claimed) -> None:
    client = TestClient(app)
    with client.websocket_connect(f"{PATH}?token={TOKEN}") as ws:
        assert ws.receive_json() == {"hello": True}
        plugin_http.mount_plugin_router(app, SLUG, _router())   # a re-enable
        _closed_with(ws, 1001)
    # The new load serves.
    with client.websocket_connect(f"{PATH}?token={TOKEN}") as ws:
        assert ws.receive_json() == {"hello": True}


def test_an_empty_mount_closes_them_too(app, claimed) -> None:
    with TestClient(app).websocket_connect(f"{PATH}?token={TOKEN}") as ws:
        assert ws.receive_json() == {"hello": True}
        plugin_http.mount_plugin_routers(app, SLUG, [])
        _closed_with(ws, 1001)


# ─── what is refused at load and at mount ─────────────────────────────────


def _unmarked() -> APIRouter:
    router = APIRouter()

    @router.websocket("/ws")
    async def live(websocket: WebSocket) -> None:  # pragma: no cover
        await websocket.accept()

    return router


def _open() -> APIRouter:
    router = APIRouter()

    @router.websocket("/ws")
    @webkit.open_endpoint
    async def live(websocket: WebSocket) -> None:  # pragma: no cover
        await websocket.accept()

    return router


def _starlette() -> APIRouter:
    router = APIRouter()

    @webkit.device_endpoint
    async def live(websocket) -> None:  # pragma: no cover
        await websocket.accept()

    router.add_websocket_route("/ws", live)
    return router


@pytest.mark.parametrize(
    "build,why",
    [
        (_unmarked, "is not @device_endpoint"),
        (_open, "is not @device_endpoint"),
        (_starlette, "plain Starlette WebSocketRoute"),
    ],
    ids=["unmarked", "open", "starlette"],
)
def test_only_device_tier_fastapi_sockets_are_served(build, why) -> None:
    routers = [build()]
    refused = webkit.refused_websocket_routes(routers)
    assert len(refused) == 1 and why in refused[0], refused
    report = ContractReport()
    check_route_tiers(routers, report)
    assert len(report.errors) == 1, report.errors
    assert webkit.WEBSOCKET_UNSUPPORTED in report.errors[0]
    _forget()
    try:
        with pytest.raises(TypeError, match="must be @device_endpoint"):
            plugin_http.mount_plugin_router(FastAPI(), SLUG, routers[0])
        assert SLUG not in plugin_http._mounted
    finally:
        _forget()


def test_a_device_tier_socket_passes_the_contract_check() -> None:
    routers = [_router()]
    assert webkit.refused_websocket_routes(routers) == []
    report = ContractReport()
    check_route_tiers(routers, report)
    assert report.errors == []


def test_a_device_socket_one_router_down_is_served_behind_the_same_gate(claimed) -> None:
    """FastAPI >= 0.139 keeps an included router nested; the gate and its
    tier read still reach the socket's own function."""
    outer = APIRouter()
    outer.include_router(_router(), prefix="/in")
    assert webkit.refused_websocket_routes([outer]) == []
    _forget()
    app = FastAPI()
    plugin_http.mount_plugin_router(app, SLUG, outer)
    try:
        client = TestClient(app)
        _refused(client, f"/v1/plugins/{SLUG}/in/live")
        with client.websocket_connect(f"/v1/plugins/{SLUG}/in/live?token={TOKEN}") as ws:
            assert ws.receive_json() == {"hello": True}
            assert plugin_http.open_socket_count(SLUG) == 1
            plugin_http.set_plugin_enabled(SLUG, False)
            _closed_with(ws, 1001)
    finally:
        _forget()


def test_the_trust_screen_lists_the_socket_on_the_device_tier(tmp_path) -> None:
    from domovoi.route_markers import scan_marked_endpoints

    package = tmp_path / "domovoi_plugin_wsdemo"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "core.py").write_text(
        "from fastapi import APIRouter, WebSocket\n"
        "from domovoi.sdk import device_endpoint\n\n"
        "router = APIRouter()\n\n\n"
        "@router.websocket('/live')\n"
        "@device_endpoint\n"
        "async def live(ws: WebSocket):\n"
        "    await ws.accept()\n",
        encoding="utf-8",
    )
    found = scan_marked_endpoints(package)
    assert [(r["method"], r["path"], r["process"]) for r in found["device"]] == [
        ("WS", "/live", "core")
    ]


def test_the_real_core_app_serves_a_plugin_socket(claimed) -> None:
    """Mounted on the core app itself (its LanHost and body-limit
    middlewares, its exception handlers), not just a bare FastAPI."""
    from domovoi.main import app as core_app

    _forget()
    plugin_http.mount_plugin_router(core_app, SLUG, _router())
    try:
        client = TestClient(core_app)
        _refused(client, PATH)
        with client.websocket_connect(f"{PATH}?token={TOKEN}") as ws:
            assert ws.receive_json() == {"hello": True}
    finally:
        plugin_http.mount_plugin_routers(core_app, SLUG, [])
        _forget()


# ─── the ?token= never reaches a log line ─────────────────────────────────

# Any printable ASCII may be a household token. A browser sends it through
# encodeURIComponent, which leaves ' ( ) ! * ~ as they are and turns the
# rest into %XX (so a token starting with '#' is sent as %23...).
ODD_TOKEN = "#Sarah's (den) token!*"


def _serve(app) -> tuple[object, threading.Thread, int]:
    import uvicorn

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, lifespan="off")
    )
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    _wait_for(lambda: server.started)
    return server, t, port


@pytest.mark.parametrize("token", [TOKEN, ODD_TOKEN], ids=["phrase", "odd"])
def test_uvicorn_logs_the_upgrade_without_the_token(app, monkeypatch, token: str) -> None:
    """uvicorn writes every upgrade, accepted or refused, with its query
    string, at INFO on ``uvicorn.error`` (stdout: the journal). The record
    factory both processes install blanks the value, all of it."""
    from websockets.exceptions import InvalidStatus
    from websockets.sync.client import connect

    install_fake_db(monkeypatch, admin=True, sessions={ADMIN}, device_token=token)
    log_privacy.install()
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(logging.Formatter("%(name)s %(message)s"))
    uv = logging.getLogger("uvicorn.error")
    level = uv.level
    uv.addHandler(handler)
    uv.setLevel(logging.INFO)
    server, t, port = _serve(app)
    query = urllib.parse.quote(token, safe="'()!*~")
    try:
        with connect(f"ws://127.0.0.1:{port}{PATH}?token={query}", open_timeout=5) as ws:
            assert ws.recv(timeout=5) == '{"hello":true}'
        # A refused upgrade (a stale token) is logged too ("403").
        with pytest.raises(InvalidStatus):
            with connect(f"ws://127.0.0.1:{port}{PATH}?token={query}x", open_timeout=5):
                pass
        _wait_for(lambda: buf.getvalue().count("WebSocket") >= 2)
    finally:
        server.should_exit = True
        t.join(timeout=5)
        uv.removeHandler(handler)
        uv.setLevel(level)
    out = buf.getvalue()
    assert f"{PATH}?token=***" in out, out
    assert "[accepted]" in out and "403" in out, out
    for leak in (token, query, "Sarah", "(den)", "%23", "household-token"):
        assert leak not in out, (leak, out)


def test_the_gate_writes_no_credential_into_its_own_lines(app, claimed, caplog, fast_recheck) -> None:
    caplog.set_level(logging.DEBUG)
    client = TestClient(app)
    _refused(client, f"{PATH}?token=wrong-{TOKEN}")
    with client.websocket_connect(f"{PATH}?token={TOKEN}") as ws:
        assert ws.receive_json() == {"hello": True}
        claimed.device_token = "rotated-household-token"
        _closed_with(ws, 1008)
    text = caplog.text
    assert "plugin wsdemo socket closed" in text
    assert TOKEN not in text and "rotated-household-token" not in text


# ─── with real rows ───────────────────────────────────────────────────────

from domovoi.tests.auth_testkit import _db_sync  # noqa: E402,F401 — fixture
from domovoi.tests.conftest import requires_db  # noqa: E402


async def _admin_session() -> str:
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        await admin_auth.set_password(s, "correct-horse-battery")
        return await admin_auth.create_session(s, label="tests")


async def _last_used(token: str):
    from sqlalchemy import text

    from domovoi.db.session import session_scope

    async with session_scope() as s:
        return (await s.execute(
            text("SELECT last_used_at FROM admin_sessions WHERE token_hash = :h"),
            {"h": admin_auth.token_sha256(token)},
        )).scalar_one()


async def _idle_out(token: str) -> None:
    from sqlalchemy import text

    from domovoi.db.session import session_scope

    async with session_scope() as s:
        await s.execute(
            text(
                "UPDATE admin_sessions SET last_used_at = now() - interval '2 hours', "
                "created_at = now() - interval '3 hours' WHERE token_hash = :h"
            ),
            {"h": admin_auth.token_sha256(token)},
        )


@requires_db
def test_with_real_rows_a_socket_neither_keeps_nor_outlives_an_admin_session(
    app, _db_sync, fast_recheck  # noqa: F811
) -> None:
    import asyncio

    token = asyncio.run(_admin_session())
    before = asyncio.run(_last_used(token))
    with TestClient(app).websocket_connect(PATH, headers=bearer(token)) as ws:
        assert ws.receive_json() == {"hello": True}
        time.sleep(0.3)   # rechecks against the real row
        ws.send_text("hi")
        assert ws.receive_text() == "echo:hi"
        assert asyncio.run(_last_used(token)) == before   # nothing moved the clock
        asyncio.run(_idle_out(token))
        _closed_with(ws, 1008)
    # And the idled-out session cannot open a new one.
    _refused(TestClient(app), PATH, headers=bearer(token))
