"""SDK 1.16: the core's player relay (domovoi/player_relay.py).

A room's music player is never handed a remote URL: a plugin asks
``sdk.playback.relay_url`` and the player is handed a token URL on the
core, which makes the only request to the remote server, every redirect
hop checked. ``kind="radio"``: one connection, ICY passed through.
``kind="file"``: byte ranges for a seek or a resume, within the token's
life. These are the radio plugin's M11 relay tests, moved with the code,
plus the file kind's.

DB-free: the core's relay router served by httpx's ASGI transport with
the caller's address set (the room's player, or someone else); DNS faked;
the remote server answered by a MockTransport keyed on the Host header (a
pinned request goes to the address, by name in Host).
"""

from __future__ import annotations

import asyncio
import functools
import ipaddress
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from domovoi import egress, net_safety, player_relay
from domovoi.player_route import PlayerRoute

PUBLIC_V4 = "93.184.216.34"
PLAYER = "172.17.0.5"
ROUTE = PlayerRoute(host="172.17.0.1", peer=PLAYER)
_RealClient = httpx.AsyncClient
AUDIO = b"\xff\xfb\x90\x64" + b"\x00" * 4000
FILE = bytes(range(256)) * 64          # 16 KiB a byte range can be checked against

ICY_BODY = b"A" * 16 + bytes([2]) + b"StreamTitle='x';" + b"\0" * 16 + b"B" * 16


class Remote:
    """The remote server's answers, request by request. Records every
    request (host + path + headers) it saw, house addresses included.
    ``files`` serves byte ranges the way a file server does."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, dict[str, str]]] = []
        self.plan: dict[str, list[httpx.Response]] = {}
        self.files: dict[str, bytes] = {}

    def answer(self, request: httpx.Request) -> httpx.Response:
        host = request.headers.get("host", request.url.host)
        key = host + request.url.path
        self.seen.append((key, dict(request.headers)))
        if key in self.files:
            return _ranged(self.files[key], request.headers.get("range"))
        queue = self.plan.get(key)
        if queue:
            return queue.pop(0) if len(queue) > 1 else queue[0]
        return httpx.Response(404)


def _ranged(data: bytes, wanted: str | None) -> httpx.Response:
    base = {"content-type": "audio/flac", "accept-ranges": "bytes"}
    if not wanted:
        return httpx.Response(200, headers=base, content=data)
    start_s, _, end_s = wanted.removeprefix("bytes=").partition("-")
    start = int(start_s)
    if start >= len(data):
        return httpx.Response(416, headers={"content-range": f"bytes */{len(data)}"})
    end = min(int(end_s) if end_s else len(data) - 1, len(data) - 1)
    return httpx.Response(
        206,
        headers={**base, "content-range": f"bytes {start}-{end}/{len(data)}"},
        content=data[start:end + 1],
    )


@pytest.fixture(autouse=True)
def _fresh_grants():
    player_relay.reset_for_tests()
    yield
    player_relay.reset_for_tests()


@pytest.fixture
def remote(monkeypatch) -> Remote:
    def resolve(host: str):
        return [ipaddress.ip_address(PUBLIC_V4)] if host.lower().endswith("example") else []

    monkeypatch.setattr(net_safety, "resolve_host", resolve)
    rm = Remote()

    def client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(rm.answer)
        return _RealClient(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    return rm


@pytest.fixture
def app() -> FastAPI:
    a = FastAPI()
    a.include_router(player_relay.router)
    return a


async def _get(app: FastAPI, url: str, *, caller: str = PLAYER, **headers: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, client=(caller, 40000))
    async with _RealClient(transport=transport, base_url="http://core") as c:
        return await c.get(httpx.URL(url).raw_path.decode(), headers=headers)


def _audio(content: bytes = AUDIO, **headers: str) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "audio/mpeg", **headers}, content=content)


def _radio(url: str = "http://good.example/live", room: str = "kitchen") -> str:
    return player_relay.mint(room, url, ROUTE, kind="radio")


def _file(url: str = "http://files.example/song.flac", room: str = "kitchen") -> str:
    return player_relay.mint(room, url, ROUTE, kind="file")


# ─── Both kinds: the fetch ────────────────────────────────────────────────


@pytest.mark.parametrize("kind", player_relay.KINDS)
async def test_every_hop_of_the_relays_fetch_is_checked(app, remote, kind) -> None:
    remote.plan["good.example/live"] = [
        httpx.Response(302, headers={"location": "http://10.0.0.8/stream"})
    ]
    url = player_relay.mint("kitchen", "http://good.example/live", ROUTE, kind=kind)
    r = await _get(app, url)
    assert r.status_code == 409
    assert [k for k, _ in remote.seen] == ["good.example/live"]


async def test_a_redirect_into_the_house_is_refused_on_a_range_request_too(app, remote) -> None:
    """A file server that answers the first request with the file and a
    seek with a redirect into the house gets nothing more fetched."""
    remote.plan["sly.example/song.flac"] = [
        httpx.Response(200, headers={"content-type": "audio/flac", "accept-ranges": "bytes"},
                       content=FILE),
        httpx.Response(302, headers={"location": "http://192.168.1.1/admin"}),
    ]
    url = _file("http://sly.example/song.flac")
    assert (await _get(app, url)).status_code == 200
    r = await _get(app, url, Range="bytes=4096-")
    assert r.status_code == 409
    assert not any(k.startswith("192.168.") for k, _ in remote.seen)
    (_, sent) = remote.seen[-1]
    assert sent["range"] == "bytes=4096-"


@pytest.mark.parametrize("kind", player_relay.KINDS)
async def test_a_playlist_is_never_relayed(app, remote, kind) -> None:
    """A player opens a playlist's entries itself, past the relay."""
    remote.plan["good.example/live"] = [_audio(b"#EXTM3U\nhttp://192.168.1.1/x\n")]
    r = await _get(app, player_relay.mint("kitchen", "http://good.example/live", ROUTE, kind=kind))
    assert r.status_code == 502
    remote.plan["good.example/live"] = [
        httpx.Response(200, headers={"content-type": "audio/x-mpegurl"}, content=b"#EXTM3U\n")
    ]
    r = await _get(app, player_relay.mint("kitchen", "http://good.example/live", ROUTE, kind=kind))
    assert r.status_code == 502


