"""A small asyncio client for MPD's control protocol.

Replaces python-mpd2 (LGPL-3.0-or-later) since 2026-10-15; Domovoi takes
permissively licensed dependencies only (docs/CONTRIBUTING.md). Written
from MPD's protocol documentation (mpd.readthedocs.io, "Protocol"); it
covers what Domovoi sends a room's daemon, and answers in the shapes
python-mpd2 did, so ``domovoi/clients/mpd.py`` reads them unchanged:

* a song list (``search``, ``playlistinfo``) is a list of dicts, a new
  dict at each ``file:`` line;
* an object (``status``, ``currentsong``) is one dict, ``{}`` when empty;
* keys are lower-cased; a key that repeats within one object (a song with
  two ``Artist:`` lines) holds a list of its values;
* ``addid`` / ``update`` answer their single value as a string;
* a refusal is :class:`CommandError`, whose text is MPD's own after
  ``ACK `` (``[55@0] {previous} Not playing``), the code first.

The protocol is line based: ``OK MPD <version>`` on connect, then each
command is one line of the name and its double-quoted arguments, answered
by ``key: value`` lines ending with ``OK`` or with one ``ACK`` line. An
argument with a line break can't be sent (it would end the command and
start another), so it is refused here, as MPD refuses a bad argument.

One command at a time per connection; ``domovoi/clients/mpd.py`` opens a
connection per operation, as it always has.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Iterable

HELLO = "OK MPD "
HELLO_TIMEOUT_SEC = 5.0

_ACK = re.compile(r"\[(?P<code>\d+)@(?P<index>\d+)\]\s+\{(?P<command>[^}]*)\}\s*(?P<message>.*)")
#: MPD's ACK code for a bad argument.
ACK_ERROR_ARG = 2


class MPDError(Exception):
    """Anything the MPD client reports."""


class CommandError(MPDError):
    """MPD answered ``ACK``: it refused the command. ``str()`` is MPD's text
    (``[50@0] {add} No such directory``); ``code`` its number."""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        m = _ACK.match(text)
        self.code: int | None = int(m.group("code")) if m else None
        self.command: str | None = m.group("command") if m else None
        self.message: str = m.group("message") if m else text


class MPDConnectionError(MPDError, ConnectionError):
    """The connection failed, closed, or never said hello."""


class ProtocolError(MPDError):
    """MPD said something this client doesn't understand."""


def quote(arg: Any) -> str:
    """One argument as MPD reads it: in double quotes, with ``\\`` and ``"``
    escaped by a backslash."""
    text = str(arg)
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _pairs(lines: Iterable[str]) -> Iterable[tuple[str, str]]:
    for line in lines:
        key, sep, value = line.partition(": ")
        if not sep:
            raise ProtocolError(f"not a key: value line: {line[:80]!r}")
        yield key, value


def parse_objects(lines: Iterable[str], delimiters: Iterable[str] = ()) -> list[dict[str, Any]]:
    """``key: value`` lines as dicts: keys lower-cased, a new dict at each
    delimiter key, a repeated key's values gathered into a list."""
    delims = set(delimiters)
    out: list[dict[str, Any]] = []
    obj: dict[str, Any] = {}
    for key, value in _pairs(lines):
        key = key.lower()
        if obj and key in delims:
            out.append(obj)
            obj = {}
        elif key in obj:
            if isinstance(obj[key], list):
                obj[key].append(value)
            else:
                obj[key] = [obj[key], value]
            continue
        obj[key] = value
    if obj:
        out.append(obj)
    return out


def parse_object(lines: list[str]) -> dict[str, Any]:
    objs = parse_objects(lines)
    return objs[0] if objs else {}


def parse_item(lines: list[str]) -> str | None:
    pairs = list(_pairs(lines))
    return pairs[0][1] if len(pairs) == 1 else None


def parse_nothing(lines: list[str]) -> None:
    if lines:
        raise ProtocolError(f"unexpected answer: {', '.join(lines)[:120]}")


