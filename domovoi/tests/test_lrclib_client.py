"""The LRCLIB client (domovoi/clients/lrclib.py, contract §7.1 [R1]–[R8]).

DB-free and offline: every answer comes from an ``httpx.MockTransport``
handed to the client, which passes it to ``egress.async_client`` — so the
egress hook runs exactly as it does in front of the internet. The record
shapes are the ones LRCLIB answers with (``id, name, trackName,
artistName, albumName, duration, instrumental, plainLyrics, syncedLyrics,
hasWordSync, lyricsfile``, checked by one live probe of three tracks on
2026-10-05 that printed key names and value types only); every lyric in
them is INVENTED ([C2]).
"""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest

from domovoi import egress
from domovoi.clients import lrclib as lrclib_mod
from domovoi.clients.lrclib import (
    MAX_BODY_BYTES,
    HttpLrclibClient,
    LrclibRateLimited,
    LrclibRejected,
    LrclibStubClient,
    LrclibUnavailable,
    get_lrclib_client,
    parse_record,
    parse_retry_after,
    user_agent,
)
from domovoi.lyrics import domovoi_version

L1 = "the lantern hums beside the river door"
L2 = "and every copper kettle sings at dawn"
SYNCED = f"[00:12.40] {L1}\n[00:16.85] {L2}\n"
PLAIN = f"{L1}\n{L2}"


def record(**over) -> dict:
    """A record as LRCLIB answers it (camelCase; the key set and value types
    of the 2026-10-05 live probe, ``hasWordSync`` and ``lyricsfile``
    included and unused), invented content."""
    rec = {
        "id": 4321,
        "name": "Lantern Song",
        "trackName": "Lantern Song",
        "artistName": "The Example Band",
        "albumName": "Glass Harbor",
        "duration": 205.0,
        "instrumental": False,
        "plainLyrics": PLAIN,
        "syncedLyrics": SYNCED,
        "hasWordSync": False,
        "lyricsfile": f"[00:12.40]{L1}",
    }
    rec.update(over)
    return rec


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


class Recorder:
    """A MockTransport handler: answers from a script, remembers requests."""

    def __init__(self, *answers, clock: FakeClock | None = None) -> None:
        self.answers = list(answers)
        self.requests: list[httpx.Request] = []
        self.starts: list[float] = []
        self.clock = clock

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.clock is not None:
            self.starts.append(self.clock())
        answer = self.answers.pop(0) if self.answers else httpx.Response(404)
        if isinstance(answer, Exception):
            raise answer
        return answer


def client_for(handler: Recorder, clock: FakeClock | None = None) -> HttpLrclibClient:
    clock = clock or FakeClock()
    return HttpLrclibClient(transport=httpx.MockTransport(handler), clock=clock, sleep=clock.sleep)


def run(coro):
    return asyncio.run(coro)


# ─── Requests [R1], [R2], [R5] ────────────────────────────────────────────


def test_get_sends_the_names_album_and_duration_with_the_user_agent() -> None:
    rec = Recorder(httpx.Response(200, json=record()))
    got = run(client_for(rec).get(track="Lantern Song", artist="The Example Band",
                                  album="Glass Harbor", duration=205))
    (req,) = rec.requests
    assert req.method == "GET"
    assert str(req.url).startswith("https://lrclib.net/api/get?")
    assert dict(req.url.params) == {"track_name": "Lantern Song", "artist_name": "The Example Band",
                                    "album_name": "Glass Harbor", "duration": "205"}
    assert req.headers["User-Agent"] == (
        f"Domovoi v{domovoi_version()} (https://github.com/coders-farm-official/domovoi)"
    ) == user_agent()
    assert req.headers["Accept"] == "application/json"
    assert got.id == 4321 and got.track_name == "Lantern Song" and got.album_name == "Glass Harbor"
    assert got.duration == 205.0 and got.instrumental is False
    assert got.plain == PLAIN and got.synced == SYNCED


