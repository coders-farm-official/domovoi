"""The satellite engine's local control channel: status, mute, volume, the
approval code, and the "ask me" approvals, for the tray and the CLI.

Spare-device satellites (S1, 2026-10-13). On a laptop the satellite
**engine** runs headless (a ``systemd --user`` service, or a system service
on a headless box) and a separate, optional **tray** process shows its
state. They talk over a local socket with a small JSON protocol, so the
engine behaves the same whether or not anything is watching, and a
Windows satellite can reuse the protocol over its own transport.

Wire format (``docs/uml/satellite-control-fixture.json`` pins it):

* One JSON object per line, UTF-8, each at most :data:`MAX_LINE` bytes.
* A request is ``{"verb": "<verb>", "req": <optional id>, ...arguments}``;
  it gets exactly one response line, in order: ``{"ok": true, "verb": …,
  "req": …, …result}`` or ``{"ok": false, "verb": …, "req": …, "error":
  "<code>", "message": "…"}``.
* ``watch`` answers with the status and then keeps the connection open,
  adding ``{"event": "status", …status}`` lines whenever the status
  changes (the mic-live indicator rides on this).

Verbs: ``status``, ``watch``, ``mute``, ``unmute``, ``volume`` (no argument
reads it; ``level`` 0-100 or ``delta`` -100..100 sets it), ``code`` (the
approval code while the device waits for approval), ``logs-path``,
``identify`` (play the chime: "which room is this"), and, for the "ask me"
permission, ``approve`` / ``decline`` a pending update or restart
(``action`` names it). See :data:`VERBS`.

Who may connect: the engine's own user. The Unix socket is created 0600
inside the 0700 config dir, and a peer whose uid is neither the engine's
nor root is turned away (``SO_PEERCRED``). Nothing here reaches the
network.

Stdlib only. The engine side is asyncio (:class:`ControlServer`); the
client side is a plain blocking socket (:class:`ControlClient`) so the CLI
and the tray need no event loop of their own to ask a question.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import socket
import struct
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol

log = logging.getLogger("satellite.control")

PROTOCOL = 1
#: Longest line either side sends or accepts, in bytes (the newline aside).
MAX_LINE = 16384
#: Connections served at once; a client past this is closed at accept.
MAX_CLIENTS = 8
#: How long a non-watching connection may sit idle before it is closed.
IDLE_TIMEOUT_SEC = 30.0
SOCKET_NAME = "control.sock"

#: Every verb, its arguments and what it answers. The fixture repeats this
#: table; :func:`test_control_protocol` holds the two together.
VERBS: dict[str, str] = {
    "status": "the engine's status (see Status below)",
    "watch": "the status now, then an {\"event\": \"status\"} line on every change",
    "mute": "stop listening: the microphone is closed until unmute (kept across restarts)",
    "unmute": "listen again",
    "volume": "no argument: read; `level` 0-100 or `delta` -100..100: set; answers {volume}",
    "code": "the approval code while waiting for approval: {waiting, code}",
    "logs-path": "where the engine's log goes: {kind, path, hint}",
    "identify": "play the room chime now (\"which room is this\"); answers {room}",
    "approve": "run a pending action (the \"ask me\" permission): `action` = its id, or \"all\"",
    "decline": "drop a pending action: `action` = its id, or \"all\"",
}

#: Error codes a response may carry.
ERRORS: dict[str, str] = {
    "bad_request": "not a JSON object, no verb, or a line too long",
    "unknown_verb": "a verb this engine doesn't have",
    "bad_argument": "an argument missing, of the wrong type or out of range",
    "no_pending": "approve/decline named no pending action",
    "not_available": "this engine can't do that here (no volume control, no chime)",
    "failed": "the engine tried and it did not work (the message says why)",
}

#: The connection states a status may report, and what each means.
CONNECTIONS: dict[str, str] = {
    "connecting": "looking for the core or dialing it",
    "connected": "a session is up: the core sent `ready`",
    "awaiting_approval": "the core parked this device: type its code in the dashboard",
    "offline": "the core can't be reached right now (reason says what failed)",
    "refused": "the core refused this device (pairing rejected, another device has the room)",
    "unpinned": "the core at the address could not prove the pinned identity",
}
#: What the microphone is doing: the tray's mic-live indicator.
MIC_STATES = ("listening", "capturing", "muted", "off")
#: What the speaker is doing.
OUTPUT_STATES = ("idle", "speaking", "music")
#: Kinds of action the "ask me" permission holds for the person's OK.
PENDING_KINDS: dict[str, str] = {
    "update": "new satellite code from the core (`upgrade`)",
    "restart": "a restart the core asked for (`restart`, or a spoken \"restart\")",
    "config": "settings pushed from the dashboard (`set_config`, ends in a restart)",
    "wake_word": "a wake-word model pushed to this room (`set_wake_word`, ends in a restart)",
}
PERMISSIONS = ("automatic", "ask")
#: The most lines a pending action may list (each shown, none cut).
MAX_DETAILS = 40


class ControlError(Exception):
    """A request the engine refused; ``code`` is one of :data:`ERRORS`."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        assert code in ERRORS, code
        self.code = code