@pytest.mark.parametrize("kind", player_relay.KINDS)
async def test_only_media_is_relayed(app, remote, kind) -> None:
    remote.plan["good.example/live"] = [
        httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html>")
    ]
    url = player_relay.mint("kitchen", "http://good.example/live", ROUTE, kind=kind)
    assert (await _get(app, url)).status_code == 502


def test_media_types_by_kind() -> None:
    assert player_relay.media_type("audio/mpeg", "radio") == "audio/mpeg"
    assert player_relay.media_type("video/mp4", "radio") is None
    assert player_relay.media_type("video/mp4", "file") == "video/mp4"
    assert player_relay.media_type("", "file") == "application/octet-stream"
    assert player_relay.media_type("application/vnd.apple.mpegurl", "file") is None
    assert player_relay.media_type("text/html", "file") is None


def test_the_url_is_fixed_when_the_token_is_minted() -> None:
    """The route takes nothing but the token: no URL can be chosen at
    fetch time, and only http(s) can be minted."""
    with pytest.raises(ValueError):
        player_relay.mint("kitchen", "file:///etc/passwd", ROUTE)
    with pytest.raises(ValueError):
        player_relay.mint("kitchen", "http://good.example/x", ROUTE, kind="proxy")
    with pytest.raises(ValueError):
        player_relay.mint("", "http://good.example/x", ROUTE)
    url = _file()
    assert url.startswith("http://172.17.0.1:6370/v1/player-relay/")
    assert "files.example" not in url


# ─── Radio: one connection ───────────────────────────────────────────────


async def test_radio_streams_the_station_to_the_rooms_player(app, remote) -> None:
    remote.plan["good.example/live"] = [_audio()]
    r = await _get(app, _radio())
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/mpeg"
    assert r.content == AUDIO