@pytest.mark.parametrize("album, duration, expected", [
    (None, 205, {"duration": "205"}),
    ("  ", 205, {"duration": "205"}),
    ("Glass Harbor", None, {"album_name": "Glass Harbor"}),
    ("Glass Harbor", 0, {"album_name": "Glass Harbor"}),
    ("Glass Harbor", 3601, {"album_name": "Glass Harbor"}),
    ("Glass Harbor", 3600, {"album_name": "Glass Harbor", "duration": "3600"}),
    (None, 1, {"duration": "1"}),
])
def test_get_omits_what_it_should(album, duration, expected) -> None:
    rec = Recorder(httpx.Response(200, json=record()))
    run(client_for(rec).get(track="t", artist="a", album=album, duration=duration))
    assert dict(rec.requests[0].url.params) == {"track_name": "t", "artist_name": "a", **expected}


def test_search_sends_the_title_and_the_artist_when_given() -> None:
    rec = Recorder(httpx.Response(200, json=[record()]), httpx.Response(200, json=[]))
    c = client_for(rec)
    assert [r.id for r in run(c.search(track="Lantern Song", artist="The Example Band"))] == [4321]
    assert run(c.search(track="Lantern Song", artist=None)) == []
    assert str(rec.requests[0].url).startswith("https://lrclib.net/api/search?")
    assert dict(rec.requests[0].url.params) == {"track_name": "Lantern Song",
                                                "artist_name": "The Example Band"}
    assert dict(rec.requests[1].url.params) == {"track_name": "Lantern Song"}


def test_only_the_first_twenty_search_records_are_used() -> None:
    records = [record(id=i) for i in range(1, 31)]
    rec = Recorder(httpx.Response(200, json=records))
    assert [r.id for r in run(client_for(rec).search(track="t", artist="a"))] == list(range(1, 21))


# ─── Records [R7] ─────────────────────────────────────────────────────────


def test_camel_case_and_snake_case_records() -> None:
    snake = {"id": 7, "track_name": "Glass Harbor", "artist_name": "The Velvet Kites",
             "album_name": None, "duration": 180, "instrumental": True,
             "plain_lyrics": None, "synced_lyrics": "  "}
    r = parse_record(snake)
    assert (r.id, r.track_name, r.artist_name, r.album_name) == (7, "Glass Harbor", "The Velvet Kites", None)
    assert r.duration == 180.0 and r.instrumental is True and r.plain is None and r.synced is None


@pytest.mark.parametrize("bad", [
    {"id": "7", "trackName": "t", "artistName": "a"},
    {"id": True, "trackName": "t", "artistName": "a"},
    {"id": 7, "trackName": None, "artistName": "a"},
    {"id": 7, "trackName": "t"},
    ["not", "a", "record"],
    None,
])
def test_records_without_an_int_id_or_string_names_are_skipped(bad) -> None:
    assert parse_record(bad) is None


def test_odd_fields_are_tolerated() -> None:
    r = parse_record(record(duration="205", albumName=12, instrumental="yes"))
    assert r.duration is None and r.album_name is None and r.instrumental is False


def test_a_search_of_only_bad_records_is_unavailable_but_some_bad_ones_are_skipped() -> None:
    rec = Recorder(httpx.Response(200, json=[{"id": "x"}, {"nope": 1}]),
                   httpx.Response(200, json=[{"id": "x"}, record(id=9)]))
    c = client_for(rec)
    with pytest.raises(LrclibUnavailable) as e:
        run(c.search(track="t", artist="a"))
    assert e.value.code == "shape"
    assert [r.id for r in run(c.search(track="t", artist="a"))] == [9]


# ─── Answers [R6] ─────────────────────────────────────────────────────────


def test_get_404_is_none() -> None:
    body = {"code": 404, "name": "TrackNotFound", "message": "Failed to find specified track"}
    rec = Recorder(httpx.Response(404, json=body))
    assert run(client_for(rec).get(track="t", artist="a", album=None, duration=200)) is None


@pytest.mark.parametrize("status", [400, 401, 403, 410, 422])
def test_other_4xx_are_rejected(status) -> None:
    rec = Recorder(httpx.Response(status, json={"name": "ValidationError"}))
    with pytest.raises(LrclibRejected) as e:
        run(client_for(rec).get(track="t", artist="a", album=None, duration=200))
    assert e.value.status == status


