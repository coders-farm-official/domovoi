"""``LoopbackPair``: two houses over loopback, for plugin mail tests (SDK 1.18).

The test kit the design note ``FRIEND-HOUSES-PLUGINS`` §4 names: house A
dials house B's real listener on ``127.0.0.1`` with a real Noise session,
and B's listener reports to B's real core handler
(:func:`domovoi.friend_houses.service.handle_event`), which answers on the
listener's "stdin". No Tor, no Docker, no database.

**This is the skeleton over build slice 3's wire.** It speaks ``CAPS`` and
reads ``MESSAGE_ACK`` / ``MESSAGE_NACK``; with no plugin mail service yet,
B's core refuses every plugin mail as ``unavailable``, and that is what
:meth:`LoopbackPair.mail` returns. Slice 4 adds the lanes, the stores and
the in-process decoder, and the end-to-end delivery to a plugin's
``on_mail``.

One pair at a time per process: while it is open it stands in for the
core's listener pipe, the listener's event output and presence.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from domovoi.friend_houses import envelope as fe
from domovoi.friend_houses import identity as fid
from domovoi.friend_houses import listener as fl
from domovoi.friend_houses import plugin_wire as pw
from domovoi.friend_houses import session as fs

_EPOCH_A = b"\x0a" * fid.EPOCH_BYTES
_EPOCH_B = b"\x0b" * fid.EPOCH_BYTES


class _Pipe:
    """The listener process's stdin as core's service writes to it."""

    def __init__(self, state: fl.State) -> None:
        self.returncode = None
        self.stdin = SimpleNamespace(write=lambda data: state.answer(data.decode("ascii")),
                                     drain=self._drain)

    @staticmethod
    async def _drain() -> None:
        return None


class LoopbackPair:
    """``async with LoopbackPair(tmp_path, apps=("games",)) as pair:``

    ``apps`` are the slugs B's listener lets A mail (``friends.json``)."""

    def __init__(self, folder: Path, *, apps: tuple[str, ...] = ()) -> None:
        self.folder = Path(folder)
        self.apps = tuple(apps)
        self.a = fid.create(self.folder / "a" / "house-keys.json")
        self.b = fid.create(self.folder / "b" / "house-keys.json")
        self.events: list[dict[str, Any]] = []
        self._seq = 0
        self._saved: dict[str, Any] = {}
        self._server: asyncio.base_events.Server | None = None
        self.port = 0
        self.state: fl.State | None = None

    def _hello(self, keys: fid.HouseKeys, epoch: bytes) -> fs.Hello:
        return fs.Hello(keys.address_statement(bytes(32), 1_800_000_000), epoch)

    async def __aenter__(self) -> "LoopbackPair":
        from domovoi.friend_houses import presence
        from domovoi.friend_houses import service as svc

        lf = self.folder / "b" / "listener"
        lf.mkdir(parents=True, exist_ok=True)
        (lf / fl.HELLO_FILE).write_bytes(self._hello(self.b, _EPOCH_B).to_bytes())
        path = lf / fl.FRIENDS_FILE
        path.write_text(json.dumps({self.a.session_key.hex(): {
            "house": self.a.house_key.hex(), "pending": False, "apps": list(self.apps)}}),
            encoding="utf-8")
        st = path.stat()
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
        self.state = fl.State(lf, self.b)

        async def no_presence(house, statement=None):
            return None

        def emit(event: str, **fields: str) -> None:
            record = {"event": event, **fields}
            self.events.append(record)
            svc.handle_event(record)

        self._saved = {"emit": fl.emit, "saw": presence.saw, "proc": svc._LISTENER["proc"]}
        fl.emit = emit
        presence.saw = no_presence
        svc._LISTENER["proc"] = _Pipe(self.state)
        state = self.state
        self._server = await asyncio.start_server(lambda r, w: fl.handle(r, w, state), "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc: object) -> None:
        from domovoi.friend_houses import presence
        from domovoi.friend_houses import service as svc

        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        await asyncio.gather(*list(svc._REFUSALS), return_exceptions=True)
        fl.emit = self._saved["emit"]
        presence.saw = self._saved["saw"]
        svc._LISTENER["proc"] = self._saved["proc"]
        pw.KNOWN.forget(self.a.house_key)
        pw.KNOWN.forget(self.b.house_key)

    async def dial(self) -> fs.Session:
        """A's session to B (the caller closes it)."""
        r, w = await asyncio.open_connection("127.0.0.1", self.port)
        return await fs.initiate(r, w, keys=self.a, me=self._hello(self.a, _EPOCH_A),
                                 friend=fs.Friend(self.b.house_key, self.b.session_key))

    async def offer_caps(self, slugs: tuple[str, ...] = ()) -> pw.Offer:
        """A tells B its apps (CAPS, then the PING probe)."""

        async def saw(house, statement):
            return None

        sess = await self.dial()
        try:
            return await pw.offer(sess, pw.encode_caps(slugs), saw=saw, known=pw.Known(),
                                  deadline=5)
        finally:
            await sess.close()

    async def mail(self, slug: str, payload: bytes, *, ttl_s: int = 600,
                   caps: tuple[str, ...] | None = None) -> pw.Answer | None:
        """A sends one plugin mail to B on a fresh connection (CAPS first, as
        the sender does) and returns B's answer."""
        self._seq += 1
        env = fe.plugin_mail(self.a, to=self.b.house_key, slug=slug, payload=payload, ttl_s=ttl_s,
                             epoch=_EPOCH_A, seq=self._seq, sent_at=int(time.time()))
        sess = await self.dial()
        try:
            await sess.send(fs.Kind.CAPS, pw.encode_caps(caps if caps is not None else (slug,)))
            await sess.send(fs.Kind.MESSAGE, env.to_bytes())
            return await pw.await_answer(sess, env.msg_id, deadline=5)
        finally:
            await sess.close()


__all__ = ["LoopbackPair"]
