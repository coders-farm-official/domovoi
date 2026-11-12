"""Plugin websocket routes on the core (design §4.11): a ``@device_endpoint``
``@router.websocket`` route is served behind the per-slug gate's websocket
form — enable-404 as a 1008 close, the LAN ``Origin`` check, and the
household device tier — and closed when the slug is disabled or mounted
again. Anything else is refused at load and at mount."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import APIRouter, FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from domovoi import plugin_http, webkit
from domovoi.plugins_runtime.contracts import ContractReport, check_route_tiers
from domovoi.tests.auth_testkit import HEADER, bearer, install_fake_db

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


@pytest.fixture
def app():
    plugin_http._plugin_enabled.pop(SLUG, None)
    plugin_http._mounted.pop(SLUG, None)
    app = FastAPI()
    plugin_http.mount_plugin_router(app, SLUG, _router())
    try:
        yield app
    finally:
        plugin_http._plugin_enabled.pop(SLUG, None)
        plugin_http._mounted.pop(SLUG, None)
        plugin_http._open_sockets.pop(SLUG, None)


@pytest.fixture
def claimed(monkeypatch):
    return install_fake_db(monkeypatch, admin=True, sessions={ADMIN}, device_token=TOKEN)


def _refused(client: TestClient, url: str, **kw) -> None:
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(url, **kw) as ws:
            ws.receive_json()
    assert exc.value.code == 1008


def test_the_household_token_in_the_query_opens_the_socket(app, claimed) -> None:
    with TestClient(app).websocket_connect(f"{PATH}?token={TOKEN}") as ws:
        assert ws.receive_json() == {"hello": True}
        ws.send_text("hi")
        assert ws.receive_text() == "echo:hi"


def test_the_header_and_an_admin_bearer_open_it_too(app, claimed) -> None:
    client = TestClient(app)
    with client.websocket_connect(PATH, headers={HEADER: TOKEN}) as ws:
        assert ws.receive_json() == {"hello": True}
    with client.websocket_connect(PATH, headers=bearer(ADMIN)) as ws:
        assert ws.receive_json() == {"hello": True}


def test_no_or_a_wrong_token_is_closed_before_accept(app, claimed) -> None:
    client = TestClient(app)
    _refused(client, PATH)
    _refused(client, f"{PATH}?token=wrong")


def test_the_pre_setup_grace_applies(app, monkeypatch) -> None:
    install_fake_db(monkeypatch, admin=False, device_token=TOKEN)
    with TestClient(app).websocket_connect(PATH) as ws:
        assert ws.receive_json() == {"hello": True}


def test_a_page_off_the_lan_is_refused(app, claimed) -> None:
    client = TestClient(app)
    _refused(client, f"{PATH}?token={TOKEN}", headers={"origin": "https://evil.example"})
    with client.websocket_connect(
        f"{PATH}?token={TOKEN}", headers={"origin": "http://192.168.1.20:6369"}
    ) as ws:
        assert ws.receive_json() == {"hello": True}


def test_a_disabled_plugin_refuses_the_upgrade(app, claimed) -> None:
    plugin_http.set_plugin_enabled(SLUG, False)
    _refused(TestClient(app), f"{PATH}?token={TOKEN}")


def test_disable_closes_an_open_socket(app, claimed) -> None:
    client = TestClient(app)
    with client.websocket_connect(f"{PATH}?token={TOKEN}") as ws:
        assert ws.receive_json() == {"hello": True}
        assert len(plugin_http._open_sockets[SLUG]) == 1

        # Disable runs on the app's event loop in production; the test
        # client's loop lives in its portal thread.
        async def _disable() -> None:
            plugin_http.set_plugin_enabled(SLUG, False)
            await asyncio.sleep(0)

        ws.portal.call(_disable)
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
        assert exc.value.code == 1001
    assert SLUG not in plugin_http._open_sockets


# ─── what is refused ──────────────────────────────────────────────────────


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
)
def test_only_device_tier_fastapi_sockets_are_served(build, why) -> None:
    routers = [build()]
    refused = webkit.refused_websocket_routes(routers)
    assert len(refused) == 1 and why in refused[0], refused
    report = ContractReport()
    check_route_tiers(routers, report)
    assert len(report.errors) == 1, report.errors
    assert webkit.WEBSOCKET_UNSUPPORTED in report.errors[0]
    plugin_http._mounted.pop(SLUG, None)
    try:
        with pytest.raises(TypeError, match="must be @device_endpoint"):
            plugin_http.mount_plugin_router(FastAPI(), SLUG, routers[0])
        assert SLUG not in plugin_http._mounted
    finally:
        plugin_http._plugin_enabled.pop(SLUG, None)


def test_a_device_tier_socket_passes_the_contract_check() -> None:
    routers = [_router()]
    assert webkit.refused_websocket_routes(routers) == []
    report = ContractReport()
    check_route_tiers(routers, report)
    assert report.errors == []