def test_a_search_404_is_a_refusal_too() -> None:
    rec = Recorder(httpx.Response(404))
    with pytest.raises(LrclibRejected):
        run(client_for(rec).search(track="t", artist="a"))


@pytest.mark.parametrize("status, header, expected", [
    (429, "30", 30.0), (503, "1", 1.0), (503, None, None), (429, "soon", None),
])
def test_429_and_503_are_rate_limits(status, header, expected) -> None:
    headers = {"Retry-After": header} if header else {}
    rec = Recorder(httpx.Response(status, headers=headers))
    with pytest.raises(LrclibRateLimited) as e:
        run(client_for(rec).get(track="t", artist="a", album=None, duration=200))
    assert e.value.retry_after == expected


def _stream_body(n: int) -> httpx.Response:
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            sent = 0
            while sent < n:
                chunk = b" " * min(65536, n - sent)
                sent += len(chunk)
                yield chunk

    return httpx.Response(200, stream=Stream())


@pytest.mark.parametrize("answer, code", [
    (httpx.Response(500), "http_500"),
    (httpx.Response(502), "http_502"),
    (httpx.Response(301, headers={"Location": "https://elsewhere.example/"}), "http_301"),
    (httpx.Response(200, content=b"<html>a proxy page</html>"), "not_json"),
    (httpx.Response(200, json={"unexpected": True}), "shape"),
    (httpx.Response(200, content=b"[" + b" " * (3 * 1024 * 1024) + b"]"), "too_large"),
    (httpx.ReadTimeout("slow"), "timeout"),
    (httpx.ConnectError("no route"), "network"),
])
def test_everything_else_is_unavailable(answer, code) -> None:
    rec = Recorder(answer)
    with pytest.raises(LrclibUnavailable) as e:
        run(client_for(rec).get(track="t", artist="a", album=None, duration=200))
    assert e.value.code == code
    assert len(rec.requests) == 1                     # never retried, never redirected


def test_a_streamed_body_over_the_cap_is_not_read() -> None:
    rec = Recorder(_stream_body(MAX_BODY_BYTES + 10))
    with pytest.raises(LrclibUnavailable) as e:
        run(client_for(rec).search(track="t", artist="a"))
    assert e.value.code == "too_large"


def test_retry_after_parsing() -> None:
    assert parse_retry_after("2.5") == 2.5
    assert parse_retry_after("") is None and parse_retry_after(None) is None
    assert parse_retry_after("-1") is None
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") is None


# ─── Pacing [R3] ──────────────────────────────────────────────────────────


def test_at_least_one_second_between_request_starts() -> None:
    clock = FakeClock()
    starts: list[float] = []

    def by_path(request: httpx.Request) -> httpx.Response:
        starts.append(clock())
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    c = HttpLrclibClient(transport=httpx.MockTransport(by_path), clock=clock, sleep=clock.sleep)

    async def go():
        await c.get(track="t", artist="a", album=None, duration=200)
        await c.get(track="t", artist="a", album=None, duration=200)
        clock.t += 5.0                                # a long gap needs no wait
        await c.get(track="t", artist="a", album=None, duration=200)
        await asyncio.gather(c.get(track="t", artist="a", album=None, duration=200),
                             c.search(track="t", artist="a"))

    run(go())
    rec = type("R", (), {"starts": starts})
    gaps = [b - a for a, b in zip(rec.starts, rec.starts[1:])]
    assert len(rec.starts) == 5
    assert all(g >= 1.0 for g in gaps), gaps
    assert clock.sleeps[0] == pytest.approx(1.0)
    assert gaps[1] == pytest.approx(5.0)


# ─── The egress gate [R4] ─────────────────────────────────────────────────


def test_under_never_no_request_reaches_the_transport() -> None:
    rec = Recorder(httpx.Response(200, json=record()))
    c = client_for(rec)
    with egress.override_policy("never"):
        with pytest.raises(egress.InternetTurnedOff):
            run(c.get(track="t", artist="a", album=None, duration=200))
        with pytest.raises(egress.InternetTurnedOff):
            run(c.search(track="t", artist="a"))
    assert rec.requests == []
    # sometimes / always / unanswered all reach it
    with egress.override_policy("sometimes"):
        assert run(c.get(track="t", artist="a", album=None, duration=200)).id == 4321


