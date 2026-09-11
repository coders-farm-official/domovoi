"""Satellite log pull — the `get_logs` → `logs_chunk` exchange.

The only request/response pair in an otherwise one-way protocol, so the
correlation, reassembly and failure modes are worth pinning down.

Deliberately DB-free (no `requires_db`): these must not silently skip on a
box without Postgres, because "green because it skipped" is how a broken
diagnostic ships.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from domovoi.streaming import StreamSession


# ─── Test doubles ──────────────────────────────────────────────────────────


class FakeWS:
    """Minimal WebSocket: captures sent text, never fails."""

    def __init__(self, app):
        self.app = app
        self.sent_text: list[str] = []

    async def send_text(self, t):
        self.sent_text.append(t)


class DeadWS(FakeWS):
    async def send_text(self, t):
        raise ConnectionError("socket is gone")


def make_app():
    return SimpleNamespace(state=SimpleNamespace(active_sessions={}))


def make_session(ws_cls=FakeWS):
    return StreamSession(ws_cls(make_app()), "office")


def chunk(rid, seq, data, final=False, stats=None):
    frame = {
        "type": "logs_chunk", "request_id": rid,
        "seq": seq, "data": data, "final": final,
    }
    if stats is not None:
        frame["stats"] = stats
    return frame


# ─── Happy path ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_request_logs_sends_get_logs_and_reassembles() -> None:
    s = make_session()
    task = asyncio.create_task(s.request_logs(max_bytes=4096))
    await asyncio.sleep(0)  # let the send land

    assert len(s.ws.sent_text) == 1
    sent = json.loads(s.ws.sent_text[0])
    assert sent["type"] == "get_logs"
    assert sent["max_bytes"] == 4096
    rid = sent["request_id"]
    assert rid  # a correlation id the satellite echoes back

    s._on_logs_chunk(chunk(rid, 0, "first\n"))
    s._on_logs_chunk(chunk(rid, 1, "second\n"))
    s._on_logs_chunk(chunk(rid, 2, "third\n", final=True, stats={"bytes": 99}))

    result = await asyncio.wait_for(task, timeout=1)
    assert result["text"] == "first\nsecond\nthird\n"
    assert result["stats"] == {"bytes": 99}
    assert result["chunks"] == 3


@pytest.mark.asyncio
async def test_single_final_chunk_resolves() -> None:
    s = make_session()
    task = asyncio.create_task(s.request_logs(max_bytes=1024))
    await asyncio.sleep(0)
    rid = json.loads(s.ws.sent_text[0])["request_id"]
    s._on_logs_chunk(chunk(rid, 0, "everything\n", final=True))
    assert (await asyncio.wait_for(task, timeout=1))["text"] == "everything\n"


@pytest.mark.asyncio
async def test_empty_log_still_resolves() -> None:
    """An empty ring must resolve, not hang until the timeout — a satellite
    that just booted has nothing to say and should say so immediately."""
    s = make_session()
    task = asyncio.create_task(s.request_logs(max_bytes=1024))
    await asyncio.sleep(0)
    rid = json.loads(s.ws.sent_text[0])["request_id"]
    s._on_logs_chunk(chunk(rid, 0, "", final=True, stats={"bytes": 0}))
    assert (await asyncio.wait_for(task, timeout=1))["text"] == ""


@pytest.mark.asyncio
async def test_request_is_deregistered_after_completion() -> None:
    s = make_session()
    task = asyncio.create_task(s.request_logs(max_bytes=1024))
    await asyncio.sleep(0)
    rid = json.loads(s.ws.sent_text[0])["request_id"]
    s._on_logs_chunk(chunk(rid, 0, "x\n", final=True))
    await asyncio.wait_for(task, timeout=1)
    assert s._log_requests == {}


# ─── Ordering + correlation ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_out_of_order_chunk_fails_the_request() -> None:
    """Stitching a misordered log would produce evidence you can't trust."""
    s = make_session()
    task = asyncio.create_task(s.request_logs(max_bytes=1024))
    await asyncio.sleep(0)
    rid = json.loads(s.ws.sent_text[0])["request_id"]

    s._on_logs_chunk(chunk(rid, 0, "first\n"))
    s._on_logs_chunk(chunk(rid, 2, "third\n", final=True))  # skipped seq 1

    with pytest.raises(RuntimeError, match="expected 1"):
        await asyncio.wait_for(task, timeout=1)


@pytest.mark.asyncio
async def test_duplicate_chunk_fails_the_request() -> None:
    s = make_session()
    task = asyncio.create_task(s.request_logs(max_bytes=1024))
    await asyncio.sleep(0)
    rid = json.loads(s.ws.sent_text[0])["request_id"]

    s._on_logs_chunk(chunk(rid, 0, "first\n"))
    s._on_logs_chunk(chunk(rid, 0, "first again\n"))

    with pytest.raises(RuntimeError):
        await asyncio.wait_for(task, timeout=1)


@pytest.mark.asyncio
async def test_chunk_for_unknown_request_is_ignored() -> None:
    """A late chunk from a timed-out pull must not crash the receive loop."""
    s = make_session()
    s._on_logs_chunk(chunk("no-such-request", 0, "orphan\n", final=True))
    assert s._log_requests == {}


