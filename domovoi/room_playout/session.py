"""One speaker's ``/v1/room-audio/{satellite_id}`` socket (protocol §3-§7).

Auth is the device's pairing token, checked against its pairing row the
way ``StreamSession._validate_pairing`` does, minus the pairing and
parking: this socket never creates trust, it only reuses it. A hello
without a token is refused whatever the pairing mode.

Outgoing traffic goes through one sender task, so the streamer's
``send_audio`` never waits. Pongs jump the queue and are stamped (``t2``)
the moment they are written; ``t1`` is taken as soon as the ping's frame is
received, before it is parsed.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections import deque
from typing import Any, Awaitable, Callable

from starlette.websockets import WebSocket, WebSocketDisconnect

from domovoi.room_playout import protocol as P
from domovoi.room_playout.clock import now_us
from domovoi.room_playout.service import RoomPlayout

log = logging.getLogger(__name__)

HELLO_TIMEOUT_S = 10.0
MAX_QUEUE = 250            # 5 s of chunks: a client this far behind is gone
MAX_PINGS_PER_S = 50
MAX_PONGS = MAX_PINGS_PER_S  # unsent pongs kept; older ones are dropped
STATS_KEYS = ("offset_us", "rate_ppm", "rtt_us", "err_us", "underruns", "late",
              "corrections", "jumps")

Auth = Callable[[str, "str | None"], Awaitable[bool]]


async def pairing_auth(satellite_id: str, token: str | None) -> bool:
    from domovoi.admin_auth import token_sha256
    from domovoi.db.repositories import SatellitePairingRepository
    from domovoi.db.session import session_scope

    if not token:
        return False
    async with session_scope() as s:
        row = await SatellitePairingRepository(s).get_pairing(satellite_id)
    return row is not None and secrets.compare_digest(row[0], token_sha256(token))


class RoomAudioSession:
    def __init__(self, ws: WebSocket, satellite_id: str, playout: RoomPlayout, *,
                 auth: Auth = pairing_auth, clock: Callable[[], int] = now_us) -> None:
        self.ws = ws
        self.satellite_id = satellite_id
        self.playout = playout
        self.auth = auth
        self.clock = clock
        self.room_id: str | None = None
        self._out: deque[tuple[str, Any]] = deque()
        self._pongs: deque[tuple[Any, int]] = deque(maxlen=MAX_PONGS)
        self._wake = asyncio.Event()
        self._overflow = False
        self._ping_window = (0, 0)   # (second, count)

    # ── Sink ───────────────────────────────────────────────────────────
    def send_audio(self, frame: bytes) -> None:
        self._push(("b", frame))

    def send_control(self, msg: dict[str, Any]) -> None:
        self._push(("t", P.encode_msg(msg)))

    def _push(self, item: tuple[str, Any]) -> None:
        if len(self._out) >= MAX_QUEUE:
            self._overflow = True
        else:
            self._out.append(item)
        self._wake.set()

    # ── the socket ─────────────────────────────────────────────────────
    async def _refuse(self, reason: str) -> None:
        log.warning("room-audio: refused %s (%s)", self.satellite_id, reason)
        try:
            await self.ws.send_text(P.encode_msg(P.error_msg(reason)))
            await self.ws.close(code=1008)
        except Exception:  # noqa: BLE001 - peer may be gone
            pass

    async def _hello(self) -> dict[str, Any] | None:
        try:
            text = await asyncio.wait_for(self.ws.receive_text(), HELLO_TIMEOUT_S)
        except (asyncio.TimeoutError, KeyError, WebSocketDisconnect, RuntimeError):
            return None
        return P.decode_msg(text)

    async def run(self) -> None:
        await self.ws.accept()
        if not P.is_satellite_id(self.satellite_id):
            log.warning("room-audio: refused a malformed satellite id")
            self.satellite_id = "?"
            await self._refuse("bad_hello")
            return
        hello = await self._hello()
        if hello is None or hello.get("type") != "hello":
            await self._refuse("bad_hello")
            return
        if hello.get("proto") not in P.SUPPORTED:
            await self._refuse("proto_unsupported")
            return
        sid = hello.get("satellite_id", self.satellite_id)
        if sid != self.satellite_id:
            await self._refuse("bad_hello")
            return
        token = hello.get("pairing_token")
        token = token.strip() if isinstance(token, str) and token.strip() else None
        try:
            ok = await self.auth(self.satellite_id, token)
        except Exception:  # noqa: BLE001 - fail closed
            log.exception("room-audio: pairing check failed for %s", self.satellite_id)
            ok = False
        if not ok:
            await self._refuse("pairing_rejected")
            return
        if self.playout.registry.room_of(self.satellite_id) is None:
            await self._refuse("not_in_room")
            return
        room = self.playout.registry.room_of(self.satellite_id)
        self.send_control(P.ready_msg(self.satellite_id, room, self.playout.playout_ms,
                                      self.clock()))
        self.room_id = self.playout.attach(self.satellite_id, self)
        log.info("room-audio: %s joined room %s", self.satellite_id, self.room_id)
        sender = asyncio.create_task(self._send_loop())
        try:
            await self._recv_loop()
        finally:
            self.playout.detach(self.satellite_id, self)
            sender.cancel()
            try:
                await sender
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def _recv_loop(self) -> None:
        while True:
            msg = await self.ws.receive()
            t1 = self.clock()
            if msg.get("type") == "websocket.disconnect":
                return
            text = msg.get("text")
            if text is None:
                continue
            m = P.decode_msg(text)
            if m is None:
                continue
            kind = m["type"]
            if kind == "ping" and isinstance(m.get("t0"), (int, float)):
                sec = t1 // 1_000_000
                s, n = self._ping_window
                n = n + 1 if s == sec else 1
                self._ping_window = (sec, n)
                if n <= MAX_PINGS_PER_S:
                    self._pongs.append((m["t0"], t1))
                    self._wake.set()
            elif kind == "stats":
                self.playout.stats[self.satellite_id] = {
                    k: m[k] for k in STATS_KEYS if isinstance(m.get(k), (int, float))
                }

    async def _send_loop(self) -> None:
        try:
            while True:
                await self._wake.wait()
                self._wake.clear()
                while self._pongs or self._out:
                    if self._overflow:
                        log.warning("room-audio: %s too slow, closing", self.satellite_id)
                        await self.ws.close(code=1011)
                        return
                    if self._pongs:
                        t0, t1 = self._pongs.popleft()
                        await self.ws.send_text(P.encode_msg(
                            {"type": "pong", "t0": t0, "t1": t1, "t2": self.clock()}))
                        continue
                    kind, data = self._out.popleft()
                    if kind == "b":
                        await self.ws.send_bytes(data)
                    else:
                        await self.ws.send_text(data)
        except (WebSocketDisconnect, RuntimeError, ConnectionError):
            return