async def test_a_radio_token_is_good_for_one_connection(app, remote) -> None:
    remote.plan["good.example/live"] = [_audio()]
    url = _radio()
    assert (await _get(app, url)).status_code == 200
    assert (await _get(app, url)).status_code == 404
    assert len(remote.seen) == 1


async def test_radio_ignores_a_range(app, remote) -> None:
    remote.plan["good.example/live"] = [_audio()]
    r = await _get(app, _radio(), Range="bytes=100-")
    assert r.status_code == 200
    (_, sent), = remote.seen
    assert "range" not in sent


async def test_icy_metadata_passes_through_when_the_player_asks(app, remote) -> None:
    remote.plan["good.example/live"] = [_audio(ICY_BODY, **{"icy-metaint": "16", "icy-name": "Good FM"})]
    r = await _get(app, _radio(), **{"Icy-MetaData": "1", "User-Agent": "Music Player Daemon"})
    assert r.status_code == 200
    assert r.headers["icy-metaint"] == "16"
    assert r.headers["icy-name"] == "Good FM"
    assert r.content == ICY_BODY   # the title blocks, byte for byte
    (_, sent), = remote.seen
    assert sent["icy-metadata"] == "1"


async def test_no_icy_metaint_unless_the_player_asked(app, remote) -> None:
    remote.plan["good.example/live"] = [_audio(**{"icy-metaint": "16"})]
    r = await _get(app, _radio())
    assert "icy-metaint" not in r.headers
    (_, sent), = remote.seen
    assert sent["icy-metadata"] == "0"


# ─── File: byte ranges within the token's life ───────────────────────────


async def test_a_file_is_served_whole_with_its_length(app, remote) -> None:
    remote.files["files.example/song.flac"] = FILE
    r = await _get(app, _file())
    assert r.status_code == 200
    assert r.content == FILE
    assert r.headers["content-length"] == str(len(FILE))
    assert r.headers["accept-ranges"] == "bytes"


async def test_a_range_gets_a_partial_response(app, remote) -> None:
    remote.files["files.example/song.flac"] = FILE
    r = await _get(app, _file(), Range="bytes=100-199")
    assert r.status_code == 206
    assert r.content == FILE[100:200]
    assert r.headers["content-range"] == f"bytes 100-199/{len(FILE)}"
    assert r.headers["content-length"] == "100"


async def test_a_seek_after_a_pause_and_more_ranges_within_the_tokens_life(
    app, remote, monkeypatch
) -> None:
    remote.files["files.example/song.flac"] = FILE
    now = [1000.0]
    monkeypatch.setattr(player_relay, "_clock", lambda: now[0])
    url = _file()
    assert (await _get(app, url)).content == FILE
    now[0] += 20 * 60                      # paused twenty minutes
    r = await _get(app, url, Range="bytes=8192-")
    assert r.status_code == 206 and r.content == FILE[8192:]
    now[0] += 3600
    for start in (0, 4000, 12000):
        r = await _get(app, url, Range=f"bytes={start}-")
        assert r.status_code == 206 and r.content == FILE[start:]
    assert len(remote.seen) == 5


async def test_a_range_past_the_end_is_416(app, remote) -> None:
    remote.files["files.example/song.flac"] = FILE
    r = await _get(app, _file(), Range=f"bytes={len(FILE) + 10}-")
    assert r.status_code == 416


async def test_a_malformed_range_serves_the_whole_file(app, remote) -> None:
    remote.files["files.example/song.flac"] = FILE
    r = await _get(app, _file(), Range="bytes=0-10,20-30")
    assert r.status_code == 200 and r.content == FILE
    (_, sent), = remote.seen
    assert "range" not in sent


async def test_a_file_sends_no_icy(app, remote) -> None:
    remote.plan["files.example/song.mp3"] = [_audio(**{"icy-metaint": "16", "icy-name": "X"})]
    r = await _get(app, _file("http://files.example/song.mp3"), **{"Icy-MetaData": "1"})
    assert r.status_code == 200
    assert not any(h.startswith("icy-") for h in r.headers)
    (_, sent), = remote.seen
    assert "icy-metadata" not in sent


