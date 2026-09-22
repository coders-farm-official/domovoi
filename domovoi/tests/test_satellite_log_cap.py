"""The size cap on a satellite log pull (ADD-2).

``request_logs`` asks a Pi for at most ``max_bytes`` of its log ring and
reassembles the numbered ``logs_chunk`` frames that come back. Nothing
used to enforce the ask on the way home: the buffer grew with whatever
the session sent, for as long as the 60 s timeout allowed.

The cap belongs to the REQUEST, not to a frame — a rule per frame would
just mean more frames. DB-free: ``_LogRequest`` is a dataclass and
``_on_logs_chunk`` is pure bookkeeping over it.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from domovoi.streaming import (
    LOG_REASSEMBLY_FLOOR,
    LOG_REASSEMBLY_SLACK,
    StreamSession,
    _LogRequest,
)


class FakeWS:
    def __init__(self):
        self.app = SimpleNamespace(state=SimpleNamespace())
        self.sent_text: list[str] = []

    async def send_text(self, t):
        self.sent_text.append(t)


def _session_with_pull(max_chars: int):
    session = StreamSession(FakeWS(), "kitchen")
    pending = _LogRequest(
        future=asyncio.get_event_loop().create_future(), max_chars=max_chars
    )
    session._log_requests["rid"] = pending
    return session, pending


def _chunk(seq: int, data: str, final: bool = False) -> dict:
    return {"type": "logs_chunk", "request_id": "rid", "seq": seq,
            "data": data, "final": final}


async def test_chunks_within_the_cap_reassemble_normally():
    session, pending = _session_with_pull(100)
    session._on_logs_chunk(_chunk(0, "hello "))
    session._on_logs_chunk(_chunk(1, "world", final=True))
    assert (await pending.future)["text"] == "hello world"


async def test_a_pull_over_the_cap_fails_the_request():
    session, pending = _session_with_pull(100)
    session._on_logs_chunk(_chunk(0, "x" * 60))
    session._on_logs_chunk(_chunk(1, "x" * 60))
    with pytest.raises(RuntimeError, match="cap"):
        await pending.future


async def test_it_stops_accumulating_rather_than_just_failing():
    """Failing the future while holding megabytes would miss the point:
    the buffer is what the cap is about."""
    session, pending = _session_with_pull(100)
    session._on_logs_chunk(_chunk(0, "x" * 60))
    session._on_logs_chunk(_chunk(1, "x" * 60))
    assert pending.chunks == []
    assert pending.future.done()

    # Anything that keeps arriving after the refusal is ignored, not
    # appended to a buffer nobody is going to read.
    session._on_logs_chunk(_chunk(2, "x" * 1000))
    session._on_logs_chunk(_chunk(3, "x" * 1000, final=True))
    assert pending.chunks == []
    with pytest.raises(RuntimeError):
        await pending.future


async def test_the_last_chunk_that_would_cross_is_not_taken():
    session, pending = _session_with_pull(10)
    session._on_logs_chunk(_chunk(0, "12345"))
    assert pending.total_chars == 5
    session._on_logs_chunk(_chunk(1, "123456"))  # would make 11
    assert pending.total_chars == 5
    with pytest.raises(RuntimeError):
        await pending.future


async def test_exactly_at_the_cap_is_accepted():
    session, pending = _session_with_pull(10)
    session._on_logs_chunk(_chunk(0, "1234567890", final=True))
    assert (await pending.future)["text"] == "1234567890"


async def test_the_cap_is_derived_from_the_max_bytes_that_was_asked_for():
    """A pull for 10 MB and a pull for 1 KB must not share a ceiling."""
    ws = FakeWS()
    session = StreamSession(ws, "kitchen")

    async def grab(max_bytes: int) -> int:
        task = asyncio.create_task(session.request_logs(max_bytes=max_bytes,
                                                        timeout=0.2))
        await asyncio.sleep(0.05)
        caps = [p.max_chars for p in session._log_requests.values()]
        try:
            await task
        except (TimeoutError, asyncio.TimeoutError):
            pass
        return caps[0]

    big = await grab(10 * 1024 * 1024)
    small = await grab(1024)
    assert big == int(10 * 1024 * 1024 * LOG_REASSEMBLY_SLACK)
    # …with a floor, so a tiny ask still has room for JSON escaping.
    assert small == LOG_REASSEMBLY_FLOOR
    assert big > small


async def test_the_frame_still_asks_the_pi_for_the_requested_size():
    ws = FakeWS()
    session = StreamSession(ws, "kitchen")
    task = asyncio.create_task(session.request_logs(max_bytes=4096, timeout=0.2))
    await asyncio.sleep(0.05)
    frame = json.loads(ws.sent_text[0])
    assert frame["type"] == "get_logs"
    assert frame["max_bytes"] == 4096
    try:
        await task
    except (TimeoutError, asyncio.TimeoutError):
        pass


async def test_an_out_of_order_chunk_still_fails_the_request():
    """The ordering rule predates the cap and must survive it: a log
    whose ordering you cannot trust is worse than an error."""
    session, pending = _session_with_pull(1000)
    session._on_logs_chunk(_chunk(0, "a"))
    session._on_logs_chunk(_chunk(5, "b"))
    with pytest.raises(RuntimeError, match="expected"):
        await pending.future


async def test_the_admin_route_reports_an_over_cap_pull_as_502(monkeypatch):
    """The dashboard has to be able to tell the three failures apart:
    504 stopped answering, 503 left, 502 answered with something we could
    not use."""
    from httpx import ASGITransport, AsyncClient

    from domovoi.main import app as core_app
    from domovoi.tests.auth_testkit import install_fake_db

    install_fake_db(monkeypatch, admin=False)  # pre-setup LAN grace

    class Overrunning:
        room_id = "kitchen"

        async def request_logs(self, *, max_bytes, timeout=60.0):
            raise RuntimeError(
                f"satellite log exceeded the {max_bytes}-character cap"
            )

    monkeypatch.setattr(
        core_app.state, "active_sessions", {"kitchen": Overrunning()},
        raising=False,
    )
    transport = ASGITransport(app=core_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/v1/admin/satellite/kitchen/logs?max_bytes=4096")
    assert r.status_code == 502
    assert "cap" in r.text