class MPDConnection:
    """One control connection to an MPD daemon."""

    def __init__(self) -> None:
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()
        self.mpd_version: str | None = None

    # ── connection ───────────────────────────────────────────────────

    async def connect(self, host: str, port: int = 6600, *, timeout: float = HELLO_TIMEOUT_SEC) -> None:
        """Open the connection and read MPD's hello, within ``timeout`` for
        the hello (the TCP connect itself is the caller's to bound)."""
        try:
            self._reader, self._writer = await asyncio.open_connection(host, port)
        except OSError as e:
            raise MPDConnectionError(f"could not connect to MPD at {host}:{port}: {e}") from e
        try:
            line = await asyncio.wait_for(self._reader.readline(), timeout)
        except asyncio.TimeoutError:
            self.disconnect()
            raise MPDConnectionError("No response from server while reading MPD hello") from None
        except asyncio.CancelledError:
            self.disconnect()                       # a caller's own deadline: don't leak the socket
            raise
        except OSError as e:
            self.disconnect()
            raise MPDConnectionError(f"connection lost while reading MPD hello: {e}") from e
        text = line.decode("utf-8", "replace")
        if not text.endswith("\n"):
            self.disconnect()
            raise MPDConnectionError("Connection lost while reading MPD hello")
        if not text.startswith(HELLO):
            self.disconnect()
            raise ProtocolError(f"not an MPD hello: {text.strip()[:80]!r}")
        self.mpd_version = text[len(HELLO):].strip()

    @property
    def connected(self) -> bool:
        return self._writer is not None

    def disconnect(self) -> None:
        """Close the connection (safe to call twice, or unconnected)."""
        writer, self._reader, self._writer = self._writer, None, None
        if writer is not None:
            try:
                writer.close()
            except Exception:  # noqa: BLE001 — closing is best-effort
                pass

    # ── one command ──────────────────────────────────────────────────

    async def _readline(self) -> str:
        assert self._reader is not None
        try:
            raw = await self._reader.readline()
        except OSError as e:
            self.disconnect()
            raise MPDConnectionError(f"Connection lost while reading line: {e}") from e
        if not raw.endswith(b"\n"):
            self.disconnect()
            raise MPDConnectionError("Connection lost while reading line")
        return raw[:-1].decode("utf-8", "replace")

    async def command(self, name: str, *args: Any) -> list[str]:
        """Send ``name`` with ``args`` and return its answer lines (without
        the closing ``OK``); raise :class:`CommandError` on ``ACK``."""
        for a in args:
            if "\n" in str(a) or "\r" in str(a):
                raise CommandError(f"[{ACK_ERROR_ARG}@0] {{{name}}} an argument contains a line break")
        line = " ".join([name, *(quote(a) for a in args)]) + "\n"
        async with self._lock:
            if self._writer is None:
                raise MPDConnectionError("not connected to MPD")
            try:
                return await self._exchange(name, line)
            except asyncio.CancelledError:
                # Cancelled halfway through an answer: what is left of it
                # would be read as the next command's. Drop the connection.
                self.disconnect()
                raise

    async def _exchange(self, name: str, line: str) -> list[str]:
        assert self._writer is not None
        try:
            self._writer.write(line.encode("utf-8"))
            await self._writer.drain()
        except OSError as e:
            self.disconnect()
            raise MPDConnectionError(f"connection lost while sending {name}: {e}") from e
        lines: list[str] = []
        while True:
            text = await self._readline()
            if text == "OK":
                return lines
            if text.startswith("ACK "):
                raise CommandError(text[4:].strip())
            if text.startswith("binary: "):
                # A binary chunk (albumart, readpicture): read past it so
                # the connection stays in step.
                size = int(text[8:])
                assert self._reader is not None
                try:
                    await self._reader.readexactly(size + 1)
                except (asyncio.IncompleteReadError, OSError) as e:
                    self.disconnect()
                    raise MPDConnectionError("Connection lost while reading binary") from e
            lines.append(text)

    # ── the commands Domovoi sends ───────────────────────────────────

    async def status(self) -> dict[str, Any]:
        return parse_object(await self.command("status"))

    async def currentsong(self) -> dict[str, Any]:
        return parse_object(await self.command("currentsong"))

    async def search(self, *args: Any) -> list[dict[str, Any]]:
        return parse_objects(await self.command("search", *args), ("file",))

    async def playlistinfo(self, *args: Any) -> list[dict[str, Any]]:
        return parse_objects(await self.command("playlistinfo", *args), ("file",))

    async def addid(self, uri: str, *args: Any) -> str | None:
        return parse_item(await self.command("addid", uri, *args))

    async def update(self, *args: Any) -> str | None:
        return parse_item(await self.command("update", *args))

    async def _nothing(self, name: str, *args: Any) -> None:
        parse_nothing(await self.command(name, *args))

    async def add(self, uri: str) -> None:
        await self._nothing("add", uri)

    async def addtagid(self, song_id: Any, tag: str, value: str) -> None:
        await self._nothing("addtagid", song_id, tag, value)

    async def clear(self) -> None:
        await self._nothing("clear")

    async def delete(self, pos_or_range: Any) -> None:
        await self._nothing("delete", pos_or_range)

    async def deleteid(self, song_id: Any) -> None:
        await self._nothing("deleteid", song_id)

    async def moveid(self, song_id: Any, to: Any) -> None:
        await self._nothing("moveid", song_id, to)

    async def next(self) -> None:
        await self._nothing("next")

    async def previous(self) -> None:
        await self._nothing("previous")

    async def pause(self, state: Any) -> None:
        await self._nothing("pause", state)

    async def play(self, pos: Any = None) -> None:
        await self._nothing("play", *(() if pos is None else (pos,)))

    async def seek(self, pos: Any, seconds: Any) -> None:
        await self._nothing("seek", pos, seconds)

    async def seekcur(self, seconds: Any) -> None:
        await self._nothing("seekcur", seconds)

    async def setvol(self, volume: Any) -> None:
        await self._nothing("setvol", volume)

    async def stop(self) -> None:
        await self._nothing("stop")


__all__ = [
    "ACK_ERROR_ARG",
    "CommandError",
    "MPDConnection",
    "MPDConnectionError",
    "MPDError",
    "ProtocolError",
    "parse_item",
    "parse_object",
    "parse_objects",
    "quote",
]