async def test_a_file_token_expires(app, remote, monkeypatch) -> None:
    remote.files["files.example/song.flac"] = FILE
    now = [1000.0]
    monkeypatch.setattr(player_relay, "_clock", lambda: now[0])
    url = _file()
    assert (await _get(app, url)).status_code == 200
    now[0] += player_relay.FILE_TTL_SEC + 1
    assert (await _get(app, url, Range="bytes=10-")).status_code == 404
    assert len(remote.seen) == 1


async def test_a_new_request_ends_the_tokens_earlier_stream(remote) -> None:
    remote.files["files.example/song.flac"] = b"x" * 200_000
    token = _file().rsplit("/", 1)[-1]
    first = await player_relay.serve(token, _Request())
    second = await player_relay.serve(token, _Request(range="bytes=1000-"))
    assert await _drain(first) < 200_000
    assert await _drain(second) == 199_000


# ─── The token: open in time, the room, the caller ───────────────────────


@pytest.mark.parametrize("kind", player_relay.KINDS)
async def test_the_token_expires_if_not_opened_in_time(app, remote, monkeypatch, kind) -> None:
    remote.plan["good.example/live"] = [_audio()]
    now = [1000.0]
    monkeypatch.setattr(player_relay, "_clock", lambda: now[0])
    url = player_relay.mint("kitchen", "http://good.example/live", ROUTE, kind=kind)
    now[0] += player_relay.OPEN_WITHIN_SEC + 1
    assert (await _get(app, url)).status_code == 404
    assert remote.seen == []


@pytest.mark.parametrize("kind", player_relay.KINDS)
async def test_only_the_rooms_player_may_use_it_and_a_stranger_changes_nothing(
    app, remote, kind,
) -> None:
    """L23 (review 2026-11-23): the token is readable on open routes, so a
    stranger presenting it is answered 404 and the room's stream goes on."""
    remote.plan["good.example/live"] = [_audio()]
    url = player_relay.mint("kitchen", "http://good.example/live", ROUTE, kind=kind)
    assert (await _get(app, url, caller="192.168.1.30")).status_code == 404
    assert remote.seen == []
    assert (await _get(app, url)).status_code == 200


async def test_a_file_token_rechecks_the_caller_on_every_range(app, remote) -> None:
    remote.files["files.example/song.flac"] = FILE
    url = _file()
    assert (await _get(app, url)).status_code == 200
    assert (await _get(app, url, caller="192.168.1.30", Range="bytes=10-")).status_code == 404
    assert (await _get(app, url, Range="bytes=10-")).status_code == 206
    assert len(remote.seen) == 2


@pytest.mark.parametrize("header", ["X-Forwarded-For", "Forwarded", "X-Real-IP"])
async def test_a_request_with_a_forwarding_header_is_refused(app, remote, header) -> None:
    """L24: the player calls the core directly; a forwarded request (a
    local process claiming the player's address) is refused."""
    remote.files["files.example/song.flac"] = FILE
    url = _file()
    assert (await _get(app, url, **{header: PLAYER})).status_code == 404
    assert remote.seen == []
    assert (await _get(app, url)).status_code == 200


@pytest.mark.parametrize("kind", player_relay.KINDS)
async def test_the_token_is_bound_to_its_room(app, remote, kind) -> None:
    """A room's new relay replaces its old one; another room's is its own."""
    remote.plan["good.example/live"] = [_audio()]
    remote.plan["other.example/live"] = [_audio()]
    old = player_relay.mint("kitchen", "http://good.example/live", ROUTE, kind=kind)
    office = player_relay.mint(
        "office", "http://other.example/live", PlayerRoute(host="172.17.0.1", peer="172.17.0.6"),
        kind=kind,
    )
    new = player_relay.mint("kitchen", "http://good.example/live", ROUTE, kind=kind)
    assert (await _get(app, old)).status_code == 404
    # The kitchen's player can't use the office's.
    assert (await _get(app, office)).status_code == 404
    assert (await _get(app, new)).status_code == 200
    assert [k for k, _ in remote.seen] == ["good.example/live"]


