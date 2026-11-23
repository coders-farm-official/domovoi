"""M11: a room's music player plays an internet station through the core.

The player used to be handed the station's URL and fetch it itself, with
its own redirect following: a station could answer the core's look at it
with audio and the player's own request with a redirect into the house.
Now the player is handed a one-use relay URL on the core, and the core
makes the only request to the station, every hop checked.

DB-free: the plugin's core router over the stub SDK, served by httpx's
ASGI transport with the caller's address set (the room's player, or
someone else); DNS faked; the station answered by a MockTransport keyed on
the Host header (a pinned request goes to the address, by name in Host).
"""

from __future__ import annotations

import ipaddress
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from domovoi.models import Context
from domovoi.player_route import PlayerRoute
from domovoi.sdk import net_safety

from domovoi_plugin_radio import core as core_mod
from domovoi_plugin_radio import player_relay
from domovoi_plugin_radio.handlers import radio as radio_mod
from domovoi_plugin_radio.handlers.radio import RadioHandler

PUBLIC_V4 = "93.184.216.34"
PLAYER = "172.17.0.5"
ROUTE = PlayerRoute(host="172.17.0.1", peer=PLAYER)
_RealClient = httpx.AsyncClient
AUDIO = b"\xff\xfb\x90\x64" + b"\x00" * 4000

ICY_BODY = b"A" * 16 + bytes([2]) + b"StreamTitle='x';" + b"\0" * 16 + b"B" * 16


class Station:
    """The station's answers, request by request. Records every request
    (host + path + headers) it saw, house addresses included."""

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
        host = host.lower()
        if host.endswith("example"):
            return [ipaddress.ip_address(PUBLIC_V4)]
        return []

    monkeypatch.setattr(net_safety, "resolve_host", resolve)
    st = Station()

    def client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(st.answer)
        return _RealClient(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    monkeypatch.setattr(radio_mod, "_stream_hops_checked", lambda sdk: True)
    return st


def _audio(content: bytes = AUDIO, **headers: str) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "audio/mpeg", **headers}, content=content)


@pytest.fixture
def app(stub_sdk) -> FastAPI:
    a = FastAPI()
    a.include_router(core_mod._build_core_router(stub_sdk), prefix="/v1/plugins/radio")
    return a


async def _get(app: FastAPI, url: str, *, caller: str = PLAYER, **headers: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, client=(caller, 40000))
    async with _RealClient(transport=transport, base_url="http://core") as c:
        return await c.get(httpx.URL(url).raw_path.decode(), headers=headers)


def _mint(url: str = "http://good.example/live", room: str = "kitchen") -> str:
    return player_relay.mint(room, url, ROUTE)


# ─── The finding ─────────────────────────────────────────────────────────


async def test_a_station_that_redirects_the_second_request_into_the_house_plays_nothing(
    stub_sdk, app, station
) -> None:
    # The core's look at the station gets audio; the next request (once
    # the player's own, now the relay's) is sent into the house.
    station.plan["sly.example/live"] = [
        _audio(),
        httpx.Response(302, headers={"location": "http://192.168.1.1/admin"}),
    ]
    stub_sdk.playback.known_rooms = None
    h = RadioHandler(stub_sdk)
    pick = {"id": 3, "name": "Sly FM", "source": "online",
            "stream_url": "http://sly.example/live", "frequency_mhz": None, "call_sign": None}
    resp = await h._stream_station_row(pick, Context(room_id="kitchen"), None)
    assert resp.music_action == "start"
    (call,) = [c for c in stub_sdk.playback.calls if c["action"] == "play_url"]
    handed = call["stream_url"]
    assert "sly.example" not in handed and "/player-relay/" in handed

    r = await _get(app, handed, caller="127.0.0.1")   # the stub route's player
    assert r.status_code == 409
    assert not r.content.startswith(AUDIO[:4])
    # Nothing inside the house was asked, by anyone.
    assert not any(k.startswith("192.168.") for k, _ in station.seen)


async def test_the_relay_streams_the_station_to_the_rooms_player(app, station) -> None:
    station.plan["good.example/live"] = [_audio()]
    r = await _get(app, _mint())
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/mpeg"
    assert r.content == AUDIO


