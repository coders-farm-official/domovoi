"""B1 — the dashboard's /ws/state hello frame carries the device token, and
the web process accepts the frame either way.

A browser WebSocket cannot set request headers, so the dashboard puts the
household token that its fetches send as ``X-Device-Token`` into the first
``/ws/state`` frame instead. The socket is on the daily tier today and the
field is ignored, which is the point of this test: an old client that sends
``{"subscribe": []}`` and a new one that also sends ``device_token`` must
both end up subscribed to everything.

DB-free (the broadcaster is a plain in-memory object), never ``requires_db``.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from web.backend.realtime import StateBroadcaster

REPO_ROOT = Path(__file__).resolve().parents[2]
MAIN = REPO_ROOT / "web" / "backend" / "main.py"
DATA_JS = REPO_ROOT / "web" / "static" / "data.js"


class _FakeWs:
    """Enough of a WebSocket for the broadcaster to push to."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, message: str) -> None:
        self.sent.append(message)


EVENTS = ("music.now_playing.changed", "satellites.presence.changed")


def _delivered(frame: dict) -> list[str]:
    """Hand `frame` to the /ws/state handling exactly as main.py does, then
    report which events this client is actually pushed."""
    broadcaster = StateBroadcaster()
    ws = _FakeWs()

    async def run() -> list[str]:
        await broadcaster.connect(ws)
        channels = frame.get("subscribe") if isinstance(frame, dict) else None
        if isinstance(channels, list):
            await broadcaster.set_subscriptions(
                ws, [str(c) for c in channels if isinstance(c, str)]
            )
        for event in EVENTS:
            await broadcaster.broadcast(event, {})
        return [json.loads(m)["type"] for m in ws.sent]

    return asyncio.run(run())


def test_a_frame_with_a_device_token_subscribes_exactly_like_one_without():
    plain = _delivered({"subscribe": []})
    with_token = _delivered({"subscribe": [], "device_token": "household-abc"})
    assert with_token == plain == list(EVENTS)        # empty = every channel

    named = _delivered({"subscribe": ["music"], "device_token": "household-abc"})
    assert named == ["music.now_playing.changed"]


def test_the_dashboard_sends_the_token_in_the_first_frame():
    src = DATA_JS.read_text(encoding="utf-8")
    opener = src[src.index("ws.addEventListener('open'"):src.index("ws.addEventListener('message'")]
    assert "hello.device_token = device" in opener
    assert "JSON.stringify(hello)" in opener
    # ...and only when this browser actually has one.
    assert re.search(r"if \(device\) hello\.device_token = device;", opener)


def test_the_ws_handler_documents_the_field():
    doc = MAIN.read_text(encoding="utf-8")
    handler = doc[doc.index('async def websocket_state'):doc.index('# ─── Static frontend')]
    assert "device_token" in handler