# ─── the "ask me" permission: actions held for the person's OK ────────────

@dataclass
class PendingAction:
    id: str
    kind: str
    summary: str
    received_at: float
    run: Callable[[], None] = field(repr=False)
    #: Every change it makes, one line each, never shortened (a settings
    #: push lists each key and value): the person reads all of it before
    #: allowing it.
    details: tuple[str, ...] = ()

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "summary": self.summary,
                "details": list(self.details), "received_at": round(self.received_at, 3)}


class PendingActions:
    """What the core asked for and the person hasn't answered yet.

    One per kind: a newer update replaces an older one (it fetches the
    core's current code anyway). Kept in memory only — a restart drops
    them, and the core asks again when it next wants something. Approving
    runs the action on a worker thread, exactly as the "automatic"
    permission would have run it on arrival."""

    def __init__(self, on_change: Callable[[], None] | None = None) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, PendingAction] = {}
        self._on_change = on_change or (lambda: None)

    def offer(self, kind: str, summary: str, run: Callable[[], None], *,
              now: float | None = None, details: list[str] | tuple[str, ...] = ()) -> PendingAction:
        if kind not in PENDING_KINDS:
            raise ValueError(f"unknown pending kind {kind!r}")
        if len(summary) > 300 or len(details) > MAX_DETAILS or any(len(d) > 400 for d in details):
            # Never shortened: what can't be shown whole is not offered.
            raise ValueError("a pending action must be shown in full; this one is too long")
        action = PendingAction(
            id=uuid.uuid4().hex[:12], kind=kind, summary=summary,
            received_at=time.time() if now is None else now, run=run, details=tuple(details),
        )
        with self._lock:
            self._items[kind] = action
        log.info("held for the person's OK (ask me): %s — %s", kind, action.summary)
        self._on_change()
        return action

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            items = sorted(self._items.values(), key=lambda a: a.received_at)
        return [a.public() for a in items]

    def _take(self, action_id: str) -> list[PendingAction]:
        with self._lock:
            if action_id == "all":
                taken = list(self._items.values())
                self._items.clear()
            else:
                taken = [a for a in self._items.values() if a.id == action_id]
                for a in taken:
                    self._items.pop(a.kind, None)
        if taken:
            self._on_change()
        return taken

    def approve(self, action_id: str) -> list[dict[str, Any]]:
        taken = self._take(action_id)
        for action in sorted(taken, key=lambda a: a.received_at):
            log.info("approved by the person: %s — %s", action.kind, action.summary)
            threading.Thread(target=action.run, daemon=True,
                             name=f"approved-{action.kind}").start()
        return [a.public() for a in taken]

    def decline(self, action_id: str) -> list[dict[str, Any]]:
        taken = self._take(action_id)
        for action in taken:
            log.info("declined by the person: %s — %s", action.kind, action.summary)
        return [a.public() for a in taken]


