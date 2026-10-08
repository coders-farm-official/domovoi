"""Directory URLs the core side stores or plays (A7-06).

The dashboard's station writes refuse a stream URL that points into the
house (test_web_url_safety.py). Two core-side writers/consumers did not:
the simulcast resolver stamps whatever radio-browser.info (a community-
edited directory) returns for an FM call sign onto the row, and the voice
"stream X" path hands a row's URL to the room's music player. Both now run
the same outbound-URL check.

DB-free: the resolver runs over a fake session that answers its SELECT
and records any UPDATE, and the handler over the stub SDK, whose playback
records calls. ``resolve_host`` is patched, so nothing here touches DNS.
"""

from __future__ import annotations

import ipaddress
from contextlib import asynccontextmanager
from typing import Any

import pytest

from domovoi.models import Context
from domovoi.sdk import net_safety

from domovoi_plugin_radio.clients.radio_browser import RadioBrowserStation
from domovoi_plugin_radio.handlers.radio import RadioHandler
from domovoi_plugin_radio.workers import simulcast

PUBLIC_V4 = "93.184.216.34"

HOUSE_URLS = [
    "http://127.0.0.1:6370/v1/health",
    "http://localhost:6369/api/config/editable",
    "http://192.168.0.117:6370/v1/admin/version",
    "http://10.0.0.5/stream",
    "http://[::1]:6370/x",
    "http://169.254.169.254/latest/meta-data/",
    "http://inside.lan.example/stream",  # a name that resolves into the LAN
    "file:///etc/passwd",
]


@pytest.fixture(autouse=True)
def _fake_dns(monkeypatch):
    def resolve(host: str):
        host = host.lower()
        if host.endswith(".lan.example"):
            return [ipaddress.ip_address("192.168.1.20")]
        if host.endswith("example"):
            return [ipaddress.ip_address(PUBLIC_V4)]
        return []

    monkeypatch.setattr(net_safety, "resolve_host", resolve)


# ─── The simulcast resolver ──────────────────────────────────────────────


class _Result:
    def __init__(self, row: Any) -> None:
        self._row = row

    def first(self):
        return self._row


class _Session:
    def __init__(self, store: "_FakeDB") -> None:
        self.store = store

    async def execute(self, statement, params=None):
        sql = str(statement)
        if sql.lstrip().upper().startswith("SELECT"):
            return _Result(self.store.row)
        if "UPDATE" in sql.upper():
            self.store.updates.append(dict(params or {}))
            return _Result((self.store.row[0],))
        raise AssertionError(f"unexpected statement: {sql[:80]}")


class _FakeDB:
    def __init__(self, row: tuple[Any, ...]) -> None:
        self.row = row
        self.updates: list[dict[str, Any]] = []

    @asynccontextmanager
    async def session_scope(self):
        yield _Session(self)


class _Realtime:
    def __init__(self) -> None:
        self.notified: list[tuple[str, str]] = []

    async def notify(self, session, channel: str, payload: str) -> None:
        self.notified.append((channel, payload))


class _CoreConfig:
    use_stubs = True


class _SDK:
    def __init__(self) -> None:
        # An FM favorite with a call sign and no stream URL yet.
        self.db = _FakeDB((7, "KXYZ", "US", "fm", None))
        self.realtime = _Realtime()
        self.core_config = _CoreConfig()


def _directory(monkeypatch, hits: list[RadioBrowserStation]) -> None:
    class _Client:
        async def search(self, **kwargs):
            return hits

    monkeypatch.setattr(simulcast, "get_radio_browser_client", lambda **kw: _Client())


def _hit(url: str, name: str = "KXYZ 98.1", ext: str = "uuid-1") -> RadioBrowserStation:
    return RadioBrowserStation(external_id=ext, name=name, stream_url=url)


@pytest.mark.parametrize("url", HOUSE_URLS)
async def test_the_resolver_never_stores_a_house_url(monkeypatch, url) -> None:
    sdk = _SDK()
    _directory(monkeypatch, [_hit(url)])
    result = await simulcast.resolve_simulcast_for_station(sdk, 7)
    assert result.resolved is False
    assert "refused" in result.message
    assert sdk.db.updates == []
    assert sdk.realtime.notified == []


