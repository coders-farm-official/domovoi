"""M11: a room's music player plays an internet station through the core.

The player used to be handed the station's URL and fetch it itself, with
its own redirect following: a station could answer the core's look at it
with audio and the player's own request with a redirect into the house.
Now the radio handler asks ``sdk.playback.relay_url(..., kind="radio")``
(SDK 1.17) and the player is handed a one-use relay URL on the core, which
makes the only request to the station, every hop checked.

The relay itself (token, binding, bounds, ICY) is the core's and is tested
in ``domovoi/tests/test_player_relay.py``; these tests hold the plugin to
it: the handler hands the player a core relay URL, and the now-playing
matcher reads that URL as its station.

DB-free: the core's relay router served by httpx's ASGI transport with the
caller's address set; DNS faked; the station answered by a MockTransport.
"""

from __future__ import annotations

import ipaddress
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from domovoi import player_relay
from domovoi.models import Context
from domovoi.sdk import net_safety

from domovoi_plugin_radio import core as core_mod
from domovoi_plugin_radio.handlers import radio as radio_mod
from domovoi_plugin_radio.handlers.radio import RadioHandler

PUBLIC_V4 = "93.184.216.34"
_RealClient = httpx.AsyncClient
AUDIO = b"\xff\xfb\x90\x64" + b"\x00" * 4000
ICY_BODY = b"A" * 16 + bytes([2]) + b"StreamTitle='x';" + b"\0" * 16 + b"B" * 16
PICK = {"id": 3, "name": "Sly FM", "source": "online",
        "stream_url": "http://sly.example/live", "frequency_mhz": None, "call_sign": None}


class Station:
    def __init__(self) -> None:
        self.seen: list[tuple[str, dict[str, str]]] = []
        self.plan: dict[str, list[httpx.Response]] = {}

    def answer(self, request: httpx.Request) -> httpx.Response:
        host = request.headers.get("host", request.url.host)
        key = host + request.url.path
        self.seen.append((key, dict(request.headers)))
        queue = self.plan.get(key)
        if queue:
            return queue.pop(0) if len(queue) > 1 else queue[0]
        return httpx.Response(404)


@pytest.fixture
def station(monkeypatch) -> Station:
    def resolve(host: str):
        return [ipaddress.ip_address(PUBLIC_V4)] if host.lower().endswith("example") else []

    monkeypatch.setattr(net_safety, "resolve_host", resolve)
    st = Station()

    def client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(st.answer)
        return _RealClient(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    monkeypatch.setattr(radio_mod, "_stream_hops_checked", lambda sdk: True)
    return st


@pytest.fixture
def app() -> FastAPI:
    a = FastAPI()
    a.include_router(player_relay.router)
    return a


async def _get(app: FastAPI, url: str, **headers: str) -> httpx.Response:
    # The stub SDK's route: the room's player calls from loopback.
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 40000))
    async with _RealClient(transport=transport, base_url="http://core") as c:
        return await c.get(httpx.URL(url).raw_path.decode(), headers=headers)


def _audio(content: bytes = AUDIO, **headers: str) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "audio/mpeg", **headers}, content=content)


async def _play(stub_sdk) -> str:
    stub_sdk.playback.known_rooms = None
    resp = await RadioHandler(stub_sdk)._stream_station_row(PICK, Context(room_id="kitchen"), None)
    assert resp.music_action == "start"
    (call,) = [c for c in stub_sdk.playback.calls if c["action"] == "play_url"]
    return call["stream_url"]


async def test_a_station_that_redirects_the_second_request_into_the_house_plays_nothing(
    stub_sdk, app, station
) -> None:
    # The core's look at the station gets audio; the next request (once
    # the player's own, now the relay's) is sent into the house.
    station.plan["sly.example/live"] = [
        _audio(),
        httpx.Response(302, headers={"location": "http://192.168.1.1/admin"}),
    ]
    handed = await _play(stub_sdk)
    assert "sly.example" not in handed and "/v1/player-relay/" in handed

    r = await _get(app, handed)
    assert r.status_code == 409
    assert not r.content.startswith(AUDIO[:4])
    assert not any(k.startswith("192.168.") for k, _ in station.seen)


async def test_the_handler_mints_a_radio_relay_one_connection_with_icy(
    stub_sdk, app, station
) -> None:
    station.plan["sly.example/live"] = [
        _audio(),
        _audio(ICY_BODY, **{"icy-metaint": "16", "icy-name": "Sly FM"}),
    ]
    handed = await _play(stub_sdk)
    r = await _get(app, handed, **{"Icy-MetaData": "1"})
    assert r.status_code == 200
    assert r.headers["icy-metaint"] == "16"
    assert r.content == ICY_BODY
    # kind="radio": one connection.
    assert (await _get(app, handed)).status_code == 404


async def test_no_route_to_the_player_is_said_aloud(stub_sdk, station, monkeypatch) -> None:
    from domovoi.sdk import RelayUnavailable

    station.plan["sly.example/live"] = [_audio()]

    async def unavailable(room_id, url, *, kind="file"):
        raise RelayUnavailable("no player")

    monkeypatch.setattr(stub_sdk.playback, "relay_url", unavailable)
    resp = await RadioHandler(stub_sdk)._stream_station_row(PICK, Context(room_id="kitchen"), None)
    assert resp.music_action != "start"
    assert "music player" in resp.text


async def test_stopping_ends_the_rooms_relay(stub_sdk, station) -> None:
    station.plan["sly.example/live"] = [_audio()]
    handed = await _play(stub_sdk)
    assert player_relay.target_for(handed) == "http://sly.example/live"
    token = player_relay.token_of(handed)
    assert token in player_relay._GRANTS
    await RadioHandler(stub_sdk)._stop_stream(Context(room_id="kitchen"))
    # The room's grant (and any stream it serves) ends. Its record stays
    # while the queue holds the entry, so target_for still names the
    # station (L20: a queued relay entry renews when it plays again).
    assert token not in player_relay._GRANTS


async def test_the_now_playing_matcher_reads_a_relay_url_as_its_station(stub_sdk) -> None:
    url = await stub_sdk.playback.relay_url("kitchen", "http://good.example/live", kind="radio")

    class _Session:
        async def execute(self, _stmt, params):
            class _R:
                def first(self_inner):
                    return (7, "Good FM", True) if params.get("url") == "http://good.example/live" else None

            return _R()

    match = core_mod._make_matcher(stub_sdk)
    got = await match(_Session(), mpd_file=url, title="Artist - Song")
    assert got == {"kind": "radio", "station_id": 7, "name": "Good FM", "favorited": True}


def test_the_plugin_no_longer_serves_its_own_relay() -> None:
    """The relay is the core's (GET /v1/player-relay/<token>): the plugin
    has no open route of its own for it."""
    from pathlib import Path

    from domovoi.route_markers import scan_marked_endpoints

    found = scan_marked_endpoints(Path(core_mod.__file__).parent)
    assert not any("player-relay" in (e.get("path") or "") for e in found.get("open", []))