# ─── what the engine offers the protocol ──────────────────────────────────

class EngineApi(Protocol):
    """The engine as the control channel sees it. The satellite client
    implements this; the tests implement it with a fake."""

    def control_status(self) -> dict[str, Any]: ...
    def control_set_muted(self, muted: bool) -> None: ...
    def control_volume(self, level: int | None) -> int | None: ...
    def control_volume_available(self) -> bool: ...
    def control_approval_code(self) -> tuple[bool, str | None]: ...
    def control_logs_path(self) -> dict[str, Any]: ...
    def control_identify(self) -> bool: ...
    def control_pending(self) -> PendingActions: ...


def _int_arg(req: dict[str, Any], name: str, lo: int, hi: int) -> int | None:
    if name not in req:
        return None
    value = req[name]
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise ControlError("bad_argument", f"`{name}` must be a whole number {lo}..{hi}")
    return value


def handle(api: EngineApi, req: Any) -> dict[str, Any]:
    """One request's response (``watch`` aside, which the server streams).
    Never raises: every refusal is a response."""
    verb = req.get("verb") if isinstance(req, dict) else None
    out: dict[str, Any] = {"verb": verb}
    if isinstance(req, dict) and "req" in req:
        out["req"] = req["req"]
    try:
        if not isinstance(req, dict) or not isinstance(verb, str):
            raise ControlError("bad_request", "a request is a JSON object with a \"verb\"")
        if verb not in VERBS:
            raise ControlError("unknown_verb", f"no verb {verb!r} (have: {', '.join(VERBS)})")
        if verb in ("status", "watch"):
            out.update(api.control_status())
        elif verb in ("mute", "unmute"):
            api.control_set_muted(verb == "mute")
            out["mic"] = api.control_status().get("mic")
        elif verb == "volume":
            level = _int_arg(req, "level", 0, 100)
            delta = _int_arg(req, "delta", -100, 100)
            if level is not None and delta is not None:
                raise ControlError("bad_argument", "give `level` or `delta`, not both")
            if not api.control_volume_available():
                raise ControlError("not_available", "this device has no volume control the engine can drive")
            if delta is not None:
                current = api.control_volume(None)
                if current is None:
                    raise ControlError("failed", "could not read the current volume")
                level = max(0, min(100, current + delta))
            got = api.control_volume(level)
            if got is None:
                raise ControlError("failed", "the volume control did not answer")
            out["volume"] = got
        elif verb == "code":
            waiting, code = api.control_approval_code()
            out["waiting"] = waiting
            out["code"] = code if waiting else None
        elif verb == "logs-path":
            out.update(api.control_logs_path())
        elif verb == "identify":
            if not api.control_identify():
                raise ControlError("not_available", "the chime could not be played")
            out["room"] = api.control_status().get("room")
        elif verb in ("approve", "decline"):
            action = req.get("action")
            if not isinstance(action, str) or not action:
                raise ControlError("bad_argument", "`action` must be a pending action's id, or \"all\"")
            pending = api.control_pending()
            done = pending.approve(action) if verb == "approve" else pending.decline(action)
            if not done:
                raise ControlError("no_pending", f"nothing pending with id {action!r}")
            out["done"] = done
            out["pending"] = pending.list()
    except ControlError as e:
        out.update({"ok": False, "error": e.code, "message": str(e)})
        return out
    except Exception as e:      # noqa: BLE001 — a bug must not kill the socket
        log.exception("control: %s failed", verb)
        out.update({"ok": False, "error": "failed", "message": f"{type(e).__name__}: {e}"[:200]})
        return out
    out["ok"] = True
    return out


