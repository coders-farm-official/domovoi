"""``AwayLoopback``: a phone and the house's away listener over loopback.

The away chat test kit, as ``friend_houses/loopback.py`` is Friend
Houses': the real listener (:class:`domovoi.away.listener.AwayState`)
serves on ``127.0.0.1``, and :class:`Phone` speaks the wire to it the way
the apps do (preface, Noise IK with the phone's own key, PHONE_HELLO,
messages). No Tor, no Docker.

Wired to the house (``core=True``, the default): the listener's events go
to the core's real relay (:func:`domovoi.away.bridge.on_event`), the
relay's lines come back on the listener's "stdin"
(:meth:`AwayState.from_core`), and the relay reaches the web process's
real away bridge in this process (``web.backend.main.app`` over an ASGI
transport), so a request runs through the real chat code against the test
database. httpx's ASGI transport hands the relay a reply only once it has
all of it, so with ``web_socket=True`` the web app is served by uvicorn on
a real loopback socket (a free ``127.0.0.1`` port) instead, and the relay
reaches it over HTTP as it does in a house: replies stream. The caller's
test enrols the phone first (or writes ``phones.json`` itself with
:meth:`AwayLoopback.set_phones`).

Unwired (``core=False``): the listener's events are only recorded, for
listener-only tests.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import secrets
import struct
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from domovoi.away import listener as al
from domovoi.away import wire
from domovoi.friend_houses.noise import Handshake, Transport, public_bytes


class HouseMessage:
    def __init__(self, kind: int, req: int, more: bool, body: bytes) -> None:
        self.kind, self.req, self.more, self.body = kind, req, more, body

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8")) if self.body else None

    def __repr__(self) -> str:  # pragma: no cover — test output
        return f"HouseMessage(kind=0x{self.kind:02x}, req={self.req}, more={self.more}, {self.body[:80]!r})"


class Phone:
    """A phone's side of one connection."""

    def __init__(self, house_pub: bytes, key: X25519PrivateKey | None = None) -> None:
        self.key = key or X25519PrivateKey.generate()
        self.house_pub = house_pub
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.transport: Transport | None = None
        self.others: list[HouseMessage] = []

    @property
    def noise_pub(self) -> bytes:
        return public_bytes(self.key)

    async def connect(self, port: int, *, platform: int = 1, version: str = "1.5.0",
                      hello: bytes | None = None) -> tuple[int, int]:
        """Preface, handshake, hellos; returns the house's ``(proto,
        max_picture)``. Raises ``asyncio.IncompleteReadError`` when the
        house closes without a word."""
        self.reader, self.writer = await asyncio.open_connection("127.0.0.1", port)
        hs = Handshake("IK", initiator=True, prologue=wire.PROLOGUE, static=self.key,
                       remote_static=self.house_pub)
        payload = hello if hello is not None else wire.encode_phone_hello(platform, version)
        self.writer.write(wire.PREFACE + wire.frame(hs.write_message(payload)))
        await self.writer.drain()
        head = await asyncio.wait_for(self.reader.readexactly(2), 10)
        (size,) = struct.unpack(">H", head)
        msg2 = await asyncio.wait_for(self.reader.readexactly(size), 10)
        result = wire.parse_house_hello(hs.read_message(msg2))
        self.transport = hs.transport()
        return result

    async def send_plain(self, plain: bytes) -> None:
        assert self.writer is not None and self.transport is not None
        self.writer.write(wire.frame(self.transport.encrypt(plain)))
        await self.writer.drain()

    async def send(self, kind: int, req: int = 0, body: bytes = b"", *, more: bool = False) -> None:
        await self.send_plain(wire.encode_message(kind, req, body, more=more))

    async def request(self, req: int, obj: dict[str, Any] | bytes, *, piece: int | None = None) -> None:
        """One REQ, split with MORE into ``piece``-byte bodies (or as many
        as it needs)."""
        raw = obj if isinstance(obj, bytes) else json.dumps(obj).encode("utf-8")
        room = piece or (wire.MAX_PLAINTEXT - wire.HEADER_SIZE)
        parts = [raw[i:i + room] for i in range(0, len(raw), room)] or [b""]
        for i, part in enumerate(parts):
            await self.send(wire.REQ, req, part, more=i < len(parts) - 1)

    async def picture(self, req: int, data: bytes, *, piece: int = wire.MAX_BLOB_PIECE) -> None:
        for offset in range(0, len(data), piece):
            await self.send(wire.BLOB, req, wire.blob_body(offset, data[offset:offset + piece]))

    async def recv(self, timeout: float = 10.0) -> HouseMessage:
        assert self.reader is not None and self.transport is not None
        head = await asyncio.wait_for(self.reader.readexactly(2), timeout)
        (size,) = struct.unpack(">H", head)
        data = await asyncio.wait_for(self.reader.readexactly(size), timeout)
        plain = self.transport.decrypt(data)
        kind, req, flags = struct.unpack_from(">BIB", plain)
        return HouseMessage(kind, req, bool(flags & wire.FLAG_MORE), plain[wire.HEADER_SIZE:])

    async def answer(self, req: int, timeout: float = 30.0) -> list[tuple[int, Any]]:
        """Every message for ``req`` until the request ends: ``[(kind,
        json), ...]``, a split RESP joined up. Messages for other requests
        are kept in :attr:`others`."""
        out: list[tuple[int, Any]] = []
        parts: list[bytes] = []
        while True:
            m = await self.recv(timeout)
            if m.req != req:
                self.others.append(m)
                continue
            if m.kind == wire.RESP and m.more:
                parts.append(m.body)
                continue
            if m.kind == wire.RESP and parts:
                body = b"".join(parts) + m.body
                parts = []
                out.append((m.kind, json.loads(body)))
            else:
                out.append((m.kind, m.json()))
            if m.kind == wire.ERR or m.kind == wire.HOUSE_BYE:
                return out
            if m.kind == wire.EVENT and out[-1][1].get("type") in ("done", "error"):
                return out
            if m.kind == wire.RESP and not _streams(out[-1][1]):
                return out

    async def closed(self, timeout: float = 10.0) -> bool:
        """Whether the house closed the stream (reading on to its end)."""
        assert self.reader is not None
        try:
            while True:
                if not await asyncio.wait_for(self.reader.read(65536), timeout):
                    return True
        except (asyncio.TimeoutError, ConnectionError):
            return False

    def close(self) -> None:
        if self.writer is not None:
            self.writer.close()


