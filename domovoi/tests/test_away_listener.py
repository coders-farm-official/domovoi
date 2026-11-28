"""Away chat's listener socket (domovoi/away/listener.py) over loopback.

DB-free: the real listener on 127.0.0.1 and a phone speaking the wire
(domovoi/away/loopback.py, ``core=False``); the test plays core, reading
the listener's ``away`` events and answering on its "stdin"
(``AwayState.from_core``). Pinned: the silent refusals (§2.3), the house
hello, requests handed to core with their device, ERR for a bad request on
an open connection, pictures reassembled and checked, the per-connection
and per-device limits (§4.6), revocation within seconds (BYE revoked /
away_off), the third connection replacing the oldest, and the idle timer.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import struct
import sys
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from domovoi.away import listener as al
from domovoi.away import wire
from domovoi.away.loopback import AwayLoopback

UUID = "6f1c2b9e-0d4a-4f7e-9c3b-2a1d5e8f7a{:02d}"


def _send(n: int = 0, **over) -> dict:
    doc = {"op": "chat.send", "chat_id": None, "client_id": UUID.format(n), "text": "hello"}
    doc.update(over)
    return doc


@pytest.fixture
async def house(tmp_path):
    async with AwayLoopback(tmp_path / "listener", core=False) as loop:
        loop.key = X25519PrivateKey.generate()
        loop.set_phones({_pub(loop.key): "android-alex"})
        yield loop


def _pub(key: X25519PrivateKey) -> bytes:
    from domovoi.friend_houses.noise import public_bytes

    return public_bytes(key)


async def _phone(house):
    phone = house.phone(house.key)
    assert await phone.connect(house.port) == (1, 1_048_576)
    return phone


async def _events(house, kind: str = "away", n: int = 1, timeout: float = 5.0) -> list[dict]:
    async def wait():
        while len([e for e in house.events if e["event"] == kind]) < n:
            await asyncio.sleep(0.01)
    await asyncio.wait_for(wait(), timeout)
    return [e for e in house.events if e["event"] == kind]


def _core(house, event: dict, kind: str, body: dict, *, end: bool = True) -> None:
    house.state.from_core(json.dumps({"c": int(event["conn"]), "r": int(event["req"]), "k": kind,
                                      "b": body, "end": end}).encode())


async def _silent(house, raw: bytes) -> bool:
    """Whether the house closes a raw stream without sending a byte."""
    r, w = await asyncio.open_connection("127.0.0.1", house.port)
    w.write(raw)
    await w.drain()
    try:
        data = await asyncio.wait_for(r.read(), 10)
    finally:
        w.close()
    return data == b""


# ─── the handshake ───────────────────────────────────────────────────────


async def test_a_current_phone_gets_the_house_hello_and_pongs(house) -> None:
    phone = await _phone(house)
    await phone.send(wire.PING)
    pong = await phone.recv()
    assert (pong.kind, pong.req, pong.body) == (wire.PONG, 0, b"")
    phone.close()


async def test_silent_refusals_before_the_handshake(house) -> None:
    assert await _silent(house, b"DFH1" + b"\x00\x10" + b"x" * 16)               # wrong preface
    assert await _silent(house, wire.PREFACE + b"\x00\x00")                       # zero frame
    assert await _silent(house, wire.PREFACE + struct.pack(">H", wire.MSG1_MAX + 1) + b"x" * 200)
    assert await _silent(house, wire.PREFACE + struct.pack(">H", wire.MSG1_MIN) + b"x" * wire.MSG1_MIN)
    assert not [e for e in house.events if e["event"] == "away"]


async def test_a_key_not_on_the_current_list_is_refused_silently(house) -> None:
    stranger = house.phone()  # its own fresh key
    with pytest.raises(asyncio.IncompleteReadError):
        await stranger.connect(house.port)


async def test_a_wrong_house_key_fails_the_handshake_silently(house) -> None:
    from domovoi.away.loopback import Phone

    lost = Phone(_pub(X25519PrivateKey.generate()), house.key)  # thinks the house has another key
    with pytest.raises(asyncio.IncompleteReadError):
        await lost.connect(house.port)


@pytest.mark.parametrize("hello", [
    struct.pack(">HBB", 2, 1, 0),           # a proto the house doesn't speak
    struct.pack(">HBB", 1, 9, 0),           # unknown platform
    struct.pack(">HBB", 1, 1, 5) + b"1",    # wrong length
])
async def test_a_bad_hello_is_refused_silently(house, hello: bytes) -> None:
    with pytest.raises(asyncio.IncompleteReadError):
        await house.phone(house.key).connect(house.port, hello=hello)


async def test_away_off_refuses_every_phone(house) -> None:
    house.set_phones({_pub(house.key): "android-alex"}, on=False)
    with pytest.raises(asyncio.IncompleteReadError):
        await house.phone(house.key).connect(house.port)


async def test_an_unreadable_phone_list_lets_nobody_in(house) -> None:
    (house.folder / al.PHONES_FILE).write_text("{not json", encoding="utf-8")
    with pytest.raises(asyncio.IncompleteReadError):
        await house.phone(house.key).connect(house.port)


async def test_no_first_frame_in_time_is_closed(house, monkeypatch) -> None:
    monkeypatch.setattr(al, "HANDSHAKE_DEADLINE_SEC", 0.3)
    assert await _silent(house, wire.PREFACE)


async def test_the_house_wide_handshake_budget(house, monkeypatch) -> None:
    monkeypatch.setattr(house.state, "_handshakes", al.Window(60.0, 1))
    (await _phone(house)).close()
    with pytest.raises(asyncio.IncompleteReadError):
        await house.phone(house.key).connect(house.port)


# ─── requests ────────────────────────────────────────────────────────────


async def test_a_request_reaches_core_with_its_device_and_the_answer_comes_back(house) -> None:
    phone = await _phone(house)
    await phone.request(5, {"op": "chat.list", "limit": 10})
    (event,) = await _events(house)
    assert (event["device"], event["req"], event["noise"]) == ("android-alex", "5", _pub(house.key).hex())
    assert json.loads(event["request"]) == {"op": "chat.list", "archived": False, "limit": 10, "cursor": None}
    big = {"threads": [{"id": i, "title": "t" * 900} for i in range(120)], "next": None}
    _core(house, event, "resp", big)
    assert await phone.answer(5) == [(wire.RESP, big)]  # split with MORE and joined again
    phone.close()


async def test_a_request_in_pieces_is_joined(house) -> None:
    phone = await _phone(house)
    await phone.request(6, _send(text="z" * 3000), piece=500)
    (event,) = await _events(house)
    assert json.loads(event["request"])["text"] == "z" * 3000
    phone.close()


async def test_a_bad_request_is_an_err_and_the_connection_stays(house) -> None:
    phone = await _phone(house)
    await phone.request(1, {"op": "chat.list", "q": "search"})
    (kind, body), = await phone.answer(1)
    assert kind == wire.ERR and body["code"] == "bad_request"
    await phone.request(2, _send(text="x" * 32_001))
    assert (await phone.answer(2))[0][1]["code"] == "too_large"
    await phone.send(wire.PING)
    assert (await phone.recv()).kind == wire.PONG
    phone.close()


@pytest.mark.parametrize("plain", [
    struct.pack(">BIB", 0x09, 1, 0),                          # unknown kind
    struct.pack(">BIB", wire.REQ, 0, 0) + b"{}",              # req 0
    struct.pack(">BIB", wire.REQ, 1, 0x80) + b"{}",           # unknown flag
])
async def test_after_the_handshake_a_malformed_message_closes(house, plain: bytes) -> None:
    phone = await _phone(house)
    await phone.send_plain(plain)
    assert await phone.closed()
    await _events(house, "away_closed")


async def test_four_open_requests_then_busy(house) -> None:
    phone = await _phone(house)
    for req in range(1, 5):
        await phone.request(req, {"op": "chat.stream", "chat_id": req})
    await _events(house, n=4)
    await phone.request(5, {"op": "chat.stream", "chat_id": 5})
    assert (await phone.answer(5))[0][1]["code"] == "busy"
    phone.close()


async def test_a_req_still_open_cant_be_reused(house) -> None:
    phone = await _phone(house)
    await phone.request(1, {"op": "chat.stream", "chat_id": 1})
    await _events(house)
    await phone.request(1, {"op": "chat.stream", "chat_id": 1})
    assert await phone.closed()


async def test_sends_are_rate_limited_per_device_with_retry_after(house) -> None:
    phone = await _phone(house)
    for n in range(10):
        await phone.request(n + 1, _send(n))
        await _events(house, n=n + 1)
        _core(house, house.events[-1], "err", {"code": "internal", "detail": "test"})
        await phone.answer(n + 1)
    await phone.request(99, _send(99))
    (kind, body), = await phone.answer(99)
    assert kind == wire.ERR and body["code"] == "rate" and 1 <= body["retry_after"] <= 60
    phone.close()


def test_the_window_says_the_truth() -> None:
    w = al.Window(60.0, 2)
    w.take(0.0)
    w.take(10.0)
    assert w.wait(20.0) == pytest.approx(40.0)
    assert w.wait(61.0) == 0.0
    b = al.Window(3600.0, 100)
    assert b.wait(0.0, 101) == 3600.0  # never fits: the whole window


# ─── pictures ────────────────────────────────────────────────────────────


def _jpeg(size: int = 150_000) -> bytes:
    return (b"\xff\xd8\xff\xe0" + bytes(range(256)) * (size // 256 + 1))[:size]


def _pic(data: bytes, kind: str = "jpeg") -> dict:
    return {"type": kind, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


async def test_a_picture_is_reassembled_checked_and_handed_on(house) -> None:
    phone = await _phone(house)
    data = _jpeg()
    await phone.request(3, _send(text="", picture=_pic(data)))
    await phone.picture(3, data, piece=40_000)
    (event,) = await _events(house)
    assert base64.b64decode(event["picture"]) == data
    phone.close()


async def test_a_wrong_sha256_is_unsupported_picture(house) -> None:
    phone = await _phone(house)
    data = _jpeg(1000)
    await phone.request(3, _send(text="", picture=_pic(data) | {"sha256": "0" * 64}))
    await phone.picture(3, data)
    assert (await phone.answer(3))[0][1]["code"] == "unsupported_picture"
    assert not [e for e in house.events if e["event"] == "away"]
    phone.close()


async def test_a_png_said_to_be_a_jpeg_is_unsupported_picture(house) -> None:
    phone = await _phone(house)
    data = b"\x89PNG\r\n\x1a\n" + b"p" * 100
    await phone.request(3, _send(text="", picture=_pic(data)))
    await phone.picture(3, data)
    assert (await phone.answer(3))[0][1]["code"] == "unsupported_picture"
    phone.close()


async def test_a_piece_for_no_picture_closes(house) -> None:
    phone = await _phone(house)
    await phone.send(wire.BLOB, 4, wire.blob_body(0, b"x"))
    assert await phone.closed()


async def test_a_gap_in_the_pieces_closes(house) -> None:
    phone = await _phone(house)
    data = _jpeg(1000)
    await phone.request(3, _send(text="", picture=_pic(data)))
    await phone.send(wire.BLOB, 3, wire.blob_body(0, data[:100]))
    await phone.send(wire.BLOB, 3, wire.blob_body(200, data[200:300]))
    assert await phone.closed()


async def test_a_refused_requests_picture_is_swallowed(house) -> None:
    phone = await _phone(house)
    data = _jpeg(5000)
    await phone.request(3, _send(text="", picture=_pic(data), unknown=1))  # bad_request
    await phone.picture(3, data, piece=1000)
    assert (await phone.answer(3))[0][1]["code"] == "bad_request"
    await phone.send(wire.PING)
    assert (await phone.recv()).kind == wire.PONG  # still open: the pieces were read and dropped
    phone.close()


async def test_a_picture_that_doesnt_arrive_in_time(house, monkeypatch) -> None:
    monkeypatch.setattr(al, "PICTURE_DEADLINE_SEC", 0.2)
    phone = await _phone(house)
    await phone.request(3, _send(text="", picture=_pic(_jpeg(1000))))
    assert (await phone.answer(3))[0][1]["code"] == "bad_request"
    phone.close()


# ─── revocation, replacement, idle ───────────────────────────────────────


async def test_a_removed_phone_is_cut_off_with_bye_revoked(house) -> None:
    phone = await _phone(house)
    house.set_phones({})
    house.state.watch_once()  # the listener runs this every two seconds
    bye = await phone.recv()
    assert bye.kind == wire.HOUSE_BYE and bye.json() == {"code": "revoked"}
    assert await phone.closed()
    with pytest.raises(asyncio.IncompleteReadError):
        await house.phone(house.key).connect(house.port)


async def test_away_turned_off_says_bye_away_off(house) -> None:
    phone = await _phone(house)
    house.set_phones({_pub(house.key): "android-alex"}, on=False)
    house.state.watch_once()
    assert (await phone.recv()).json() == {"code": "away_off"}


async def test_a_frame_after_removal_is_answered_with_bye(house) -> None:
    phone = await _phone(house)
    house.set_phones({})
    await phone.send(wire.PING)
    assert (await phone.recv()).json() == {"code": "revoked"}


async def test_a_third_connection_replaces_the_oldest(house) -> None:
    first, second = await _phone(house), await _phone(house)
    third = await _phone(house)
    assert (await first.recv()).json() == {"code": "replaced"}
    assert await first.closed()
    for phone in (second, third):
        await phone.send(wire.PING)
        assert (await phone.recv()).kind == wire.PONG
        phone.close()


async def test_an_idle_connection_is_closed(house, monkeypatch) -> None:
    monkeypatch.setattr(al, "IDLE_SEC", 0.3)
    phone = await _phone(house)
    assert (await phone.recv(5)).json() == {"code": "idle"}
    assert await phone.closed()


async def test_core_learns_when_a_connection_ends(house) -> None:
    phone = await _phone(house)
    await phone.send(wire.BYE)
    (closed,) = await _events(house, "away_closed")
    assert closed["conn"].isdigit()


async def test_a_line_from_core_for_a_gone_connection_is_dropped(house) -> None:
    house.state.from_core(b'{"c": 999, "r": 1, "k": "resp", "b": {}}')
    house.state.from_core(b"not json")
    house.state.from_core(b'{"c": "1", "k": "resp"}')


def test_the_phone_list_reader_never_lets_everybody_in(tmp_path) -> None:
    path = tmp_path / al.PHONES_FILE
    for text in ('{"on": true, "phones": []}', '[]', '{"on": true}', '{"on": "yes", "phones": {}}'):
        path.write_text(text, encoding="utf-8")
        phones = al.read_phones(path)
        assert not phones.by_key and not (phones.on and phones.by_key)
    path.write_text(json.dumps({"on": True, "phones": {"ab": "x", "00" * 32: "android-1"}}), encoding="utf-8")
    assert al.read_phones(path).by_key == {bytes(32): "android-1"}


# ─── the core's relay (domovoi/away/bridge.py), DB-free ──────────────────


@pytest.fixture
def relay(monkeypatch):
    from domovoi.away import bridge
    from domovoi.friend_houses import service as svc

    sent: list[dict] = []
    started: list[dict] = []
    hold = asyncio.Event()

    async def to_listener(line: str) -> bool:
        assert line.startswith("away ")
        sent.append(json.loads(line[5:]))
        return True

    async def fake_relay(conn, req, payload):
        started.append({"conn": conn, "req": req, **payload})
        await hold.wait()

    monkeypatch.setattr(svc, "send_listener_line", to_listener)
    monkeypatch.setattr(bridge, "_relay", fake_relay)
    yield SimpleNamespace(sent=sent, started=started, hold=hold, bridge=bridge)
    bridge.stop_all()


def _event(conn: int = 1, req: int = 1, request: dict | None = None, **over) -> dict:
    ev = {"event": "away", "conn": str(conn), "device": "android-alex", "noise": "ab" * 32, "req": str(req),
          "request": json.dumps(request or {"op": "chat.list"}), "picture": ""}
    return ev | over


async def test_the_relay_checks_each_event_again(relay) -> None:
    relay.bridge.on_event(_event(device="../etc"))
    relay.bridge.on_event(_event(noise="zz"))
    relay.bridge.on_event(_event(req="0"))
    relay.bridge.on_event(_event(conn="x"))
    await asyncio.sleep(0.05)
    assert relay.started == [] and relay.sent == []
    relay.bridge.on_event(_event(request={"op": "chat.list", "extra": 1}))
    relay.bridge.on_event(_event(req=2, request=_send(1, picture={"type": "jpeg", "size": 3, "sha256": "a" * 64})))
    await asyncio.sleep(0.05)
    assert [s["b"]["code"] for s in relay.sent] == ["bad_request", "internal"]  # a picture said, none came
    assert relay.started == []


async def test_the_relay_is_bounded_and_a_closed_connection_cancels_its_own(relay) -> None:
    for req in range(1, 8):
        relay.bridge.on_event(_event(req=req))
    await asyncio.sleep(0.05)
    assert len(relay.started) == relay.bridge.MAX_RELAYS_PER_CONN
    assert [s["b"]["code"] for s in relay.sent] == ["unavailable"] * (7 - relay.bridge.MAX_RELAYS_PER_CONN)
    relay.bridge.on_event(_event(conn=2))
    await asyncio.sleep(0.05)
    assert len(relay.started) == relay.bridge.MAX_RELAYS_PER_CONN + 1
    relay.bridge.on_event({"event": "away_closed", "conn": "1"})
    await asyncio.sleep(0.05)
    assert 1 not in relay.bridge._RELAYS and 2 in relay.bridge._RELAYS
    assert relay.started[0]["request"] == {"op": "chat.list", "archived": False, "limit": 50, "cursor": None}


# ─── review fixes (2026-11-27) ───────────────────────────────────────────


def test_connection_ids_start_at_random_large_numbers_per_process(tmp_path) -> None:
    """H1: a listener that restarts never reuses the ids of the one before."""
    a, b = al.AwayState(tmp_path, None), al.AwayState(tmp_path, None)
    assert a._seq >= 1 << 48 and b._seq >= 1 << 48 and a._seq != b._seq
    assert a._seq < 1 << 63


async def test_a_stale_bye_or_answer_from_core_is_dropped(house) -> None:
    phone = await _phone(house)
    (conn,) = house.state.conns.values()
    # An answer or a bye for a request this connection doesn't have open.
    house.state.from_core(json.dumps({"c": conn.id, "r": 3, "k": "bye", "b": {"code": "revoked"}}).encode())
    house.state.from_core(json.dumps({"c": conn.id, "r": 3, "k": "resp", "b": {}, "end": True}).encode())
    await phone.send(wire.PING)
    assert (await phone.recv()).kind == wire.PONG          # still open, nothing else arrived
    # Core's own word (r 0) is a bye.
    house.state.from_core(json.dumps({"c": conn.id, "r": 0, "k": "bye", "b": {"code": "revoked"}}).encode())
    assert (await phone.recv()).json() == {"code": "revoked"}


async def test_a_listener_restart_cancels_the_replies_in_flight(relay, monkeypatch, tmp_path) -> None:
    """H1: when the listener process ends, every relay for its connections
    ends with it, so nothing can reach a connection of the next process."""
    from domovoi.friend_houses import service as svc
    from domovoi.friend_houses import tor_config as tc

    monkeypatch.setitem(svc._LISTENER, "proc", None)
    monkeypatch.setitem(svc._LISTENER, "task", None)
    monkeypatch.setitem(svc._LISTENER, "stopping", False)

    async def no_docker():
        svc._LISTENER["image"] = "sha256:test"

    monkeypatch.setattr(svc, "_prepare_listener", no_docker)
    monkeypatch.setattr(svc, "_listener_command", lambda folder: [sys.executable, "-c", "pass"])
    stopped = asyncio.Event()
    real_stop = svc._stop_away_relays

    def stop():
        real_stop()
        stopped.set()

    monkeypatch.setattr(svc, "_stop_away_relays", stop)
    conn = (1 << 48) + 7
    relay.bridge.on_event(_event(conn=conn, req=3))
    await asyncio.sleep(0.05)
    (in_flight,) = relay.bridge._RELAYS[conn]
    run = asyncio.get_running_loop().create_task(svc._run_listener(tc.TorFolder(tmp_path / "fh")))
    try:
        await asyncio.wait_for(stopped.wait(), 20)   # the process exited
        await asyncio.sleep(0.05)
        assert in_flight.cancelled() and conn not in relay.bridge._RELAYS
    finally:
        svc._LISTENER["stopping"] = True
        proc = svc._LISTENER["proc"]
        if proc is not None and proc.returncode is None:
            proc.kill()
        run.cancel()
        try:
            await run
        except (asyncio.CancelledError, Exception):  # noqa: BLE001 — torn down either way
            pass


async def test_stopping_the_listener_cancels_the_relays(relay, monkeypatch) -> None:
    from domovoi.friend_houses import service as svc

    monkeypatch.setitem(svc._LISTENER, "proc", None)
    monkeypatch.setitem(svc._LISTENER, "task", None)
    relay.bridge.on_event(_event(conn=9, req=1))
    await asyncio.sleep(0.05)
    await svc._stop_listener()
    await asyncio.sleep(0)
    assert not relay.bridge._RELAYS


async def test_a_refused_pictures_slot_isnt_open_and_ends_by_its_deadline(house, monkeypatch) -> None:
    """L1: a refused request's picture is read and dropped, never counted as
    an open request, and only for the picture deadline."""
    monkeypatch.setattr(al, "PICTURE_DEADLINE_SEC", 0.3)
    phone = await _phone(house)
    data = _jpeg(5000)
    await phone.request(1, _send(text="", picture=_pic(data), unknown=1))     # refused: bad_request
    assert (await phone.answer(1))[0][1]["code"] == "bad_request"
    for req in range(2, 6):                                                    # four open still fit
        await phone.request(req, {"op": "chat.stream", "chat_id": req})
    await _events(house, n=4)
    await asyncio.sleep(0.5)                                                    # the deadline passed
    await phone.send(wire.BLOB, 1, wire.blob_body(0, data[:100]))
    assert await phone.closed()                                                 # a piece for no picture


async def test_requests_in_pieces_are_capped_and_have_a_deadline(house, monkeypatch) -> None:
    """L7: at most four requests in pieces at once; one that never ends
    closes the connection."""
    phone = await _phone(house)
    for req in range(1, 5):
        await phone.send(wire.REQ, req, b'{"op":', more=True)
    await phone.send(wire.REQ, 5, b'{"op":', more=True)
    assert await phone.closed()
    monkeypatch.setattr(al, "PIECES_DEADLINE_SEC", 0.3)
    slow = await _phone(house)
    await slow.send(wire.REQ, 1, b'{"op":', more=True)
    assert await slow.closed()


async def test_a_phone_replaces_its_own_oldest_even_when_the_house_is_full(house, monkeypatch) -> None:
    monkeypatch.setattr(al, "MAX_CONNECTIONS", 2)
    first, second = await _phone(house), await _phone(house)
    third = await _phone(house)                    # every slot taken, but it's the same phone
    assert (await first.recv()).json() == {"code": "replaced"}
    await third.send(wire.PING)
    assert (await third.recv()).kind == wire.PONG
    other_key = X25519PrivateKey.generate()
    house.set_phones({_pub(house.key): "android-alex", _pub(other_key): "android-sam"})
    with pytest.raises(asyncio.IncompleteReadError):
        await house.phone(other_key).connect(house.port)   # another phone: the house is full
    second.close()


async def test_pictures_in_flight_have_a_house_wide_budget(house, monkeypatch) -> None:
    """M1: past the budget, a picture is ERR busy (and its pieces dropped)."""
    monkeypatch.setattr(al, "PICTURES_IN_FLIGHT_MAX", 3000)
    phone = await _phone(house)
    first, second = _jpeg(2000), _jpeg(2000)
    await phone.request(1, _send(1, text="", picture=_pic(first)))
    await phone.request(2, _send(2, text="", picture=_pic(second)))
    assert (await phone.answer(2))[0][1]["code"] == "busy"
    await phone.picture(2, second)                         # read and dropped
    await phone.picture(1, first)
    (event,) = await _events(house)
    assert base64.b64decode(event["picture"]) == first
    assert house.state._pictures == 0                       # given back once handed on
    phone.close()


def test_the_outbox_cap_is_smaller_now() -> None:
    assert al.OUTBOX_MAX_BYTES <= 2 * 1024 * 1024
    assert al.PICTURES_IN_FLIGHT_MAX <= 12 * 1024 * 1024


def test_the_phone_list_is_read_again_when_replaced_with_the_same_time_and_size(tmp_path) -> None:
    """L6: the cache is keyed on the inode too, not the mtime alone."""
    state = al.AwayState(tmp_path, None)
    path = tmp_path / al.PHONES_FILE
    path.write_text(json.dumps({"on": True, "phones": {"aa" * 32: "android-1"}}), encoding="utf-8")
    assert list(state.phones().by_key.values()) == ["android-1"]
    st = path.stat()
    new = tmp_path / "new.json"
    new.write_text(json.dumps({"on": True, "phones": {"bb" * 32: "android-2"}}), encoding="utf-8")
    os.utime(new, ns=(st.st_atime_ns, st.st_mtime_ns))
    os.replace(new, path)
    assert list(state.phones().by_key.values()) == ["android-2"]


# ─── away.leave (the phone turns away chat off for itself) ───────────────


async def _other(house):
    """A second household phone, on the list beside android-alex."""
    other = X25519PrivateKey.generate()
    house.set_phones({_pub(house.key): "android-alex", _pub(other): "android-sam"})
    phone = house.phone(other)
    await phone.connect(house.port)
    return phone


async def test_a_leave_goes_to_core_and_its_resp_closes_only_that_phone(house) -> None:
    first, second = await _phone(house), await _phone(house)
    other = await _other(house)
    await first.request(7, {"op": "away.leave"})
    (event,) = await _events(house)
    assert (event["device"], event["noise"], json.loads(event["request"])) == (
        "android-alex", _pub(house.key).hex(), {"op": "away.leave"})
    # Core has taken it off the list before it answers: the listener's
    # watch doesn't cut the leaving connection before the answer lands.
    house.set_phones({_pub(other.key): "android-sam"})
    house.state.watch_once()
    assert (await second.recv()).json() == {"code": "revoked"}   # not leaving: cut as any removal
    _core(house, event, "resp", {})
    assert await first.answer(7) == [(wire.RESP, {})]
    bye = await first.recv()
    assert (bye.kind, bye.json()) == (wire.HOUSE_BYE, {"code": "revoked"})
    assert await first.closed() and await second.closed()
    # Another phone stays.
    await other.send(wire.PING)
    assert (await other.recv()).kind == wire.PONG
    # Coming back after leaving is refused in silence.
    with pytest.raises(asyncio.IncompleteReadError):
        await house.phone(house.key).connect(house.port)
    other.close()


async def test_a_leave_core_refuses_leaves_the_connection_open(house) -> None:
    phone = await _phone(house)
    await phone.request(1, {"op": "away.leave"})
    (event,) = await _events(house)
    _core(house, event, "err", {"code": "unavailable", "detail": "Your house is busy. Try again shortly."})
    assert (await phone.answer(1))[0][1]["code"] == "unavailable"
    await phone.send(wire.PING)
    assert (await phone.recv()).kind == wire.PONG
    assert not house.state.conns[int(event["conn"])].leaving
    phone.close()


async def test_a_leave_naming_anything_but_its_op_is_refused(house) -> None:
    phone = await _phone(house)
    await phone.request(1, {"op": "away.leave", "device_id": "android-sam"})
    assert (await phone.answer(1))[0][1]["code"] == "bad_request"
    assert [e for e in house.events if e["event"] == "away"] == []
    phone.close()


async def test_leaves_have_a_rate_limit_of_their_own(house) -> None:
    phone = await _phone(house)
    for n in range(5):
        await phone.request(n + 1, {"op": "away.leave"})
        await _events(house, n=n + 1)
        _core(house, house.events[-1], "err", {"code": "unavailable", "detail": "test"})
        await phone.answer(n + 1)
    await phone.request(99, {"op": "away.leave"})
    (kind, body), = await phone.answer(99)
    assert kind == wire.ERR and body["code"] == "rate" and 1 <= body["retry_after"] <= 60
    await phone.request(100, {"op": "chat.list"})                 # other requests still go
    assert len(await _events(house, n=6)) == 6
    phone.close()


async def test_the_relay_answers_a_leave_itself_never_the_web(relay, monkeypatch) -> None:
    from domovoi.friend_houses import service as svc

    left, held = [], []

    async def leave(device, noise):
        held.append(any(t.get_name() == "away-chat-leave" for t in svc._PENDING))
        left.append((device, noise))
        return True

    monkeypatch.setattr(relay.bridge, "leave", leave)
    relay.bridge.on_event(_event(req=3, request={"op": "away.leave"}))
    await asyncio.sleep(0.05)
    assert relay.started == []                                       # not relayed to the web
    assert left == [("android-alex", "ab" * 32)]
    assert relay.sent == [{"c": 1, "r": 3, "k": "resp", "b": {}, "end": True}]
    assert held == [True]                                            # a strong reference while it runs


async def test_the_reconcile_after_a_leave_waits_and_leaves_close_together_share_it(monkeypatch) -> None:
    from domovoi.away import bridge, routes

    kicked: list[float] = []
    monkeypatch.setattr(routes, "kick", lambda: kicked.append(asyncio.get_running_loop().time()))
    monkeypatch.setattr(bridge, "LEAVE_KICK_DELAY_SEC", 0.3)
    monkeypatch.setitem(bridge._KICK_LATER, "handle", None)
    start = asyncio.get_running_loop().time()
    bridge._kick_later()
    await asyncio.sleep(0.15)
    bridge._kick_later()                                             # a second leave moves it on
    assert bridge._KICK_LATER["handle"] is not None                 # held, not dropped
    await asyncio.sleep(0.2)
    assert kicked == []
    await asyncio.sleep(0.3)
    assert len(kicked) == 1 and kicked[0] - start >= 0.44
    assert bridge._KICK_LATER["handle"] is None


async def test_a_leave_the_database_refuses_is_unavailable(relay, monkeypatch) -> None:
    async def leave(device, noise):
        raise OSError("database unavailable")

    monkeypatch.setattr(relay.bridge, "leave", leave)
    relay.bridge.on_event(_event(req=3, request={"op": "away.leave"}))
    await asyncio.sleep(0.05)
    assert [(s["k"], s["b"]["code"]) for s in relay.sent] == [("err", "unavailable")]


async def test_a_leave_closes_only_the_connections_with_its_key(house) -> None:
    """A leave sent on an old key's connection, racing the same device
    enrolling again with a new key, closes the old key's only."""
    new_key = X25519PrivateKey.generate()
    house.set_phones({_pub(house.key): "android-alex", _pub(new_key): "android-alex"})
    old = await _phone(house)
    new = house.phone(new_key)
    await new.connect(house.port)
    await old.request(1, {"op": "away.leave"})
    (event,) = await _events(house)
    house.set_phones({_pub(new_key): "android-alex"})               # the new key is the current one
    _core(house, event, "resp", {})
    assert await old.answer(1) == [(wire.RESP, {})]
    assert (await old.recv()).json() == {"code": "revoked"}
    house.state.watch_once()
    await new.send(wire.PING)
    assert (await new.recv()).kind == wire.PONG                     # the new key's connection stays
    new.close()


async def test_the_leaving_grace_runs_out(house, monkeypatch) -> None:
    """Should core never answer a leave, the connection doesn't outlive
    its removal by more than the grace."""
    monkeypatch.setattr(al, "LEAVING_GRACE_SEC", 0.3)
    phone = await _phone(house)
    await phone.request(1, {"op": "away.leave"})
    await _events(house)
    house.set_phones({})
    house.state.watch_once()
    await phone.send(wire.PING)
    assert (await phone.recv()).kind == wire.PONG                   # still within the grace
    await asyncio.sleep(0.4)
    house.state.watch_once()
    assert (await phone.recv()).json() == {"code": "revoked"}
    assert await phone.closed()