async def test_an_unknown_token_is_404(app, remote) -> None:
    assert (await _get(app, "/v1/player-relay/nope")).status_code == 404


def test_the_relay_url_stands_for_its_target() -> None:
    url = _radio("http://good.example/live")
    assert player_relay.target_for(url) == "http://good.example/live"
    assert player_relay.target_for("http://good.example/live") is None
    player_relay.revoke_room("kitchen")
    # The grant is gone; the record stays while the queue holds the entry
    # (it renews when it plays, L20), and goes when the queue doesn't.
    assert player_relay.target_for(url) == "http://good.example/live"
    player_relay.forget_records("kitchen", keep=set())
    assert player_relay.target_for(url) is None


def test_grants_are_bounded(monkeypatch) -> None:
    monkeypatch.setattr(player_relay, "MAX_GRANTS", 4)
    for n in range(10):
        _file(room=f"room{n}")
    assert len(player_relay._GRANTS) <= 4


# ─── Bounds ──────────────────────────────────────────────────────────────


class _Request:
    """What serve() reads of the player's request."""

    def __init__(self, caller: str = PLAYER, **headers: str) -> None:
        self.client = type("C", (), {"host": caller})()
        self.headers = headers


async def _drain(response, *, after_first=None) -> int:
    got = 0
    async for chunk in response.body_iterator:
        got += len(chunk)
        if after_first is not None:
            after_first()
            after_first = None
    return got


@pytest.mark.parametrize("kind", player_relay.KINDS)
async def test_a_relay_ends_when_the_room_gets_a_new_one_or_stops(remote, kind) -> None:
    remote.plan["good.example/live"] = [_audio(b"x" * 200_000)]
    token = player_relay.mint("kitchen", "http://good.example/live", ROUTE, kind=kind)
    resp = await player_relay.serve(token.rsplit("/", 1)[-1], _Request())
    got = await _drain(resp, after_first=lambda: player_relay.revoke_room("kitchen"))
    assert 0 < got < 200_000


@pytest.mark.parametrize("kind", player_relay.KINDS)
async def test_the_relay_cuts_off_under_never(remote, kind) -> None:
    remote.plan["good.example/live"] = [_audio(b"x" * 200_000)]
    token = player_relay.mint("kitchen", "http://good.example/live", ROUTE, kind=kind)
    resp = await player_relay.serve(token.rsplit("/", 1)[-1], _Request())
    with egress.override_policy("never"):
        got = await _drain(resp)
    assert got < 200_000


async def test_a_request_under_never_is_refused(app, remote) -> None:
    remote.files["files.example/song.flac"] = FILE
    url = _file()
    with egress.override_policy("never"):
        r = await _get(app, url, Range="bytes=10-")
    assert r.status_code == 409
    assert remote.seen == []


async def test_the_relay_ends_at_its_time_limit(remote, monkeypatch) -> None:
    remote.plan["good.example/live"] = [_audio(b"x" * 200_000)]
    now = [0.0]
    monkeypatch.setattr(player_relay, "_clock", lambda: now[0])
    token = _radio().rsplit("/", 1)[-1]
    resp = await player_relay.serve(token, _Request())

    def later() -> None:
        now[0] += player_relay.MAX_DURATION_SEC + 1

    got = await _drain(resp, after_first=later)
    assert 0 < got < 200_000