async def test_every_hop_of_the_relays_fetch_is_checked(app, station) -> None:
    station.plan["good.example/live"] = [
        httpx.Response(302, headers={"location": "http://10.0.0.8/stream"})
    ]
    r = await _get(app, _mint())
    assert r.status_code == 409
    assert [k for k, _ in station.seen] == ["good.example/live"]


async def test_a_playlist_is_never_relayed(app, station) -> None:
    """A player opens a playlist's entries itself, past the relay."""
    station.plan["good.example/live"] = [_audio(b"#EXTM3U\nhttp://192.168.1.1/x\n")]
    r = await _get(app, _mint())
    assert r.status_code == 502
    station.plan["good.example/live"] = [
        httpx.Response(200, headers={"content-type": "audio/x-mpegurl"}, content=b"#EXTM3U\n")
    ]
    r = await _get(app, _mint())
    assert r.status_code == 502


# ─── The token ───────────────────────────────────────────────────────────


async def test_the_token_is_good_for_one_connection(app, station) -> None:
    station.plan["good.example/live"] = [_audio()]
    url = _mint()
    assert (await _get(app, url)).status_code == 200
    assert (await _get(app, url)).status_code == 404
    assert len(station.seen) == 1


async def test_only_the_rooms_player_may_use_it_and_a_stranger_spends_it(app, station) -> None:
    station.plan["good.example/live"] = [_audio()]
    url = _mint()
    assert (await _get(app, url, caller="192.168.1.30")).status_code == 404
    # Spent by the stranger's try: the player gets nothing either.
    assert (await _get(app, url)).status_code == 404
    assert station.seen == []


async def test_the_token_expires_if_not_opened_in_time(app, station, monkeypatch) -> None:
    station.plan["good.example/live"] = [_audio()]
    now = [1000.0]
    monkeypatch.setattr(player_relay, "_clock", lambda: now[0])
    url = _mint()
    now[0] += player_relay.OPEN_WITHIN_SEC + 1
    assert (await _get(app, url)).status_code == 404
    assert station.seen == []


async def test_the_token_is_bound_to_its_room(app, station) -> None:
    """A room's new relay replaces its old one; another room's is its own."""
    station.plan["good.example/live"] = [_audio()]
    station.plan["other.example/live"] = [_audio()]
    old = _mint()
    office = player_relay.mint(
        "office", "http://other.example/live", PlayerRoute(host="172.17.0.1", peer="172.17.0.6")
    )
    new = _mint()
    assert (await _get(app, old)).status_code == 404
    # The kitchen's player can't use the office's.
    assert (await _get(app, office)).status_code == 404
    assert (await _get(app, new)).status_code == 200
    assert [k for k, _ in station.seen] == ["good.example/live"]


async def test_an_unknown_token_is_404(app, station) -> None:
    assert (await _get(app, "/v1/plugins/radio/player-relay/nope")).status_code == 404


# ─── Now playing ─────────────────────────────────────────────────────────


async def test_icy_metadata_passes_through_when_the_player_asks(app, station) -> None:
    station.plan["good.example/live"] = [_audio(ICY_BODY, **{"icy-metaint": "16", "icy-name": "Good FM"})]
    r = await _get(app, _mint(), **{"Icy-MetaData": "1", "User-Agent": "Music Player Daemon"})
    assert r.status_code == 200
    assert r.headers["icy-metaint"] == "16"
    assert r.headers["icy-name"] == "Good FM"
    assert r.content == ICY_BODY   # the title blocks, byte for byte
    (_, sent), = station.seen
    assert sent["icy-metadata"] == "1"


async def test_no_icy_metaint_unless_the_player_asked(app, station) -> None:
    station.plan["good.example/live"] = [_audio(**{"icy-metaint": "16"})]
    r = await _get(app, _mint())
    assert "icy-metaint" not in r.headers
    (_, sent), = station.seen
    assert sent["icy-metadata"] == "0"


