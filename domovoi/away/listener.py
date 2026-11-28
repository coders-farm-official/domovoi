"""Away chat's socket in the Friend Houses listener: ``away.sock``.

Away chat (1.5.0) lets a household member's paired phone chat with its own
house from anywhere, through Tor's ``hs-away`` onion. Tor hands each
connection to ``away.sock``, served by the same sandboxed listener process
as the friends' sockets (:mod:`domovoi.friend_houses.listener`: no network,
no database, a read-only root), with a key set of its own:

* ``phones.json`` in the listener's folder, written by core on every
  change: ``{"on": bool, "phones": {"<noise key hex>": "<device id>"}}``.
  It is re-read (when it changed) before each handshake, on every frame,
  and every :data:`WATCH_EVERY_SEC` seconds, so a phone removed, or away
  chat turned off, is refused at its next handshake and cut off within
  seconds (``BYE revoked`` / ``away_off``), whatever Tor still has cached.
  An unreadable file means nobody gets in;
* the house's away Noise key (``--away-key``), never the Friend Houses
  session key.

It refuses in the cheapest order, and silently (a stranger who somehow has
the address learns nothing): the slots, the preface, the frame size, the
house-wide handshake budget, the Noise handshake, "is this a current
phone?", then the hello's shape. After that every message goes through
:mod:`domovoi.away.wire` (header, strict JSON, the closed request set,
picture reassembly with its sha256) and the per-device limits
(:data:`LIMITS`). A request that survives is handed to core as one JSON
line on stdout (``away``); core runs it through the house's own chat code
and answers on stdin (``away {...}`` lines, :meth:`AwayState.from_core`),
which this module frames back to the phone. ``away.leave`` (the phone
turning away chat off for itself) is answered by core, which takes the
phone off ``phones.json`` first; the RESP then closes every connection the
phone has (``BYE revoked``), and its next handshake is refused.

Nothing from a message's text or a picture is logged here.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import secrets
import sys
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from domovoi.away import wire
from domovoi.friend_houses.noise import Handshake, NoiseError, Transport

log = logging.getLogger("domovoi.away.listener")

PHONES_FILE = "phones.json"

MAX_CONNECTIONS = 16        # authenticated, over all phones
MAX_PER_DEVICE = 2          # a third closes the oldest (BYE replaced)
MAX_HANDSHAKING = 8         # before the handshake; the oldest makes room
HANDSHAKES_PER_MINUTE = 60  # house-wide; over it: silence
HANDSHAKE_DEADLINE_SEC = 30.0
IDLE_SEC = 120.0
PICTURE_DEADLINE_SEC = 120.0  # a picture's pieces, and a refused one's, from its REQ
PIECES_DEADLINE_SEC = 120.0   # a REQ arriving in pieces: closed past it
OPEN_REQUESTS = 4           # per connection: ERR busy
PINGS_PER_MINUTE = 30       # per connection; more is closed
MAX_DISCARDING = 4          # refused requests whose pictures are still arriving
WATCH_EVERY_SEC = 2.0
OUTBOX_MAX_BYTES = 1536 * 1024  # a phone that stops reading is closed
#: Picture bytes being received across the house (each picture's declared
#: size, held from its REQ until it is handed on or dropped): over it, a
#: picture is answered ERR busy. Keeps the container under its memory cap.
PICTURES_IN_FLIGHT_MAX = 12 * 1024 * 1024

#: Per device, across its connections: (window seconds, limit).
LIMITS = {
    "send_minute": (60.0, 10),
    "send_hour": (3600.0, 200),
    "other_minute": (60.0, 60),
    "picture_hour": (3600.0, 30),
    # away.leave also counts as an "other" request; a leave that worked
    # ends the phone's away chat, so more than a few is a phone retrying.
    "leave_minute": (60.0, 5),
}
PICTURE_BYTES_PER_HOUR = 20_000_000

BYE_CODES = frozenset({"shutting_down", "away_off", "revoked", "idle", "replaced"})
_KINDS = {"resp": wire.RESP, "event": wire.EVENT, "err": wire.ERR}


def emit(event: str, **fields: str) -> None:
    """One event for core, on stdout (the listener's channel)."""
    sys.stdout.write(json.dumps({"event": event, **fields}, separators=(",", ":")) + "\n")
    sys.stdout.flush()


class Window:
    """A sliding-window budget: :meth:`wait` says how long until one more
    (or ``n`` more units) fits, :meth:`take` spends it."""

    def __init__(self, window: float, limit: int) -> None:
        self.window = window
        self.limit = limit
        self.items: deque[tuple[float, int]] = deque()
        self.total = 0

    def _trim(self, now: float) -> None:
        while self.items and now - self.items[0][0] >= self.window:
            self.total -= self.items.popleft()[1]

    def wait(self, now: float, n: int = 1) -> float:
        self._trim(now)
        if self.total + n <= self.limit:
            return 0.0
        need = self.total + n - self.limit
        freed = 0
        for at, units in self.items:
            freed += units
            if freed >= need:
                return max(0.0, at + self.window - now)
        return self.window  # n alone is over the limit

    def take(self, now: float, n: int = 1) -> None:
        self.items.append((now, n))
        self.total += n


@dataclass
class Device:
    """One phone's budgets, across all its connections."""

    windows: dict[str, Window] = field(
        default_factory=lambda: {k: Window(*v) for k, v in LIMITS.items()})
    picture_bytes: Window = field(default_factory=lambda: Window(3600.0, PICTURE_BYTES_PER_HOUR))
    conns: list[Conn] = field(default_factory=list)

    def charge(self, request: wire.Request, now: float) -> int:
        """0 and spent, or the seconds to wait (nothing spent)."""
        names = ["send_minute", "send_hour"] if request.op == "chat.send" else ["other_minute"]
        if request.op == wire.LEAVE:
            names.append("leave_minute")
        if request.picture is not None:
            names.append("picture_hour")
        wait = max(self.windows[n].wait(now) for n in names)
        if request.picture is not None:
            wait = max(wait, self.picture_bytes.wait(now, request.picture.size))
        if wait > 0:
            return max(1, int(wait + 0.999))
        for n in names:
            self.windows[n].take(now)
        if request.picture is not None:
            self.picture_bytes.take(now, request.picture.size)
        return 0


@dataclass
class Phones:
    on: bool = False
    by_key: dict[bytes, str] = field(default_factory=dict)


def read_phones(path: Path) -> Phones:
    """``phones.json``; anything unreadable is nobody, never everybody."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or not isinstance(doc.get("phones"), dict):
            raise ValueError("not a phone list")
        by_key: dict[bytes, str] = {}
        for key_hex, device in doc["phones"].items():
            key = bytes.fromhex(key_hex)
            if len(key) == 32 and isinstance(device, str) and 0 < len(device) <= 64:
                by_key[key] = device
        return Phones(doc.get("on") is True, by_key)
    except FileNotFoundError:
        return Phones()
    except (OSError, ValueError, TypeError, AttributeError, RecursionError) as e:
        log.error("the away phone list can't be read (%s); refusing every phone", type(e).__name__)
        return Phones()


def load_key(path: Path) -> Any:
    """The house's away Noise key as mounted, or None."""
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

    try:
        raw = path.read_bytes()
    except OSError:
        return None
    return X25519PrivateKey.from_private_bytes(raw) if len(raw) == 32 else None


@dataclass
class Conn:
    """One authenticated phone connection."""

    id: int
    device: str
    noise: bytes
    writer: asyncio.StreamWriter
    transport: Transport
    outbox: asyncio.Queue = field(default_factory=asyncio.Queue)
    queued: int = 0
    closed: bool = False
    assembling: dict[int, tuple[wire.RequestPieces, asyncio.TimerHandle]] = field(default_factory=dict)
    awaiting: dict[int, tuple[wire.Request, wire.PictureSink, asyncio.TimerHandle]] = field(default_factory=dict)
    # A refused request's picture still arriving: read and dropped, never
    # counted as open, for at most PICTURE_DEADLINE_SEC.
    discarding: dict[int, tuple[wire.PictureSink, asyncio.TimerHandle]] = field(default_factory=dict)
    forwarded: set[int] = field(default_factory=set)
    # The away.leave requests among ``forwarded``: core's RESP to one ends
    # every connection of this phone (it no longer has away chat).
    leaving: set[int] = field(default_factory=set)
    pings: Window = field(default_factory=lambda: Window(60.0, PINGS_PER_MINUTE))

    @property
    def open(self) -> int:
        return len(self.awaiting) + len(self.forwarded)

    def in_use(self, req: int) -> bool:
        return req in self.awaiting or req in self.forwarded or req in self.discarding

    def push(self, plain: bytes) -> bool:
        """Encrypt and queue one message (in order: encryption happens
        here, writing in :meth:`AwayState._write_loop`). False when the
        phone has stopped reading and the connection was closed."""
        if self.closed:
            return False
        data = wire.frame(self.transport.encrypt(plain))
        self.queued += len(data)
        if self.queued > OUTBOX_MAX_BYTES:
            self.close()
            return False
        self.outbox.put_nowait(data)
        return True

    def close(self) -> None:
        for *_, handle in (*self.awaiting.values(), *self.discarding.values(), *self.assembling.values()):
            handle.cancel()
        if not self.closed:
            self.closed = True
            self.outbox.put_nowait(None)


@dataclass
class AwayState:
    folder: Path
    key: Any  # X25519PrivateKey, or None: every handshake fails
    emit: Callable[..., None] = emit
    _phones: Phones = field(default_factory=Phones)
    _stamp: tuple[int, int, int] | None = None
    # Connection ids start at a random 48- to 63-bit number in each listener
    # process, so an answer core still holds for a connection of an earlier
    # process can never name one of this process's (review H1).
    _seq: int = field(default_factory=lambda: (1 << 48) + secrets.randbelow((1 << 63) - (1 << 48)))
    _pictures: int = 0  # picture bytes in flight, house-wide
    _sockets: int = 0
    _handshaking: deque = field(default_factory=deque)
    _handshakes: Window = field(default_factory=lambda: Window(60.0, HANDSHAKES_PER_MINUTE))
    conns: dict[int, Conn] = field(default_factory=dict)
    devices: dict[str, Device] = field(default_factory=dict)

    # ── the phone list ──────────────────────────────────────────────────

    def phones(self) -> Phones:
        """``phones.json``, read again whenever its inode, time or size
        changed (core replaces it atomically)."""
        path = self.folder / PHONES_FILE
        try:
            st = path.stat()
        except OSError:
            self._phones, self._stamp = Phones(), None
            return self._phones
        stamp = (st.st_ino, st.st_mtime_ns, st.st_size)
        if stamp != self._stamp:
            self._phones, self._stamp = read_phones(path), stamp
        return self._phones

    def still_admitted(self, conn: Conn) -> str | None:
        """None while the phone may stay, else the BYE code."""
        phones = self.phones()
        if not phones.on:
            return "away_off"
        if phones.by_key.get(conn.noise) != conn.device:
            # A phone leaving (away.leave) is off the list before core's
            # answer arrives: that answer goes out first, then BYE revoked
            # (:meth:`left`). Core always ends the request, RESP or ERR.
            return None if conn.leaving else "revoked"
        return None

    def bye(self, conn: Conn, code: str) -> None:
        if code in BYE_CODES and not conn.closed:
            conn.push(wire.encode_message(wire.HOUSE_BYE, 0, wire.dump({"code": code})))
        conn.close()

    def watch_once(self) -> None:
        for conn in list(self.conns.values()):
            why = self.still_admitted(conn)
            if why is not None:
                self.bye(conn, why)

    async def watch(self) -> None:  # pragma: no cover — a loop around watch_once
        while True:
            await asyncio.sleep(WATCH_EVERY_SEC)
            try:
                self.watch_once()
            except Exception:  # noqa: BLE001 — the watch never stops
                log.exception("away: the phone check failed")

    def shutdown(self) -> None:
        for conn in list(self.conns.values()):
            self.bye(conn, "shutting_down")

    # ── core's answers ──────────────────────────────────────────────────

    def from_core(self, raw: bytes) -> None:
        """One ``away`` line from core (after the word): ``{"c": conn,
        "r": req, "k": "resp"|"event"|"err"|"bye", "b": {...}, "end": bool}``.
        Core is trusted, but a line that doesn't fit is dropped all the same."""
        try:
            line = json.loads(raw.decode("utf-8"))
            c, r, k, body = line["c"], line.get("r", 0), line["k"], line.get("b", {})
            end = line.get("end") is True
        except (ValueError, KeyError, TypeError, AttributeError, UnicodeDecodeError, RecursionError):
            return
        if not isinstance(c, int) or not isinstance(r, int) or not isinstance(body, dict):
            return
        conn = self.conns.get(c)
        if conn is None or conn.closed:
            return
        if k == "bye":
            # For a request this connection has open, or r 0: core's own word.
            if r == 0 or r in conn.forwarded:
                self.bye(conn, str(body.get("code", "")))
            return
        kind = _KINDS.get(k)
        if kind is None or r not in conn.forwarded:
            return
        data = wire.dump(body)
        if (kind == wire.RESP and len(data) > wire.MAX_RESP) or (
                kind != wire.RESP and len(data) > wire.MAX_PLAINTEXT - wire.HEADER_SIZE):
            kind, end = wire.ERR, True
            data = wire.dump({"code": "internal", "detail": "the answer was too large to send"})
        for message in wire.split_json(kind, r, data):
            if not conn.push(message):
                return
        if end or kind == wire.ERR:
            conn.forwarded.discard(r)
            if r in conn.leaving:
                conn.leaving.discard(r)
                if kind == wire.RESP:
                    self.left(conn.device)

    def left(self, device: str) -> None:
        """The phone turned away chat off for itself (``away.leave``):
        core has already taken it off ``phones.json``, so its next
        handshake is refused; every connection it has open is closed now,
        each after what was queued for it (the leave's RESP first)."""
        dev = self.devices.get(device)
        for conn in list(dev.conns if dev is not None else ()):
            self.bye(conn, "revoked")

    # ── one connection ──────────────────────────────────────────────────

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """One connection on away.sock, start to finish. Never raises."""
        if not _admit(self._sockets, MAX_CONNECTIONS + MAX_HANDSHAKING, self._handshaking, MAX_HANDSHAKING):
            writer.close()
            return
        self._sockets += 1
        self._handshaking.append(writer)
        conn: Conn | None = None
        writer_task: asyncio.Task | None = None
        try:
            conn = await asyncio.wait_for(self._handshake(reader, writer), HANDSHAKE_DEADLINE_SEC)
            _done_waiting(self._handshaking, writer)
            if conn is None:
                return
            writer_task = asyncio.get_running_loop().create_task(self._write_loop(conn))
            await self._serve(reader, conn)
        except (wire.ProtocolError, NoiseError, asyncio.TimeoutError, asyncio.IncompleteReadError,
                ConnectionError) as e:
            log.info("away: connection closed (%s)", type(e).__name__)
        except Exception:  # noqa: BLE001 — one bad connection never stops the listener
            log.exception("away: connection failed")
        finally:
            self._sockets -= 1
            _done_waiting(self._handshaking, writer)
            if conn is not None:
                conn.close()
                for req in list(conn.awaiting):
                    self._drop_picture(conn, req)
                self.conns.pop(conn.id, None)
                dev = self.devices.get(conn.device)
                if dev is not None and conn in dev.conns:
                    dev.conns.remove(conn)
                self.emit("away_closed", conn=str(conn.id))
            if writer_task is not None:
                try:
                    await asyncio.wait_for(writer_task, 5.0)
                except (asyncio.TimeoutError, Exception):  # noqa: BLE001 — closing anyway
                    writer_task.cancel()
            writer.close()

    async def _handshake(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> Conn | None:
        if await reader.readexactly(len(wire.PREFACE)) != wire.PREFACE:
            raise wire.ProtocolError("unknown preface")
        size = wire.frame_size(await reader.readexactly(2), max_size=wire.MSG1_MAX, min_size=wire.MSG1_MIN)
        msg1 = await reader.readexactly(size)
        now = time.monotonic()
        if self._handshakes.wait(now) > 0:
            raise wire.ProtocolError("handshake budget spent")
        self._handshakes.take(now)
        phones = self.phones()
        if not phones.on or self.key is None:
            raise wire.ProtocolError("away chat is off")
        hs = Handshake("IK", initiator=False, prologue=wire.PROLOGUE, static=self.key)
        payload = hs.read_message(msg1)
        caller = hs.remote_static
        device = phones.by_key.get(caller) if caller is not None else None
        if device is None:
            raise wire.ProtocolError("not a current phone")
        wire.parse_phone_hello(payload)
        # A phone already holding its two connections replaces its own
        # oldest, so it gets in even when every slot is taken.
        known = self.devices.get(device)
        replacing = known is not None and len(known.conns) >= MAX_PER_DEVICE
        if len(self.conns) >= MAX_CONNECTIONS and not replacing:
            raise wire.ProtocolError("every phone slot is taken")
        writer.write(wire.frame(hs.write_message(wire.HOUSE_HELLO)))
        await writer.drain()
        self._seq += 1
        assert caller is not None
        conn = Conn(self._seq, device, caller, writer, hs.transport())
        dev = self.devices.setdefault(device, Device())
        while len(dev.conns) >= MAX_PER_DEVICE:
            self.bye(dev.conns.pop(0), "replaced")
        dev.conns.append(conn)
        self.conns[conn.id] = conn
        return conn

    async def _write_loop(self, conn: Conn) -> None:
        """Writes what :meth:`Conn.push` queued, in order. A closed
        connection's last messages (its BYE) go out first, then the socket
        closes, which also ends its reader."""
        try:
            while True:
                data = await conn.outbox.get()
                if data is None:
                    break
                conn.queued -= len(data)
                conn.writer.write(data)
                await asyncio.wait_for(conn.writer.drain(), IDLE_SEC)
        finally:
            conn.closed = True
            conn.writer.close()

    async def _serve(self, reader: asyncio.StreamReader, conn: Conn) -> None:
        while not conn.closed:
            try:
                head = await asyncio.wait_for(reader.readexactly(2), IDLE_SEC)
            except asyncio.TimeoutError:
                self.bye(conn, "idle")
                return
            size = wire.frame_size(head)
            data = await asyncio.wait_for(reader.readexactly(size), IDLE_SEC)
            msg = wire.parse_message(conn.transport.decrypt(data))
            why = self.still_admitted(conn)
            if why is not None:
                self.bye(conn, why)
                return
            if not self.on_message(conn, msg):
                return

    def on_message(self, conn: Conn, msg: wire.Message) -> bool:
        """One phone message; False when the connection ends. Protocol
        errors raise :class:`wire.ProtocolError`."""
        if msg.kind == wire.PING:
            now = time.monotonic()
            if conn.pings.wait(now) > 0:
                raise wire.ProtocolError("too many pings")
            conn.pings.take(now)
            return conn.push(wire.encode_message(wire.PONG, 0))
        if msg.kind == wire.BYE:
            conn.close()
            return False
        if msg.kind == wire.BLOB:
            return self._on_blob(conn, msg)
        return self._on_req(conn, msg)

    def _err(self, conn: Conn, req: int, code: str, detail: str, retry_after: int | None = None) -> bool:
        body: dict[str, Any] = {"code": code, "detail": detail}
        if retry_after is not None:
            body["retry_after"] = retry_after
        return conn.push(wire.encode_message(wire.ERR, req, wire.dump(body)))

    def _on_req(self, conn: Conn, msg: wire.Message) -> bool:
        assembling = conn.assembling.get(msg.req)
        if assembling is None:
            if conn.in_use(msg.req) or msg.req in conn.discarding:
                raise wire.ProtocolError("req already open")
            if msg.more:
                if len(conn.assembling) >= OPEN_REQUESTS:
                    raise wire.ProtocolError("too many requests in pieces")
                handle = asyncio.get_running_loop().call_later(
                    PIECES_DEADLINE_SEC, self._pieces_late, conn, msg.req)
                assembling = conn.assembling[msg.req] = (wire.RequestPieces(), handle)
            else:
                assembling = (wire.RequestPieces(), None)
        pieces, handle = assembling
        pieces.add(msg.body)
        if msg.more:
            return True
        conn.assembling.pop(msg.req, None)
        if handle is not None:
            handle.cancel()
        picture: wire.Picture | None = None
        try:
            request = pieces.finish()
            picture = request.picture
            if conn.open >= OPEN_REQUESTS:
                raise wire.BadRequest("busy", "this connection has four requests open")
            if picture is not None and self._pictures + picture.size > PICTURES_IN_FLIGHT_MAX:
                raise wire.BadRequest("busy", "the house is receiving too many pictures; try again shortly")
            wait = self._device(conn).charge(request, time.monotonic())
            if wait:
                return self._refuse(conn, msg.req, picture, "rate", "too many requests; wait a little",
                                    retry_after=wait)
        except wire.BadRequest as e:
            if picture is None and not pieces.over:
                picture = _declared_picture(pieces)
            return self._refuse(conn, msg.req, picture, e.code, e.detail)
        if picture is None:
            self._forward(conn, msg.req, request, None)
            return True
        handle = asyncio.get_running_loop().call_later(
            PICTURE_DEADLINE_SEC, self._picture_late, conn, msg.req)
        conn.awaiting[msg.req] = (request, wire.PictureSink(picture), handle)
        self._pictures += picture.size
        return True

    def _pieces_late(self, conn: Conn, req: int) -> None:
        """A REQ still in pieces past its deadline: the connection closes."""
        if conn.assembling.pop(req, None) is not None and not conn.closed:
            log.info("away: a request in pieces didn't finish in time; closing")
            conn.close()

    def _refuse(self, conn: Conn, req: int, picture: wire.Picture | None, code: str, detail: str,
                retry_after: int | None = None) -> bool:
        """An ERR for a request; its picture, if one is on its way, is
        read and dropped for up to :data:`PICTURE_DEADLINE_SEC` (not an
        open request; at most :data:`MAX_DISCARDING` at once)."""
        if picture is not None:
            if len(conn.discarding) >= MAX_DISCARDING:
                raise wire.ProtocolError("too many refused pictures in flight")
            handle = asyncio.get_running_loop().call_later(
                PICTURE_DEADLINE_SEC, self._discard_late, conn, req)
            conn.discarding[req] = (wire.PictureSink(picture, keep=False), handle)
        return self._err(conn, req, code, detail, retry_after)

    def _discard_late(self, conn: Conn, req: int) -> None:
        # Pieces after this are pieces for no picture: the connection closes.
        conn.discarding.pop(req, None)

    def _drop_picture(self, conn: Conn, req: int) -> tuple | None:
        """Forget a picture being received and give its bytes back to the
        house-wide budget."""
        waiting = conn.awaiting.pop(req, None)
        if waiting is not None:
            waiting[2].cancel()
            self._pictures = max(0, self._pictures - waiting[1].picture.size)
        return waiting

    def _picture_late(self, conn: Conn, req: int) -> None:
        if self._drop_picture(conn, req) is not None and not conn.closed:
            self._err(conn, req, "bad_request", "the picture didn't arrive within 120 seconds")

    def _on_blob(self, conn: Conn, msg: wire.Message) -> bool:
        offset, data = wire.parse_blob(msg.body)
        dropping = conn.discarding.get(msg.req)
        if dropping is not None:
            if dropping[0].add(offset, data):
                dropping[1].cancel()
                del conn.discarding[msg.req]
            return True
        waiting = conn.awaiting.get(msg.req)
        if waiting is None:
            raise wire.ProtocolError("a picture piece for no picture")
        request, sink, handle = waiting
        if not sink.add(offset, data):
            return True
        self._drop_picture(conn, msg.req)
        try:
            picture = sink.finish()
        except wire.BadRequest as e:
            return self._err(conn, msg.req, e.code, e.detail)
        self._forward(conn, msg.req, request, picture)
        return True

    def _device(self, conn: Conn) -> Device:
        return self.devices.setdefault(conn.device, Device())

    def _forward(self, conn: Conn, req: int, request: wire.Request, picture: bytes | None) -> None:
        conn.forwarded.add(req)
        if request.op == wire.LEAVE:
            conn.leaving.add(req)
        self.emit("away", conn=str(conn.id), device=conn.device, noise=conn.noise.hex(), req=str(req),
                  request=json.dumps(request.to_json(), separators=(",", ":")),
                  picture=base64.b64encode(picture).decode("ascii") if picture else "")


def _declared_picture(pieces: wire.RequestPieces) -> wire.Picture | None:
    """The picture a refused request declared, if its JSON says one well
    enough to read its pieces off the wire and drop them."""
    try:
        doc = wire.load_object(b"".join(pieces.parts))
        return wire._parse_picture(doc.get("picture")) if doc.get("op") == "chat.send" else None
    except wire.BadRequest:
        return None


def _admit(open_now: int, max_open: int, waiting: deque, max_waiting: int) -> bool:
    if open_now >= max_open:
        if not waiting:
            return False
        waiting.popleft().close()
    while len(waiting) >= max_waiting:
        waiting.popleft().close()
    return True


def _done_waiting(waiting: deque, writer: asyncio.StreamWriter) -> None:
    try:
        waiting.remove(writer)
    except ValueError:
        pass
