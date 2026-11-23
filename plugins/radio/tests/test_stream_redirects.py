"""PLG-12 (round 6): a station's stream redirects are followed here, checked.

The card's proof of concept: someone lists a station in the public
directory under a popular name whose stream URL is public and answers
``302`` to an address inside the house. "Play <name> radio", "yes": the
URL passed the outbound check as written, and the room's music player
fetched it with its own redirect following, so the GET went into the house.

Now the handler follows the chain itself, every hop through the outbound
check and pinned to the address it judged, and the player is handed the URL
that answered, or nothing.

DB-free: the handler over the stub SDK (whose playback records calls), DNS
faked, and the stream servers answered by an httpx MockTransport keyed on
the Host header (a pinned request goes to the address, by name in Host).
"""

from __future__ import annotations

import ipaddress
from typing import Any

import httpx
import pytest

from domovoi.models import Context
from domovoi.sdk import net_safety

from domovoi_plugin_radio import player_relay
from domovoi_plugin_radio.handlers import radio as radio_mod
from domovoi_plugin_radio.handlers.radio import RadioHandler

PUBLIC_V4 = "93.184.216.34"


@pytest.fixture(autouse=True)
def _wire(monkeypatch):
    """DNS: *.example is public, *.lan.example is in the house. HTTP:
    each host answers from ``ANSWERS`` (status, Location)."""
    def resolve(host: str):
        host = host.lower()
        if host.endswith(".lan.example"):
            return [ipaddress.ip_address("192.168.1.20")]
        if host.endswith("example"):
            return [ipaddress.ip_address(PUBLIC_V4)]
        return []

    monkeypatch.setattr(net_safety, "resolve_host", resolve)
    seen: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        host = request.headers.get("host", request.url.host)
        seen.append(f"{host}{request.url.path}")
        status, location = ANSWERS.get(host + request.url.path, (200, None))
        headers = {"location": location} if location else {}
        return httpx.Response(status, headers=headers, content=b"ICY audio")

    real = httpx.AsyncClient

    def client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(answer)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    monkeypatch.setattr(radio_mod, "_stream_hops_checked", lambda sdk: True)
    return seen


ANSWERS: dict[str, tuple[int, str | None]] = {
    # The card's station: public, and a 302 into the house.
    "popular.example/live": (302, "http://192.168.1.20:6370/v1/admin/version"),
    # The same, through a name that resolves into the house.
    "sneaky.example/live": (302, "http://router.lan.example/admin"),
    # An honest station behind a CDN redirect.
    "honest.example/live": (302, "http://cdn.example/edge/live.mp3"),
}


def _directory_pick(url: str) -> dict[str, Any]:
    return {"id": 9, "name": "Popular FM", "source": "online", "stream_url": url,
            "frequency_mhz": None, "call_sign": None}


@pytest.mark.parametrize("url", ["http://popular.example/live", "http://sneaky.example/live"])
async def test_a_redirect_into_the_house_never_reaches_the_player(stub_sdk, _wire, url) -> None:
    h = RadioHandler(stub_sdk)
    resp = await h._stream_station_row(_directory_pick(url), Context(room_id="kitchen"), None)
    assert stub_sdk.playback.calls == []
    assert "can't stream Popular FM" in resp.text
    # The house address itself was never asked.
    assert not any(s.startswith(("192.168.1.20", "router.lan.example")) for s in _wire)


async def test_the_player_is_handed_a_relay_url_never_the_station(stub_sdk, _wire) -> None:
    """M11: the player makes no request of its own to the station; it is
    handed a one-use URL on the core that stands for the station."""
    h = RadioHandler(stub_sdk)
    await h._stream_station_row(_directory_pick("http://honest.example/live"), Context(room_id="kitchen"), None)
    (call,) = stub_sdk.playback.calls
    assert call["stream_url"].startswith("http://127.0.0.1:6370/v1/plugins/radio/player-relay/")
    assert "example" not in call["stream_url"]
    assert player_relay.station_url_for(call["stream_url"]) == "http://honest.example/live"


async def test_an_unreachable_stream_plays_nothing(stub_sdk, monkeypatch) -> None:
    async def down(*_a, **_k):
        raise httpx.ConnectError("no answer")

    monkeypatch.setattr(net_safety, "resolve_final_target", down)
    h = RadioHandler(stub_sdk)
    resp = await h._stream_station_row(_directory_pick("http://honest.example/live"), Context(room_id="kitchen"), None)
    assert stub_sdk.playback.calls == []
    assert "couldn't reach Popular FM's stream" in resp.text