def encode(doc: dict[str, Any]) -> bytes:
    line = json.dumps(doc, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(line) > MAX_LINE:
        raise ValueError("control line too long")
    return line + b"\n"


# ─── transports (the platform seam) ───────────────────────────────────────

Handler = Callable[[asyncio.StreamReader, asyncio.StreamWriter], Any]


class ControlTransport(Protocol):
    """Where the control channel listens. Linux: :class:`UnixSocketTransport`.
    A Windows satellite brings a named-pipe one with the same framing."""

    def describe(self) -> str: ...
    async def start(self, handler: Handler) -> None: ...
    async def close(self) -> None: ...


def peer_uid(sock: socket.socket) -> int | None:
    """The connecting process's uid (Linux ``SO_PEERCRED``), or None where
    the platform can't say."""
    opt = getattr(socket, "SO_PEERCRED", None)
    if opt is None:
        return None
    try:
        creds = sock.getsockopt(socket.SOL_SOCKET, opt, struct.calcsize("3i"))
    except OSError:
        return None
    _pid, uid, _gid = struct.unpack("3i", creds)
    return uid


class UnixSocketTransport:
    """``~/.domovoi/control.sock``: created 0600 (the umask is held at 077
    around the bind), in a directory made 0700; an old socket left by a
    crash is replaced, anything else at the path is left alone and the
    channel stays off. Peers are held to this process's uid (or root); one
    whose uid can't be read is refused."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._server: asyncio.AbstractServer | None = None

    def describe(self) -> str:
        return str(self.path)

    async def start(self, handler: Handler) -> None:
        import stat

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(self.path.parent, 0o700)
        try:
            st = os.lstat(self.path)
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISSOCK(st.st_mode):
                raise OSError(f"{self.path} exists and is not a socket; not replacing it")
            os.unlink(self.path)
        own_uid = os.getuid()

        async def guarded(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            sock = writer.get_extra_info("socket")
            uid = peer_uid(sock) if sock is not None else None
            # A peer this process can't identify is refused like a stranger.
            if uid is None or uid not in (own_uid, 0):
                log.warning("control: refused a connection from uid %s", uid)
                writer.close()
                return
            await handler(reader, writer)

        old = os.umask(0o077)
        try:
            self._server = await asyncio.start_unix_server(guarded, path=str(self.path), limit=MAX_LINE + 2)
        finally:
            os.umask(old)
        os.chmod(self.path, 0o600)

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()
            self._server = None
        with contextlib.suppress(OSError):
            os.unlink(self.path)


def default_socket_path(config_dir: Path) -> Path:
    return Path(config_dir) / SOCKET_NAME


def default_transport(config_dir: Path) -> ControlTransport:
    """The platform's transport. Linux (and any POSIX): the Unix socket.
    Windows has none yet — the Windows satellite adds a named pipe here."""
    if sys.platform == "win32":
        raise NotImplementedError(
            "no control transport on Windows yet: a named pipe with the same "
            "line framing (docs/uml/satellite-control-fixture.json)"
        )
    return UnixSocketTransport(default_socket_path(config_dir))


# ─── the engine side ──────────────────────────────────────────────────────

class ControlServer:
    """Serves :func:`handle` over a :class:`ControlTransport`, and pushes the
    status to watchers. :meth:`notify` is thread-safe: the mic thread, the
    receiver loop and the pending-action store call it on every change; a
    status equal to the last one sent is not sent again."""

    def __init__(self, api: EngineApi, transport: ControlTransport) -> None:
        self.api = api
        self.transport = transport
        self._loop: asyncio.AbstractEventLoop | None = None
        self._watchers: set[asyncio.StreamWriter] = set()
        self._clients = 0
        self._last_sent: dict[asyncio.StreamWriter, dict[str, Any]] = {}
        self._dirty: asyncio.Event | None = None
        self._pusher: asyncio.Task | None = None

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._dirty = asyncio.Event()
        await self.transport.start(self._serve)
        self._pusher = asyncio.create_task(self._push_loop(), name="control-push")
        log.info("control channel on %s", self.transport.describe())

    async def close(self) -> None:
        if self._pusher is not None:
            self._pusher.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._pusher
        for w in list(self._watchers):
            with contextlib.suppress(Exception):
                w.close()
        self._watchers.clear()
        await self.transport.close()

    def notify(self) -> None:
        loop, dirty = self._loop, self._dirty
        if loop is None or dirty is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(dirty.set)
        except RuntimeError:
            pass

    async def _push_loop(self) -> None:
        assert self._dirty is not None
        while True:
            await self._dirty.wait()
            self._dirty.clear()
            # Coalesce a burst (capture start + LED + output) into one line.
            await asyncio.sleep(0.02)
            if not self._watchers:
                continue
            status = self.api.control_status()
            for w in list(self._watchers):
                if self._last_sent.get(w) == status:
                    continue
                try:
                    w.write(encode({"event": "status", **status}))
                    await asyncio.wait_for(w.drain(), timeout=2.0)
                    self._last_sent[w] = status
                except Exception:      # noqa: BLE001 — a gone watcher is just gone
                    self._drop(w)

    def _drop(self, writer: asyncio.StreamWriter) -> None:
        self._watchers.discard(writer)
        self._last_sent.pop(writer, None)
        with contextlib.suppress(Exception):
            writer.close()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self._clients >= MAX_CLIENTS:
            writer.close()
            return
        self._clients += 1
        try:
            while True:
                try:
                    line = await asyncio.wait_for(
                        reader.readline(),
                        timeout=None if writer in self._watchers else IDLE_TIMEOUT_SEC,
                    )
                except (asyncio.TimeoutError, ConnectionError):
                    break
                except (asyncio.LimitOverrunError, ValueError):
                    writer.write(encode({"ok": False, "verb": None, "error": "bad_request",
                                         "message": f"a line is at most {MAX_LINE} bytes"}))
                    break
                if not line:
                    break
                if len(line) > MAX_LINE + 1:
                    writer.write(encode({"ok": False, "verb": None, "error": "bad_request",
                                         "message": f"a line is at most {MAX_LINE} bytes"}))
                    break
                try:
                    req = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, ValueError):
                    req = None
                # Off the event loop: a verb may run a subprocess (volume).
                resp = await asyncio.to_thread(handle, self.api, req)
                writer.write(encode(resp))
                await writer.drain()
                if resp.get("ok") and resp.get("verb") == "watch":
                    self._watchers.add(writer)
                    self._last_sent[writer] = {k: v for k, v in resp.items()
                                               if k not in ("ok", "verb", "req")}
        finally:
            self._clients -= 1
            self._drop(writer)


# ─── the client side (CLI, tray, installer) ───────────────────────────────

class ControlClient:
    """A blocking client. ``call`` sends one request and returns its
    response dict; ``watch`` yields the status, then every change."""

    def __init__(self, path: Path, *, timeout: float = 5.0) -> None:
        self.path = Path(path)
        self.timeout = timeout

    def _connect(self) -> socket.socket:
        if not hasattr(socket, "AF_UNIX"):
            raise ConnectionError("no Unix sockets on this platform")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(str(self.path))
        except OSError as e:
            sock.close()
            raise ConnectionError(
                f"the satellite engine isn't answering on {self.path} ({e.strerror or e})"
            ) from e
        return sock

    @staticmethod
    def _lines(sock: socket.socket) -> Iterator[dict[str, Any]]:
        buf = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                return
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if line.strip():
                    yield json.loads(line.decode("utf-8"))
            if len(buf) > MAX_LINE:
                raise ConnectionError("the engine sent an over-long line")

    def call(self, verb: str, **args: Any) -> dict[str, Any]:
        with contextlib.closing(self._connect()) as sock:
            sock.sendall(encode({"verb": verb, **args}))
            for doc in self._lines(sock):
                return doc
        raise ConnectionError("the engine closed the connection without answering")

    def watch(self) -> Iterator[dict[str, Any]]:
        """The first item is the ``watch`` response (the status now); then
        one ``{"event": "status", …}`` per change. Blocks between them; ends
        when the engine goes away."""
        sock = self._connect()
        sock.settimeout(None)
        try:
            sock.sendall(encode({"verb": "watch"}))
            yield from self._lines(sock)
        finally:
            sock.close()