def test_the_relay_url_stands_for_its_station() -> None:
    url = _mint("http://good.example/live")
    assert url.startswith("http://172.17.0.1:6370/v1/plugins/radio/player-relay/")
    assert player_relay.station_url_for(url) == "http://good.example/live"
    assert player_relay.station_url_for("http://good.example/live") is None
    player_relay.revoke_room("kitchen")
    assert player_relay.station_url_for(url) is None


async def test_the_now_playing_matcher_reads_a_relay_url_as_its_station(stub_sdk) -> None:
    url = _mint("http://good.example/live")
    asked: list[Any] = []

    class _Session:
        async def execute(self, _stmt, params):
            asked.append(params)

            class _R:
                def first(self_inner):
                    return (7, "Good FM", True) if params.get("url") == "http://good.example/live" else None

            return _R()

    match = core_mod._make_matcher(stub_sdk)
    got = await match(_Session(), mpd_file=url, title="Artist - Song")
    assert got == {"kind": "radio", "station_id": 7, "name": "Good FM", "favorited": True}


# ─── Bounds ──────────────────────────────────────────────────────────────


class _Request:
    """What serve() reads of the player's request."""

    def __init__(self, caller: str = PLAYER) -> None:
        self.client = type("C", (), {"host": caller})()
        self.headers: dict[str, str] = {}


async def _drain(response, *, after_first=None) -> int:
    got = 0
    async for chunk in response.body_iterator:
        got += len(chunk)
        if after_first is not None:
            after_first()
            after_first = None
    return got


async def test_a_relay_ends_when_the_room_gets_a_new_one(station) -> None:
    station.plan["good.example/live"] = [_audio(b"x" * 200_000)]
    token = _mint().rsplit("/", 1)[-1]
    resp = await player_relay.serve(token, _Request())
    got = await _drain(resp, after_first=lambda: player_relay.revoke_room("kitchen"))
    assert 0 < got < 200_000


async def test_the_relay_cuts_off_under_never(station) -> None:
    from domovoi.sdk import egress

    station.plan["good.example/live"] = [_audio(b"x" * 200_000)]
    token = _mint().rsplit("/", 1)[-1]
    resp = await player_relay.serve(token, _Request())
    with egress.override_policy("never"):
        got = await _drain(resp)
    assert got < 200_000


async def test_the_relay_ends_at_its_time_limit(station, monkeypatch) -> None:
    station.plan["good.example/live"] = [_audio(b"x" * 200_000)]
    now = [0.0]
    monkeypatch.setattr(player_relay, "_clock", lambda: now[0])
    token = _mint().rsplit("/", 1)[-1]
    resp = await player_relay.serve(token, _Request())

    def later() -> None:
        now[0] += player_relay.MAX_DURATION_SEC + 1

    got = await _drain(resp, after_first=later)
    assert 0 < got < 200_000


async def test_the_relay_holds_a_stream_to_its_ceiling(station, monkeypatch) -> None:
    """Past the burst, a station that sends faster than the ceiling is
    read no faster than it."""
    monkeypatch.setattr(player_relay, "BURST_BYTES", 32 * 1024)
    monkeypatch.setattr(player_relay, "MAX_BYTES_PER_SEC", 16 * 1024)
    slept: list[float] = []

    async def fake_sleep(sec: float) -> None:
        slept.append(sec)

    monkeypatch.setattr(player_relay.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(player_relay, "_clock", lambda: 0.0)
    station.plan["good.example/live"] = [_audio(b"x" * (96 * 1024))]
    token = _mint().rsplit("/", 1)[-1]
    await _drain(await player_relay.serve(token, _Request()))
    # 64 KiB past a 32 KiB burst at 16 KiB/s: four seconds of waiting.
    assert slept and max(slept) == pytest.approx(4.0)


def test_the_route_is_open_and_marked_so() -> None:
    """The player has no credential: the route is @open_endpoint, listed
    on the install preview with its path, and the token is what it checks."""
    from pathlib import Path

    from domovoi.route_markers import scan_marked_endpoints

    found = scan_marked_endpoints(Path(core_mod.__file__).parent)
    assert any(
        e["path"] == player_relay.ROUTE and e["method"] == "GET" and e["process"] == "core"
        for e in found["open"]
    ), found
