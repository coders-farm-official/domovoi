"""``LoopbackPair``: two houses over loopback, for plugin mail tests (SDK 1.18).

The test kit the design note ``FRIEND-HOUSES-PLUGINS`` §4 names. House B's
real listener (:func:`domovoi.friend_houses.listener.handle`) serves on
``127.0.0.1`` and house A dials it with a real Noise session. No Tor, no
Docker.

One of the two is **this process's house** (``home``), with its real
plugin mail service and the test database:

* ``home="b"`` (the default): B receives. B's listener reports to B's real
  core (:func:`domovoi.friend_houses.service.handle_event`, so the plugin
  mail lane, the stores and the dispatcher), which answers on the
  listener's "stdin". A is scripted: :meth:`LoopbackPair.mail` sends one
  envelope the way the sender does (CAPS first) and returns B's answer.
* ``home="a"``: A sends. This process's sender
  (:mod:`domovoi.friend_houses.plugin_sender`) dials B's listener through
  :data:`~domovoi.friend_houses.plugin_sender.DIAL`; B's core is scripted by
  ``core(event) -> line`` (default: store everything).

While it is open it stands in for the core's listener pipe, the listener's
event output, presence and ``identity.load`` (the home house's keys). One
pair at a time per process. :meth:`LoopbackPair.befriend` writes the other
house as a friend in the home house's database, apps on and ``apps``
allowed (the caller's test owns the database).
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from domovoi.friend_houses import envelope as fe
from domovoi.friend_houses import identity as fid
from domovoi.friend_houses import listener as fl
from domovoi.friend_houses import plugin_wire as pw
from domovoi.friend_houses import session as fs

EPOCH_A = b"\x0a" * fid.EPOCH_BYTES
EPOCH_B = b"\x0b" * fid.EPOCH_BYTES


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

    def __init__(self, folder: Path, *, apps: tuple[str, ...] = (), home: str = "b",
                 core: Callable[[dict[str, Any]], str | None] | None = None) -> None:
        if home not in ("a", "b"):
            raise ValueError("home is 'a' or 'b'")
        self.folder = Path(folder)
        self.apps = tuple(apps)
        self.home = home
        self.core = core
        self.a = fid.create(self.folder / "a" / "house-keys.json")
        self.b = fid.create(self.folder / "b" / "house-keys.json")
        self.epoch_a = EPOCH_A
        self.events: list[dict[str, Any]] = []
        self._seq = 0
        self._saved: dict[str, Any] = {}
        self._server: asyncio.base_events.Server | None = None
        self.port = 0
        self.state: fl.State | None = None
        self.friend_id: str | None = None

    def _hello(self, keys: fid.HouseKeys, epoch: bytes) -> fs.Hello:
        return fs.Hello(keys.address_statement(bytes(32), 1_800_000_000), epoch)

    def set_listener_apps(self, apps: tuple[str, ...]) -> None:
        """Rewrite B's ``friends.json`` (what B's listener lets A mail)."""
        self.apps = tuple(apps)
        assert self.state is not None
        path = self.state.folder / fl.FRIENDS_FILE
        path.write_text(json.dumps({self.a.session_key.hex(): {
            "house": self.a.house_key.hex(), "pending": False, "apps": sorted(self.apps)}}),
            encoding="utf-8")
        st = path.stat()
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + secrets.randbelow(10**6) + 1_000_000))

    async def __aenter__(self) -> "LoopbackPair":
        from domovoi.friend_houses import plugin_sender, presence
        from domovoi.friend_houses import service as svc

        lf = self.folder / "b" / "listener"
        lf.mkdir(parents=True, exist_ok=True)
        (lf / fl.HELLO_FILE).write_bytes(self._hello(self.b, EPOCH_B).to_bytes())
        self.state = fl.State(lf, self.b)
        self.set_listener_apps(self.apps)
        home_keys = self.b if self.home == "b" else self.a

        async def no_presence(house, statement=None):
            return None

        def emit(event: str, **fields: str) -> None:
            record = {"event": event, **fields}
            self.events.append(record)
            if self.home == "b":
                svc.handle_event(record)
            elif event == "plugin_mail":
                env = fe.peek(bytes.fromhex(record["envelope"]))
                line = (self.core or (lambda e: f"stored {env.msg_id.hex()}"))(record)
                if line:
                    # Core answers later on its pipe, never inside emit.
                    asyncio.get_running_loop().call_soon(self.state.answer, line)

        original_load = fid.load

        def load(path=None):
            return home_keys if path is None else original_load(path)

        async def dial(friend):
            return await self.dial()

        self._saved = {"emit": fl.emit, "saw": presence.saw, "proc": svc._LISTENER["proc"],
                       "load": fid.load, "dial": plugin_sender.DIAL, "hello": svc._STATE.get("hello")}
        fl.emit = emit
        presence.saw = no_presence
        fid.load = load
        if self.home == "b":
            svc._LISTENER["proc"] = _Pipe(self.state)
        else:
            plugin_sender.DIAL = dial
            svc._STATE["hello"] = self._hello(self.a, self.epoch_a)
        state = self.state
        self._server = await asyncio.start_server(lambda r, w: fl.handle(r, w, state), "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc: object) -> None:
        from domovoi.friend_houses import plugin_mail, plugin_sender, presence
        from domovoi.friend_houses import service as svc

        plugin_sender.stop_all()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        await plugin_mail.shutdown()
        fl.emit = self._saved["emit"]
        presence.saw = self._saved["saw"]
        fid.load = self._saved["load"]
        plugin_sender.DIAL = self._saved["dial"]
        svc._LISTENER["proc"] = self._saved["proc"]
        svc._STATE["hello"] = self._saved["hello"]
        pw.KNOWN.forget(self.a.house_key)
        pw.KNOWN.forget(self.b.house_key)

    async def befriend(self, *, apps: tuple[str, ...] | None = None, friend_id: str = "fa") -> str:
        """The other house as a friend of the home house, in the test
        database: active, "Apps with this friend" on, ``apps`` allowed. For
        ``home="a"`` the friend (B) has also sent its CAPS listing them."""
        from sqlalchemy import text

        from domovoi.db.session import session_scope

        other = self.a if self.home == "b" else self.b
        apps = self.apps if apps is None else tuple(apps)
        statement = other.address_statement(secrets.token_bytes(32), int(time.time()))
        async with session_scope() as s:
            await s.execute(text(
                "INSERT INTO friend_houses_friends (id, house_key, session_key, onion_key, statement, "
                "statement_at, reach_public, local_name, state, apps_on, apps_on_at) VALUES "
                "(:i, :h, :s, :o, :st, :sa, :rp, 'Sam house', 'active', TRUE, now())"),
                {"i": friend_id, "h": other.house_key, "s": other.session_key,
                 "o": statement.onion_key, "st": statement.to_bytes(), "sa": statement.issued_at,
                 "rp": secrets.token_bytes(32)})
            for slug in apps:
                await s.execute(text(
                    "INSERT INTO friend_houses_plugin_pairs (friend_id, slug, allowed, confirmed_at) "
                    "VALUES (:i, :s, TRUE, now())"), {"i": friend_id, "s": slug})
            if self.home == "a":
                await s.execute(text(
                    "UPDATE friend_houses_friends SET peer_proto = 2, peer_caps = :c, peer_caps_at = now(), "
                    "peer_caps_epoch = :e, peer_epoch = :e WHERE id = :i"),
                    {"c": list(apps), "e": EPOCH_B, "i": friend_id})
            row = (await s.execute(text("SELECT 1 FROM friend_houses_state WHERE id = 1"))).first()
            if row is None:
                await s.execute(text(
                    "INSERT INTO friend_houses_state (id, epoch, marker) VALUES (1, :e, 'loopback')"),
                    {"e": self.epoch_a if self.home == "a" else EPOCH_B})
        self.friend_id = friend_id
        return friend_id

    async def dial(self) -> fs.Session:
        """A's session to B (the caller closes it)."""
        r, w = await asyncio.open_connection("127.0.0.1", self.port)
        return await fs.initiate(r, w, keys=self.a, me=self._hello(self.a, self.epoch_a),
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

    def envelope(self, slug: str, payload: bytes, *, ttl_s: int = 600, seq: int | None = None,
                 epoch: bytes | None = None) -> fe.Envelope:
        """A's signed plugin mail to B (seq counts up unless given)."""
        if seq is None:
            self._seq += 1
            seq = self._seq
        return fe.plugin_mail(self.a, to=self.b.house_key, slug=slug, payload=payload, ttl_s=ttl_s,
                              epoch=epoch or self.epoch_a, seq=seq, sent_at=int(time.time()))

    async def mail(self, slug: str, payload: bytes, *, ttl_s: int = 600,
                   caps: tuple[str, ...] | None = None, seq: int | None = None,
                   env: fe.Envelope | None = None, deadline: float = 25) -> pw.Answer | None:
        """A sends one plugin mail to B on a fresh connection (CAPS first, as
        the sender does) and returns B's answer."""
        env = env or self.envelope(slug, payload, ttl_s=ttl_s, seq=seq)
        sess = await self.dial()
        try:
            await sess.send(fs.Kind.CAPS, pw.encode_caps(caps if caps is not None else (slug,)))
            await sess.send(fs.Kind.MESSAGE, env.to_bytes())
            return await pw.await_answer(sess, env.msg_id, deadline=deadline)
        finally:
            await sess.close()


__all__ = ["EPOCH_A", "EPOCH_B", "LoopbackPair"]