def test_the_egress_hook_itself_refuses_when_the_answer_changes_mid_way(monkeypatch) -> None:
    """``require_destination`` passed, then the answer became never: the
    hook on the client refuses before the transport sees anything."""
    rec = Recorder(httpx.Response(200, json=record()))
    c = client_for(rec)
    calls = {"n": 0}
    real = egress.require_destination

    def first_passes(url):
        calls["n"] += 1
        if calls["n"] > 2:
            real(url)

    monkeypatch.setattr(egress, "require_destination", first_passes)
    with egress.override_policy("never"):
        with pytest.raises(egress.InternetTurnedOff):
            run(c.get(track="t", artist="a", album=None, duration=200))
    assert rec.requests == []


# ─── Never a lyric in a log or an error [R8], [C3], [C4] ──────────────────


def test_no_lyric_text_in_logs_errors_or_reprs(caplog) -> None:
    answers = [
        httpx.Response(200, json=record()),
        httpx.Response(200, content=("not json " + L1).encode()),
        httpx.Response(500, content=L2.encode()),
        httpx.Response(400, content=L1.encode()),
    ]
    rec = Recorder(*answers)
    c = client_for(rec)
    messages = []
    with caplog.at_level(logging.DEBUG):
        got = run(c.get(track="t", artist="a", album=None, duration=200))
        messages.append(repr(got))
        for exc in (LrclibUnavailable, LrclibUnavailable, LrclibRejected):
            with pytest.raises(exc) as e:
                run(c.get(track="t", artist="a", album=None, duration=200))
            messages.append(str(e.value))
            messages.append(repr(e.value))
    text = caplog.text + "\n".join(messages)
    for word in ("lantern", "kettle", "copper", "river door"):
        assert word not in text


def test_httpx_request_lines_never_put_a_title_in_the_info_log(caplog, monkeypatch) -> None:
    """httpx logs each request's URL at INFO; LRCLIB's carries the title
    and the artist ([R30]). Kept out unless the server logs at DEBUG."""
    root = logging.getLogger()
    monkeypatch.setattr(root, "level", logging.INFO)
    rec = Recorder(httpx.Response(404), httpx.Response(404))
    c = client_for(rec)
    with caplog.at_level(logging.INFO, logger="httpx"):
        run(c.get(track="Velvet Kites Lullaby", artist="The Example Band", album=None, duration=200))
    assert "Velvet Kites" not in caplog.text and "lrclib.net" not in caplog.text
    caplog.clear()
    monkeypatch.setattr(root, "level", logging.DEBUG)
    with caplog.at_level(logging.DEBUG, logger="httpx"):
        run(c.get(track="Velvet Kites Lullaby", artist="The Example Band", album=None, duration=200))
    assert "lrclib.net" in caplog.text                 # debugging shows them again
    other = logging.getLogger("httpx").makeRecord("httpx", logging.INFO, __file__, 1,
                                                  "HTTP Request: GET %s", ("https://example.org/feed",), None)
    assert all(f.filter(other) for f in logging.getLogger("httpx").filters)


# ─── The process-wide client ──────────────────────────────────────────────


def test_stubs_get_the_stub(monkeypatch) -> None:
    from domovoi.config import settings

    monkeypatch.setattr(lrclib_mod, "_client", None)
    monkeypatch.setattr(settings, "use_stubs", True)
    c = get_lrclib_client()
    assert isinstance(c, LrclibStubClient)
    assert get_lrclib_client() is c
    assert run(c.get(track="t", artist="a", album=None, duration=1)) is None
    assert run(c.search(track="t", artist="a")) == []
    monkeypatch.setattr(lrclib_mod, "_client", None)
    monkeypatch.setattr(settings, "use_stubs", False)
    assert isinstance(get_lrclib_client(), HttpLrclibClient)


def test_the_answer_shape_is_valid_json_round_trip() -> None:
    """The fixture record is what LRCLIB's JSON looks like."""
    raw = json.loads(json.dumps(record()))
    assert parse_record(raw) == parse_record(record())