@pytest.mark.asyncio
async def test_late_chunk_after_resolution_is_ignored() -> None:
    s = make_session()
    task = asyncio.create_task(s.request_logs(max_bytes=1024))
    await asyncio.sleep(0)
    rid = json.loads(s.ws.sent_text[0])["request_id"]
    s._on_logs_chunk(chunk(rid, 0, "done\n", final=True))
    await asyncio.wait_for(task, timeout=1)
    s._on_logs_chunk(chunk(rid, 1, "too late\n", final=True))  # must not raise


@pytest.mark.asyncio
async def test_concurrent_requests_do_not_cross_wires() -> None:
    s = make_session()
    t1 = asyncio.create_task(s.request_logs(max_bytes=1024))
    await asyncio.sleep(0)
    t2 = asyncio.create_task(s.request_logs(max_bytes=2048))
    await asyncio.sleep(0)
    rid1 = json.loads(s.ws.sent_text[0])["request_id"]
    rid2 = json.loads(s.ws.sent_text[1])["request_id"]
    assert rid1 != rid2

    s._on_logs_chunk(chunk(rid2, 0, "for-two\n", final=True))
    s._on_logs_chunk(chunk(rid1, 0, "for-one\n", final=True))

    assert (await asyncio.wait_for(t1, timeout=1))["text"] == "for-one\n"
    assert (await asyncio.wait_for(t2, timeout=1))["text"] == "for-two\n"


# ─── Failure modes ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dead_socket_raises_and_deregisters() -> None:
    s = make_session(DeadWS)
    with pytest.raises(ConnectionError):
        await s.request_logs(max_bytes=1024)
    assert s._log_requests == {}


@pytest.mark.asyncio
async def test_silent_satellite_times_out_and_deregisters() -> None:
    s = make_session()
    with pytest.raises(TimeoutError):
        await s.request_logs(max_bytes=1024, timeout=0.05)
    assert s._log_requests == {}


# ─── Cross-implementation contract ─────────────────────────────────────────
# The satellite builds these frames in `client._send_logs` and the core
# consumes them in `_on_logs_chunk`. Neither test above exercises both sides,
# and `client.py` can't even be imported off a Pi (it pulls in sounddevice at
# module scope) — so the split it uses, `log_buffer.chunk_text`, is imported
# here directly and the frame shape is reproduced verbatim. If the two ever
# drift apart, this is what catches it.


def _frames_as_the_satellite_builds_them(text, request_id, chunk_chars, stats):
    from satellite.log_buffer import chunk_text

    out = []
    for seq, piece, final in chunk_text(text, chunk_chars):
        frame = {
            "type": "logs_chunk", "request_id": request_id,
            "seq": seq, "data": piece, "final": final,
        }
        if final:
            frame["stats"] = stats
        out.append(frame)
    return out


@pytest.mark.asyncio
@pytest.mark.parametrize("chunk_chars", [1, 7, 512, 1_000_000])
async def test_satellite_frames_reassemble_in_the_core(chunk_chars: int) -> None:
    from satellite.log_buffer import RingLogBuffer

    ring = RingLogBuffer(64 * 1024)
    for i in range(200):
        ring.append(f"2026-09-10 21:35:5{i % 10},955 INFO satellite: line {i}")
    original = ring.tail()

    s = make_session()
    task = asyncio.create_task(s.request_logs(max_bytes=64 * 1024))
    await asyncio.sleep(0)
    rid = json.loads(s.ws.sent_text[0])["request_id"]

    for frame in _frames_as_the_satellite_builds_them(
        original, rid, chunk_chars, ring.stats()
    ):
        s._on_logs_chunk(frame)

    result = await asyncio.wait_for(task, timeout=2)
    assert result["text"] == original
    assert result["stats"]["bytes"] == ring.stats()["bytes"]


@pytest.mark.asyncio
async def test_an_empty_ring_round_trips() -> None:
    """The satellite still sends one final frame; the core still resolves."""
    from satellite.log_buffer import RingLogBuffer

    ring = RingLogBuffer(1024)
    s = make_session()
    task = asyncio.create_task(s.request_logs(max_bytes=1024))
    await asyncio.sleep(0)
    rid = json.loads(s.ws.sent_text[0])["request_id"]

    frames = _frames_as_the_satellite_builds_them(ring.tail(), rid, 4096, ring.stats())
    assert len(frames) == 1
    for frame in frames:
        s._on_logs_chunk(frame)

    result = await asyncio.wait_for(task, timeout=2)
    assert result["text"] == ""
    assert result["stats"]["lines"] == 0


@pytest.mark.asyncio
async def test_frames_survive_json_round_trip() -> None:
    """They cross a WebSocket as JSON text — multibyte and control
    characters have to come back identical, not merely similar."""
    from satellite.log_buffer import RingLogBuffer

    ring = RingLogBuffer(8192)
    ring.append('INFO satellite: heard: "what\'s the weather" — café 🎙')
    ring.append('WARNING satellite: tab\\there, "quotes" and back\\\\slash')
    original = ring.tail()

    s = make_session()
    task = asyncio.create_task(s.request_logs(max_bytes=8192))
    await asyncio.sleep(0)
    rid = json.loads(s.ws.sent_text[0])["request_id"]

    for frame in _frames_as_the_satellite_builds_them(original, rid, 16, ring.stats()):
        # json.loads(json.dumps(...)) is the wire, in miniature.
        s._on_logs_chunk(json.loads(json.dumps(frame)))

    assert (await asyncio.wait_for(task, timeout=2))["text"] == original