def _streams(resp: Any) -> bool:
    """Whether a RESP is followed by events: a send's, or a stream's that
    found a reply running."""
    if not isinstance(resp, dict):
        return False
    return "user_message" in resp or resp.get("streaming") is True


class AwayLoopback:
    """``async with AwayLoopback(tmp_path) as loop:`` (see the module doc)."""

    def __init__(self, folder: Path, *, core: bool = True, house_key: X25519PrivateKey | None = None,
                 web_socket: bool = False) -> None:
        self.folder = Path(folder)
        self.web_socket = web_socket
        self._web: Any = None
        self._web_task: asyncio.Task | None = None
        self.web_port = 0
        self.folder.mkdir(parents=True, exist_ok=True)
        self.core = core
        self.house_key = house_key or X25519PrivateKey.generate()
        self.events: list[dict[str, Any]] = []
        self.state = al.AwayState(self.folder, self.house_key, emit=self._emit)
        self._server: asyncio.base_events.Server | None = None
        self._saved: dict[str, Any] = {}
        self._phones: list[Phone] = []
        self.port = 0

    @property
    def house_pub(self) -> bytes:
        return public_bytes(self.house_key)

    def phone(self, key: X25519PrivateKey | None = None) -> Phone:
        phone = Phone(self.house_pub, key)
        self._phones.append(phone)
        return phone

    def set_phones(self, phones: dict[bytes, str], *, on: bool = True) -> None:
        """Write the listener's ``phones.json`` (noise key -> device id)."""
        path = self.folder / al.PHONES_FILE
        path.write_text(json.dumps({"on": on, "phones": {k.hex(): v for k, v in phones.items()}}),
                        encoding="utf-8")
        st = path.stat()
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + secrets.randbelow(10**6) + 1_000_000))

    def _emit(self, event: str, **fields: str) -> None:
        record = {"event": event, **fields}
        self.events.append(record)
        if self.core:
            from domovoi.away import bridge

            bridge.on_event(record)

    async def __aenter__(self) -> "AwayLoopback":
        if self.core:
            if self.web_socket:
                await self._serve_web()
            self._wire_core()
        self._server = await asyncio.start_server(self.state.handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    def _wire_core(self) -> None:
        import httpx

        from domovoi.away import bridge
        from domovoi.friend_houses import service as svc
        from web.backend.main import app as web_app

        state = self.state

        async def to_listener(line: str) -> bool:
            if line.startswith("away "):
                state.from_core(line[5:].encode("ascii"))
            return True

        real_client = httpx.AsyncClient

        def client(**kw: Any) -> httpx.AsyncClient:
            kw.pop("trust_env", None)
            return real_client(transport=httpx.ASGITransport(app=web_app, client=("127.0.0.1", 40000)), **kw)

        self._saved = {"send": svc.send_listener_line, "client": httpx.AsyncClient,
                       "env": {k: os.environ.get(k) for k in ("WEB_HOST", "WEB_PORT")}}
        svc.send_listener_line = to_listener
        if self.web_socket:
            # The relay's own client (bridge.httpx_client None), to the
            # real socket: bridge.web_url() reads these.
            os.environ["WEB_HOST"], os.environ["WEB_PORT"] = "127.0.0.1", str(self.web_port)
        else:
            bridge.httpx_client = client

    async def _serve_web(self) -> None:
        """The web app on a real socket: uvicorn in this event loop (so it
        shares the test's database engine), on a port the OS picks."""
        import socket

        import uvicorn

        from web.backend.main import app as web_app

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        self.web_port = sock.getsockname()[1]
        config = uvicorn.Config(web_app, lifespan="off", log_level="warning", access_log=False,
                                timeout_graceful_shutdown=2)
        self._web = uvicorn.Server(config)
        # In a test's loop: Ctrl-C stays pytest's.
        self._web.capture_signals = contextlib.nullcontext  # type: ignore[method-assign]
        self._web_task = asyncio.get_running_loop().create_task(self._web.serve(sockets=[sock]),
                                                                name="away-loopback-web")
        for _ in range(500):
            if self._web.started or self._web_task.done():
                break
            await asyncio.sleep(0.01)
        if not self._web.started:
            raise RuntimeError("the web app didn't start on its loopback socket")

    async def __aexit__(self, *exc: object) -> None:
        from domovoi.away import bridge

        for phone in self._phones:
            phone.close()
        for conn in list(self.state.conns.values()):
            conn.close()
            conn.writer.close()
        if self._server is not None:
            self._server.close()
            try:
                await asyncio.wait_for(self._server.wait_closed(), 5)
            except asyncio.TimeoutError:
                pass
        bridge.stop_all()
        await asyncio.sleep(0)
        if self._saved:
            from domovoi.friend_houses import service as svc

            svc.send_listener_line = self._saved["send"]
            bridge.httpx_client = None
            for k, v in self._saved["env"].items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        if self._web is not None and self._web_task is not None:
            self._web.should_exit = True
            try:
                await asyncio.wait_for(self._web_task, 10)
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001 — closing anyway
                self._web_task.cancel()


__all__ = ["AwayLoopback", "HouseMessage", "Phone"]
