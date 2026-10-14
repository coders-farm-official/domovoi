r"""The control channel on Windows: a named pipe only this user can open.

Spare-device satellites on Windows (2026-10-13). CPython on Windows has no
AF_UNIX sockets, and asyncio cannot serve one there, so the engine's local
control channel (:mod:`satellite.control`: the same verbs, the same
one-JSON-object-per-line framing, the same fixture) runs over
``\\.\pipe\domovoi-satellite-<user SID>`` instead. What the Unix socket gets
from its 0600 mode, its 0700 directory and ``SO_PEERCRED``, the pipe gets
from:

* **A DACL with one entry** — the engine's own user (:func:`win32.pipe_sddl`):
  no other account can open it, and a low-integrity or app-container
  process of the same user can't write to it (the default integrity label).
* **No network clients** — ``PIPE_REJECT_REMOTE_CLIENTS``: pipes are
  otherwise reachable over SMB from other machines.
* **The name claimed first** — ``FILE_FLAG_FIRST_PIPE_INSTANCE``: if another
  process (another user, signed in first) already holds a pipe of this name,
  the engine refuses to share it and the channel stays off, said in its log.
* **Both ends checked** — the engine closes a client whose process is not
  this user's (``GetNamedPipeClientProcessId``), and the client — the tray,
  the CLI, the installer — refuses a pipe served by anyone else
  (``GetNamedPipeServerProcessId``), so nobody can sit on the name and feed
  the tray a "muted" that isn't true, or read the approval code.

The engine side is asyncio's proactor loop (the Windows default): pipe
instances are created here, with the security above, and handed to the
loop's own overlapped-pipe transport. The client side is blocking, with
timeouts, in the shape :class:`satellite.control.ControlClient` uses a
socket. Stdlib only.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any, Callable

from satellite import win32

log = logging.getLogger("satellite.control")

Handler = Callable[[asyncio.StreamReader, asyncio.StreamWriter], Any]

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
#: How long a client waits for a free pipe instance (the engine always
#: keeps one listening; this covers the gap between two accepts).
CONNECT_WAIT_SEC = 2.0


class NamedPipeTransport:
    """``ControlTransport`` for Windows. ``limit`` is the StreamReader's
    line limit (the server's framing check runs on top of it)."""

    def __init__(self, name: str | None = None, *, limit: int = 65536,
                 own_sid: str | None = None) -> None:
        self._own_sid = own_sid
        self.name = name
        self._limit = limit
        self._security: win32.PipeSecurity | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._handler: Handler | None = None
        self._unconnected: Any = None
        self._free: set[Any] = set()
        self._accept_future: Any = None
        self._closed = True

    def describe(self) -> str:
        return self.name or win32.PIPE_STEM + "<user SID>"

    async def start(self, handler: Handler) -> None:
        from asyncio import windows_utils

        loop = asyncio.get_running_loop()
        if not hasattr(loop, "_proactor") or not hasattr(loop, "_make_duplex_pipe_transport"):
            raise NotImplementedError(
                "the control pipe needs asyncio's proactor event loop (the Windows default)"
            )
        self._own_sid = self._own_sid or win32.current_user_sid()
        self.name = self.name or win32.control_pipe_name(self._own_sid)
        self._security = win32.PipeSecurity(self._own_sid)
        self._loop = loop
        self._handler = handler
        self._pipe_handle_cls = windows_utils.PipeHandle
        try:
            # Raises when the name is taken: never join a pipe someone else
            # created (it would be theirs to serve, and theirs to read).
            self._unconnected = self._new_instance(first=True)
        except OSError as e:
            self._security.close()
            raise OSError(
                f"the control pipe {self.name} is already held by another process ({e}); "
                "not sharing it"
            ) from e
        self._closed = False
        loop.call_soon(self._accept)

    def _new_instance(self, *, first: bool):
        assert self._security is not None
        handle = win32.create_pipe_instance(self.name, self._security, first=first)
        pipe = self._pipe_handle_cls(handle)
        self._free.add(pipe)
        return pipe

    def _take_unconnected(self):
        """The listening instance, with the next one already created, so a
        client never finds the name missing between two accepts."""
        if self._closed:
            return None
        current, self._unconnected = self._unconnected, self._new_instance(first=False)
        return current

    def _protocol(self) -> asyncio.Protocol:
        reader = asyncio.StreamReader(limit=self._limit, loop=self._loop)
        return asyncio.StreamReaderProtocol(reader, self._guarded, loop=self._loop)

    async def _guarded(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        pipe = writer.get_extra_info("pipe")
        handle = getattr(pipe, "handle", None)
        pid = win32.pipe_client_pid(handle) if handle is not None else None
        if not win32.peer_is_this_user(pid, self._own_sid):
            log.warning("control: refused a pipe client (pid %s) that is not this user's", pid)
            writer.close()
            return
        assert self._handler is not None
        await self._handler(reader, writer)

    def _accept(self, f: Any = None) -> None:
        loop = self._loop
        assert loop is not None
        pipe = None
        try:
            if f is not None:
                pipe = f.result()
                self._free.discard(pipe)
                if self._closed:
                    pipe.close()
                    return
                loop._make_duplex_pipe_transport(pipe, self._protocol(), extra={"addr": self.name})
                pipe = None
            pipe = self._take_unconnected()
            if pipe is None:
                return
            f = loop._proactor.accept_pipe(pipe)
        except asyncio.CancelledError:
            if pipe is not None:
                pipe.close()
            return
        except BrokenPipeError:
            # The client went away between connecting and being served.
            if pipe is not None and pipe.fileno() != -1:
                pipe.close()
            loop.call_soon(self._accept)
            return
        except OSError as e:
            if pipe is not None and pipe.fileno() != -1:
                pipe.close()
            if not self._closed:
                log.warning("control: pipe accept failed (%s); trying again", e)
                loop.call_later(1.0, self._accept)
            return
        self._accept_future = f
        f.add_done_callback(self._accept)

    async def close(self) -> None:
        self._closed = True
        if self._accept_future is not None:
            self._accept_future.cancel()
            self._accept_future = None
        for pipe in list(self._free):
            with contextlib.suppress(Exception):
                pipe.close()
        self._free.clear()
        self._unconnected = None
        if self._security is not None:
            self._security.close()
            self._security = None


class PipeStream:
    """A connected client end, shaped like the socket
    :class:`satellite.control.ControlClient` uses: ``sendall``, ``recv``,
    ``settimeout``, ``close``. Overlapped I/O, so reads and writes honour
    the timeout. Refuses a pipe served by another user's process."""

    def __init__(self, name: str, *, timeout: float | None = 5.0, own_sid: str | None = None) -> None:
        import _winapi

        self._winapi = _winapi
        self.name = name
        self.timeout = timeout
        deadline = time.monotonic() + max(CONNECT_WAIT_SEC, timeout or 0.0)
        while True:
            try:
                self._h = _winapi.CreateFile(
                    name, GENERIC_READ | GENERIC_WRITE, 0, _winapi.NULL, OPEN_EXISTING,
                    win32.FILE_FLAG_OVERLAPPED, _winapi.NULL,
                )
                break
            except OSError as e:
                if getattr(e, "winerror", None) == win32.ERROR_PIPE_BUSY and time.monotonic() < deadline:
                    with contextlib.suppress(OSError):
                        _winapi.WaitNamedPipe(name, 200)
                    continue
                raise ConnectionError(
                    f"the satellite engine isn't answering on {name} ({e.strerror or e})"
                ) from e
        pid = win32.pipe_server_pid(self._h)
        if not win32.peer_is_this_user(pid, own_sid):
            self.close()
            raise ConnectionError(
                f"{name} is served by a process that is not yours (pid {pid}); not talking to it"
            )

    def settimeout(self, timeout: float | None) -> None:
        self.timeout = timeout

    def _wait(self, ov: Any) -> None:
        ms = win32.INFINITE if self.timeout is None else max(1, int(self.timeout * 1000))
        res = self._winapi.WaitForMultipleObjects([ov.event], False, ms)
        if res == win32.WAIT_TIMEOUT:
            ov.cancel()
            with contextlib.suppress(OSError):
                ov.GetOverlappedResult(True)
            raise TimeoutError(f"the satellite engine did not answer within {self.timeout:.0f} s")

    def sendall(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            try:
                ov, err = self._winapi.WriteFile(self._h, bytes(view), overlapped=True)
            except BrokenPipeError as e:
                raise ConnectionError("the satellite engine closed the pipe") from e
            if err == win32.ERROR_IO_PENDING:
                self._wait(ov)
            try:
                written, err = ov.GetOverlappedResult(True)
            except BrokenPipeError as e:
                raise ConnectionError("the satellite engine closed the pipe") from e
            view = view[written:]

    def recv(self, size: int) -> bytes:
        try:
            ov, err = self._winapi.ReadFile(self._h, size, overlapped=True)
        except BrokenPipeError:
            return b""
        if err == win32.ERROR_IO_PENDING:
            self._wait(ov)
        try:
            _read, err = ov.GetOverlappedResult(True)
        except BrokenPipeError:
            return b""
        return bytes(ov.getbuffer())

    def close(self) -> None:
        h, self._h = getattr(self, "_h", None), None
        if h is not None:
            with contextlib.suppress(OSError):
                self._winapi.CloseHandle(h)