async def test_the_resolver_skips_a_bad_hit_for_the_next_good_one(monkeypatch) -> None:
    sdk = _SDK()
    _directory(
        monkeypatch,
        [
            _hit("http://127.0.0.1:6370/v1/health", ext="uuid-bad"),
            _hit("http://kxyz.example/live.mp3", name="KXYZ-FM", ext="uuid-good"),
        ],
    )
    result = await simulcast.resolve_simulcast_for_station(sdk, 7)
    assert result.resolved is True
    assert result.stream_url == "http://kxyz.example/live.mp3"
    (update,) = sdk.db.updates
    assert update["stream_url"] == "http://kxyz.example/live.mp3"
    assert update["external_id"] == "uuid-good"


async def test_the_resolver_still_stores_a_public_simulcast(monkeypatch) -> None:
    sdk = _SDK()
    _directory(monkeypatch, [_hit("https://kxyz.example/stream")])
    result = await simulcast.resolve_simulcast_for_station(sdk, 7)
    assert result.resolved is True
    assert [u["stream_url"] for u in sdk.db.updates] == ["https://kxyz.example/stream"]
    assert sdk.realtime.notified == [("stations_changed", "simulcast_resolved")]


def test_pick_best_match_is_unchanged_by_the_iterator() -> None:
    hits = [_hit("http://a.example/", name="WQHHE-FM"), _hit("http://b.example/", name="WQHH 96.5")]
    assert simulcast.pick_best_match(hits, "WQHH").stream_url == "http://b.example/"
    assert simulcast.pick_best_match([], "WQHH") is None


# ─── "stream X": the voice path to the room's music player ───────────────


def _online(url: str) -> dict[str, Any]:
    return {
        "id": 3,
        "name": "News Station",
        "source": "online",
        "stream_url": url,
        "frequency_mhz": None,
        "call_sign": None,
    }


@pytest.mark.parametrize("url", HOUSE_URLS)
async def test_streaming_a_house_url_never_reaches_the_player(stub_sdk, url) -> None:
    h = RadioHandler(stub_sdk)
    resp = await h._stream_station_row(_online(url), Context(room_id="kitchen"), None)
    assert stub_sdk.playback.calls == []
    assert "can't stream News Station" in resp.text


async def test_streaming_a_public_url_still_plays(stub_sdk) -> None:
    h = RadioHandler(stub_sdk)
    resp = await h._stream_station_row(
        _online("http://news.example/live.mp3"), Context(room_id="kitchen"), None
    )
    (call,) = stub_sdk.playback.calls
    assert call["stream_url"] == "http://news.example/live.mp3"
    assert resp.text == "Streaming News Station."


async def test_a_name_that_does_not_resolve_is_left_to_the_player(stub_sdk) -> None:
    """Store-mode at play time: a DNS miss is the player's failure to
    report, not a refusal."""
    h = RadioHandler(stub_sdk)
    await h._stream_station_row(
        _online("http://nowhere.invalid/live.mp3"), Context(room_id="kitchen"), None
    )
    assert [c["stream_url"] for c in stub_sdk.playback.calls] == [
        "http://nowhere.invalid/live.mp3"
    ]


async def test_the_fm_tuner_url_is_not_a_station_url(stub_sdk) -> None:
    """FM plays the tuner's own host-local URL; the check is for rows."""

    class _Tuner:
        async def tune(self, freq: float) -> str:
            return "http://127.0.0.1:8090/fm.mp3"

    stub_sdk.state["sdr_tuner"] = _Tuner()
    h = RadioHandler(stub_sdk)
    station = {
        "id": 4, "name": "WKAR", "source": "fm", "stream_url": None,
        "frequency_mhz": 90.5, "call_sign": "WKAR",
    }
    await h._stream_station_row(station, Context(room_id="kitchen"), None)
    assert [c["stream_url"] for c in stub_sdk.playback.calls] == [
        "http://127.0.0.1:8090/fm.mp3"
    ]
