"""What the core's WebSocket UPGRADES require before anything exists
(CORE-2, CORE-8).

An HTTP gate can answer late — the handler has done nothing yet when the
dependency raises. A WebSocket gate cannot: by the time a session object
exists it has already registered itself, and on ``/v1/dropin`` that
registration IS the call. So both checks run in the route function,
before any session is constructed, and the assertions here are as much
about what did NOT happen (``active_dropins`` untouched, the room never
disturbed) as about the close code.

DB-FREE. The auth primitives are faked with the B0 kit
(``install_fake_db``), and the socket is a real Starlette ``WebSocket``
over a scripted ASGI scope — so ``headers``, ``cookies`` and
``query_params`` behave exactly as they do in production, including the
``?token=`` fallback a browser needs.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from starlette.websockets import WebSocket

from domovoi import admin_auth
from domovoi.main import phone_dropin, stream
from domovoi.tests.auth_testkit import HEADER, install_fake_db

ADMIN_TOKEN = "admin-token"
DEVICE_TOKEN = "household-token"


# ─── A scripted WS upgrade ────────────────────────────────────────────────


def make_app_state() -> SimpleNamespace:
    """The app.state surface the drop-in route and session read."""
    return SimpleNamespace(
        state=SimpleNamespace(
            active_sessions={},
            satellite_full_duplex={},
            active_dropins={},
            pending_dropins={},
            dropin_lock=asyncio.Lock(),
            resumable_music={},
            pending_music_start={},
        )
    )


class Upgrade:
    """One WebSocket upgrade, driven far enough to be accepted or refused.

    Wraps a real ``starlette.websockets.WebSocket`` so the route sees the
    genuine header/cookie/query-param parsing, and records what the server
    sent back (accept, frames, close code).
    """

    def __init__(
        self,
        *,
        headers: dict[str, str] | None = None,
        query: str = "",
        app: SimpleNamespace | None = None,
        client_host: str = "192.168.1.50",
    ) -> None:
        raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
        scope = {
            "type": "websocket",
            "path": "/v1/dropin/kitchen",
            "headers": raw,
            "query_string": query.encode(),
            "client": (client_host, 51515),
            "app": app if app is not None else make_app_state(),
        }
        self.sent: list[dict] = []
        self._inbox: asyncio.Queue = asyncio.Queue()
        self._inbox.put_nowait({"type": "websocket.connect"})
        self.ws = WebSocket(scope, receive=self._inbox.get, send=self._send)

    async def _send(self, message: dict) -> None:
        self.sent.append(message)

    # ── what happened ────────────────────────────────────────────────────
    @property
    def accepted(self) -> bool:
        return any(m["type"] == "websocket.accept" for m in self.sent)

    @property
    def close_code(self) -> int | None:
        for m in self.sent:
            if m["type"] == "websocket.close":
                return m.get("code")
        return None

    def frames(self) -> list[dict]:
        out = []
        for m in self.sent:
            if m["type"] == "websocket.send" and m.get("text"):
                try:
                    out.append(json.loads(m["text"]))
                except ValueError:
                    pass
        return out

    def error_code(self) -> str | None:
        for f in self.frames():
            if f.get("type") == "error":
                return f.get("code")
        return None

    def disconnect(self) -> None:
        self._inbox.put_nowait({"type": "websocket.disconnect", "code": 1000})


def device(token: str = DEVICE_TOKEN) -> dict[str, str]:
    return {HEADER: token}


# ─── CORE-2: the drop-in upgrade is on the device tier ────────────────────


async def _dropin(up: Upgrade) -> None:
    """Run the real route function to completion."""
    up.disconnect()  # so a session that DOES start ends immediately
    await asyncio.wait_for(phone_dropin(up.ws, "kitchen"), timeout=5)


def _refused(up: Upgrade) -> bool:
    return up.close_code == 1008 and up.error_code() == "unauthorized"


async def test_dropin_upgrade_without_a_token_is_closed_1008(monkeypatch):
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN},
                    device_token=DEVICE_TOKEN)
    up = Upgrade()
    await _dropin(up)
    assert _refused(up)


async def test_a_refused_upgrade_leaves_the_registries_untouched(monkeypatch):
    """The point of gating the UPGRADE: a refusal must not register the
    caller, mark it full-duplex, or let the target room notice at all."""
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN},
                    device_token=DEVICE_TOKEN)
    app = make_app_state()
    pi = SimpleNamespace(room_id="kitchen", dropin_peer=None, sent=[])
    app.state.active_sessions["kitchen"] = pi
    app.state.satellite_full_duplex["kitchen"] = True

    up = Upgrade(app=app)
    await _dropin(up)

    assert _refused(up)
    assert app.state.active_dropins == {}
    assert app.state.pending_dropins == {}
    assert set(app.state.satellite_full_duplex) == {"kitchen"}
    assert pi.dropin_peer is None


async def test_dropin_upgrade_with_a_stale_token_is_closed_1008(monkeypatch):
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN},
                    device_token=DEVICE_TOKEN)
    up = Upgrade(headers=device("a-token-that-was-rotated-away"))
    await _dropin(up)
    assert _refused(up)


async def test_dropin_upgrade_with_the_cookie_alone_is_closed_1008(monkeypatch):
    """The dashboard cookie renders GET state; it never opens a mic."""
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN},
                    device_token=DEVICE_TOKEN)
    up = Upgrade(headers={"cookie": f"{admin_auth.COOKIE_NAME}={ADMIN_TOKEN}"})
    await _dropin(up)
    assert _refused(up)


@pytest.mark.parametrize(
    ("label", "headers", "query"),
    [
        ("device-token header", device(), ""),
        ("token query param", {}, f"token={DEVICE_TOKEN}"),
        ("admin bearer", {"authorization": f"Bearer {ADMIN_TOKEN}"}, ""),
    ],
)
async def test_dropin_upgrade_with_a_household_credential_proceeds(
    monkeypatch, label, headers, query
):
    """Past the gate the session runs and answers on its own terms — the
    target room is offline here, so ``target_offline`` (not
    ``unauthorized``) is proof the credential was accepted."""
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN},
                    device_token=DEVICE_TOKEN)
    up = Upgrade(headers=headers, query=query)
    await _dropin(up)
    assert up.accepted
    assert up.error_code() == "target_offline", label


async def test_dropin_upgrade_keeps_the_pre_setup_lan_grace(monkeypatch):
    """A fresh install (and a throwaway test instance) has no admin
    credential yet — the household is still trusted on the LAN."""
    install_fake_db(monkeypatch, admin=False)
    up = Upgrade()
    await _dropin(up)
    assert up.accepted
    assert up.error_code() == "target_offline"


async def test_the_query_param_token_is_validated_not_just_present(monkeypatch):
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN},
                    device_token=DEVICE_TOKEN)
    up = Upgrade(query="token=not-the-household-token")
    await _dropin(up)
    assert _refused(up)


# ─── The classifier behind the gate ───────────────────────────────────────


def _conn(headers: dict[str, str] | None = None, query: str = "") -> WebSocket:
    return Upgrade(headers=headers, query=query).ws


async def test_check_device_websocket_prefers_the_header(monkeypatch):
    """A client that can set headers is never downgraded to the query
    string — which also means a stale ?token= alongside a good header
    cannot lock it out."""
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN},
                    device_token=DEVICE_TOKEN)
    ws = _conn(headers=device(), query="token=stale")
    assert admin_auth.device_token_from_websocket(ws) == DEVICE_TOKEN
    assert await admin_auth.check_device_websocket(ws) == "ok"


async def test_check_device_websocket_result_vocabulary(monkeypatch):
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN},
                    device_token=DEVICE_TOKEN)
    assert await admin_auth.check_device_websocket(_conn(device())) == "ok"
    assert await admin_auth.check_device_websocket(
        _conn(query="token=" + DEVICE_TOKEN)
    ) == "ok"
    assert await admin_auth.check_device_websocket(
        _conn({"authorization": f"Bearer {ADMIN_TOKEN}"})
    ) == "admin"
    assert await admin_auth.check_device_websocket(_conn(device("stale"))) == "invalid"
    assert await admin_auth.check_device_websocket(_conn()) == "no-auth"


async def test_http_requests_still_refuse_a_query_string_token(monkeypatch):
    """``?token=`` is a WebSocket-only affordance: a query string lands in
    access logs and Referer headers, and every HTTP client can set the
    header instead."""
    from domovoi.tests.auth_testkit import make_request

    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN},
                    device_token=DEVICE_TOKEN)
    request = make_request()
    request.scope["query_string"] = f"token={DEVICE_TOKEN}".encode()
    assert await admin_auth.check_device_request(request) == "no-auth"


# ─── /v1/stream keeps its own front door ──────────────────────────────────


async def test_stream_upgrade_is_not_device_gated(monkeypatch):
    """The satellite socket authenticates with its PAIRING token in the
    hello frame (V002), not the household token — gating the upgrade too
    would lock out every already-provisioned Pi. Guard that this stayed
    the case: an upgrade with no household credential must reach the
    hello gate, which closes it 1008 for a MISSING HELLO, not for a
    missing token.
    """
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN},
                    device_token=DEVICE_TOKEN)
    up = Upgrade()
    up.disconnect()
    await asyncio.wait_for(stream(up.ws, "kitchen"), timeout=5)
    assert up.accepted
    assert up.error_code() != "unauthorized"