async def test_the_relay_holds_a_stream_to_its_ceiling(remote, monkeypatch) -> None:
    """Past the burst, a remote server that sends faster than the ceiling
    is read no faster than it."""
    monkeypatch.setattr(player_relay, "BURST_BYTES", 32 * 1024)
    monkeypatch.setattr(player_relay, "MAX_BYTES_PER_SEC", 16 * 1024)
    slept: list[float] = []

    async def fake_sleep(sec: float) -> None:
        slept.append(sec)

    monkeypatch.setattr(player_relay.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(player_relay, "_clock", lambda: 0.0)
    remote.plan["good.example/live"] = [_audio(b"x" * (96 * 1024))]
    token = _radio().rsplit("/", 1)[-1]
    await _drain(await player_relay.serve(token, _Request()))
    # 64 KiB past a 32 KiB burst at 16 KiB/s: four seconds of waiting.
    assert slept and max(slept) == pytest.approx(4.0)


# ─── The SDK call ────────────────────────────────────────────────────────


async def test_sdk_relay_url_mints_for_the_rooms_route() -> None:
    from domovoi.sdk import PlaybackAPI, RelayUnavailable

    api = PlaybackAPI("someplugin")
    url = await api.relay_url("kitchen", "http://files.example/a.ogg")
    assert url.startswith("http://127.0.0.1:6370/v1/player-relay/")   # stubs: loopback
    assert api.relay_target(url) == "http://files.example/a.ogg"
    with pytest.raises(ValueError):
        await api.relay_url("kitchen", "http://files.example/a.ogg", kind="other")
    assert issubclass(RelayUnavailable, RuntimeError)


async def test_sdk_relay_url_raises_without_a_route(monkeypatch) -> None:
    from domovoi.sdk import PlaybackAPI, RelayUnavailable
    from domovoi.sdk import playback as playback_mod

    async def no_route(_room):
        return None

    monkeypatch.setattr(playback_mod, "player_route", no_route)
    with pytest.raises(RelayUnavailable):
        await PlaybackAPI("x").relay_url("kitchen", "http://files.example/a.ogg")


async def test_play_url_judges_a_relay_url_by_its_target_under_never(monkeypatch) -> None:
    from domovoi.now_playing import NOW_PLAYING
    from domovoi.sdk import PlaybackAPI

    source = "someplugin"
    monkeypatch.setattr(NOW_PLAYING, "sources", lambda: {source})
    api = PlaybackAPI("someplugin")
    url = await api.relay_url("kitchen", "http://files.example/a.ogg")
    with egress.override_policy("never"):
        resp = await api.play_url("kitchen", url, title="A", source=source)
    assert resp.music_action is None
    assert resp.data["status"] == "internet_off"


def test_the_route_is_open_and_marked_so() -> None:
    """The player has no credential: the route wears no gate, is marked
    @open_endpoint, and the token is what it checks."""
    from domovoi.webkit import is_open_endpoint

    assert is_open_endpoint(player_relay.player_relay_stream)


# ─── H2: nothing is decompressed ─────────────────────────────────────────


@functools.lru_cache(maxsize=1)
def _nested_gzip(size: int, depth: int = 2) -> bytes:
    import gzip

    data = b"\0" * size
    for _ in range(depth):
        data = gzip.compress(data, compresslevel=9)
    return data


@pytest.mark.parametrize("kind", player_relay.KINDS)
@pytest.mark.parametrize("coding", ["gzip, gzip", "gzip", "br", "deflate"])
async def test_a_compressed_answer_is_refused_not_decoded(app, remote, kind, coding) -> None:
    """H2 (review 2026-11-23): a station answering with nested gzip (a few
    KB on the wire, hundreds of MB decoded) gets a 502 before a byte is
    decoded; the request asked for identity."""
    bomb = _nested_gzip(32 * 1024 * 1024)
    assert len(bomb) < 200_000
    # A stream, not content=: httpx would decode content= as it builds the
    # answer, here in the test.
    remote.plan["good.example/live"] = [httpx.Response(
        200, headers={"content-type": "audio/mpeg", "content-encoding": coding},
        stream=httpx.ByteStream(bomb),
    )]
    r = await _get(app, player_relay.mint("kitchen", "http://good.example/live", ROUTE, kind=kind))
    assert r.status_code == 502
    assert "compressed" in r.json()["detail"]
    (_, sent) = remote.seen[-1]
    assert sent["accept-encoding"] == "identity"


async def test_the_relay_reads_raw_bytes_in_capped_chunks(remote, monkeypatch) -> None:
    """The body is read through net_safety.iter_raw, chunk by chunk, never
    through the library's decoders, and no chunk passed on is larger than
    the relay's chunk size."""
    calls: list[int] = []
    real = net_safety.iter_raw

    async def spy(response, chunk_size=65536):
        calls.append(chunk_size)
        async for c in real(response, chunk_size=chunk_size):
            yield c

    monkeypatch.setattr(net_safety, "iter_raw", spy)
    remote.plan["good.example/live"] = [_audio(b"x" * 200_000)]
    resp = await player_relay.serve(_radio().rsplit("/", 1)[-1], _Request())
    sizes = [len(c) async for c in resp.body_iterator]
    assert calls == [player_relay._CHUNK]
    assert sum(sizes) == 200_000 and max(sizes) <= player_relay._CHUNK


# ─── M4: a stalled upstream ends ─────────────────────────────────────────


# Whole chunks: a raw read hands on full chunks while bytes keep coming.
STALL_HEAD = AUDIO[:4] + bytes(2 * player_relay._CHUNK - 4)


def _stalled(monkeypatch, *, first: bytes | None) -> asyncio.Event:
    """The remote server answers nothing (``first=None``), or its headers
    and ``first`` then nothing more."""
    import asyncio as _asyncio

    never = _asyncio.Event()

    class Stall(httpx.AsyncByteStream):
        async def __aiter__(self):
            if first:
                yield first
            await never.wait()

    class Transport(httpx.AsyncBaseTransport):
        # Not MockTransport: it reads the whole answer before handing it on.
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if first is None:
                await never.wait()
            return httpx.Response(200, headers={"content-type": "audio/mpeg"}, stream=Stall())

    def resolve(host: str):
        return [ipaddress.ip_address(PUBLIC_V4)]

    monkeypatch.setattr(net_safety, "resolve_host", resolve)

    def client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = Transport()
        return _RealClient(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    return never


async def test_an_upstream_that_never_answers_is_a_504_in_time(app, monkeypatch) -> None:
    """M4 (review 2026-11-23): a server that accepts and never answers no
    longer holds the player's request (and one of the core's connections)
    forever."""
    import time as _time

    _stalled(monkeypatch, first=None)
    monkeypatch.setattr(player_relay, "OPEN_DEADLINE_SEC", 0.3)
    began = _time.monotonic()
    r = await _get(app, _radio())
    assert r.status_code == 504
    assert _time.monotonic() - began < 5


async def test_a_stream_that_stalls_ends_at_the_read_timeout(monkeypatch) -> None:
    _stalled(monkeypatch, first=STALL_HEAD)
    monkeypatch.setattr(player_relay, "READ_TIMEOUT_SEC", 0.3)
    monkeypatch.setattr(player_relay, "CHECK_EVERY_SEC", 0.05)
    resp = await player_relay.serve(_radio().rsplit("/", 1)[-1], _Request())
    assert await asyncio.wait_for(_drain(resp), 5) == len(STALL_HEAD)


async def test_a_stop_ends_a_stalled_stream_without_waiting_for_bytes(monkeypatch) -> None:
    """The read waits on bytes that never come; the stop still ends it
    (the grant is looked at while the read waits)."""
    _stalled(monkeypatch, first=STALL_HEAD[:player_relay._CHUNK])
    monkeypatch.setattr(player_relay, "CHECK_EVERY_SEC", 0.05)
    resp = await player_relay.serve(_radio().rsplit("/", 1)[-1], _Request())
    got = await asyncio.wait_for(
        _drain(resp, after_first=lambda: player_relay.revoke_room("kitchen")), 5)
    assert got == player_relay._CHUNK


# ─── L25: minting ────────────────────────────────────────────────────────


async def test_minting_checks_the_url_before_ending_the_rooms_relay(monkeypatch) -> None:
    """L25: the outbound check runs before the room's current relay is
    ended, and a made-up room name can't evict a real room's relay."""
    from domovoi.clients import mpd as mpd_module
    from domovoi.sdk import PlaybackAPI, RelayUnavailable

    monkeypatch.setattr(mpd_module, "_room_ports", {"kitchen": (6650, 8050)})
    api = PlaybackAPI("someplugin")
    good = await api.relay_url("kitchen", "http://93.184.216.34/a.ogg")
    with pytest.raises(ValueError):
        await api.relay_url("kitchen", "http://192.168.1.1/admin")
    assert api.relay_target(good) == "http://93.184.216.34/a.ogg"
    for n in range(5):
        with pytest.raises(RelayUnavailable):
            await api.relay_url(f"made-up-{n}", "http://93.184.216.34/b.ogg")
    assert api.relay_target(good) == "http://93.184.216.34/a.ogg"


# ─── L20: a queued relay entry outlives its token ─────────────────────────


class _Player:
    """A room's own player as the renewal asks it: its current song and state."""

    def __init__(self, file: str | None = None, state: str = "play") -> None:
        self.file = file
        self.st = state

    async def current_song(self):
        return {"file": self.file} if self.file else None

    async def state(self) -> str:
        return self.st


@pytest.fixture
def player(monkeypatch):
    from domovoi.clients import mpd as mpd_mod

    p = _Player()
    monkeypatch.setitem(mpd_mod._clients, "kitchen", p)

    async def route(room_id):
        return ROUTE

    monkeypatch.setattr(player_relay, "_route_for", route)
    return p


async def test_a_long_playing_station_renews_while_its_entry_plays(app, remote, player, monkeypatch) -> None:
    now = [1000.0]
    monkeypatch.setattr(player_relay, "_clock", lambda: now[0])
    remote.plan["good.example/live"] = [_audio(), _audio(), _audio()]
    url = _radio()
    player.file = url
    assert (await _get(app, url)).status_code == 200          # its one connection
    now[0] += player_relay.MAX_DURATION_SEC + 60                # a day and more later
    r = await _get(app, url)                                    # the player reconnects
    assert r.status_code == 200 and r.content == AUDIO
    assert (await _get(app, url, caller="192.168.1.30")).status_code == 404   # never anyone else


async def test_a_file_renews_past_its_hours_only_while_it_is_the_current_song(
    app, remote, player, monkeypatch,
) -> None:
    now = [1000.0]
    monkeypatch.setattr(player_relay, "_clock", lambda: now[0])
    remote.files["files.example/song.flac"] = FILE
    url = _file()
    player.file = url
    assert (await _get(app, url)).status_code == 200
    now[0] += player_relay.FILE_TTL_SEC + 1
    player.st = "stop"                                          # stopped: no renewal
    assert (await _get(app, url, Range="bytes=10-")).status_code == 404
    player.st = "pause"                                         # paused on it, then a seek
    r = await _get(app, url, Range="bytes=10-")
    assert r.status_code == 206 and r.content == FILE[10:]
    player.file = "Artist/other.mp3"                            # another song now
    player_relay.revoke_room("kitchen")
    assert (await _get(app, url)).status_code == 404


async def test_a_station_resumes_through_the_relay_after_a_core_restart(
    app, remote, player, tmp_path, monkeypatch,
) -> None:
    monkeypatch.setenv("DOMOVOI_PLAYER_RELAY_DIR", str(tmp_path))
    remote.plan["good.example/live"] = [_audio(), _audio()]
    url = _radio()
    saved = (tmp_path / "entries.json").read_text(encoding="utf-8")
    assert url.rsplit("/", 1)[1] not in saved                  # a hash of the token, never the token
    # The core restarts: every grant and in-memory record is gone.
    player_relay._GRANTS.clear()
    player_relay._RECORDS.clear()
    player_relay._records_loaded = False
    player.file = url                                           # MPD resumed its queue
    r = await _get(app, url)
    assert r.status_code == 200 and r.content == AUDIO
    assert [k for k, _ in remote.seen] == ["good.example/live"]


async def test_a_token_the_core_never_minted_is_not_renewed(app, remote, player) -> None:
    url = ROUTE.url("/v1/player-relay/" + "A" * 43)
    player.file = url
    assert (await _get(app, url)).status_code == 404
    assert remote.seen == []
