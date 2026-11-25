"""Shared pieces for the plugin mail tests (Friend Houses slice 4).

A stand-in decoder process (no Docker), a pool built on it, the home
house's fixture (keys, which plugins are "ready", the decoder, a registered
``on_mail`` handler) and helpers to write friends and send a friend's mail
straight into the receiving lane.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import struct
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest
from sqlalchemy import text

from domovoi.db.session import session_scope
from domovoi.friend_houses import envelope as fe
from domovoi.friend_houses import identity as fid
from domovoi.friend_houses import plugin_mail as pm
from domovoi.friend_houses import plugin_sender as ps
from domovoi.plugins_runtime import isolated as iso

SCHEMA = {"type": "object", "additionalProperties": {"anyOf": [
    {"type": "string"}, {"type": "integer"}, {"type": "array", "items": {"type": "string"}}]}}
EPOCH_F = b"\x0f" * 8


def frame(rid: int, status: int, data: bytes) -> bytes:
    body = struct.pack(">IB", rid, status) + data
    return struct.pack(">I", len(body)) + body


def echo(rid: int, kind: int, meta: dict, payload: bytes):
    """Answer the payload itself (it is JSON in these tests)."""
    return frame(rid, 0, payload)


class Proc:
    """A decoder process: ``answer(rid, kind, meta, payload)`` returns the
    bytes it writes, None to hang, or ``"crash"``. Says ready unless told."""

    def __init__(self, answer: Callable, *, ready: bool = True) -> None:
        self.answer = answer
        self.stdout = asyncio.StreamReader()
        if ready:
            self.stdout.feed_data(iso.READY_BYTE)
        self.returncode = None
        self.stdin = self
        self.killed = False

    def write(self, data: bytes) -> None:
        body = data[4:]
        rid, kind, mlen = struct.unpack(">IBH", body[:7])
        out = self.answer(rid, kind, json.loads(body[7:7 + mlen]) if mlen else {}, body[7 + mlen:])
        if out == "crash":
            self.returncode = 1
            self.stdout.feed_eof()
        elif out is not None:
            self.stdout.feed_data(out)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        pass

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode


async def make_pool(slug: str, answer: Callable = echo, *, ready: bool = True, timeout: float = 0.5,
                    ready_timeout: float = 0.3, schema: Any = SCHEMA) -> tuple[iso.DecoderPool, list[Proc]]:
    procs: list[Proc] = []

    async def spawn(argv):
        p = Proc(pool.answer, ready=ready)
        procs.append(p)
        return p

    async def prepare() -> str:
        return "sha256:img"

    async def gate(slug, manifest):
        return None

    pool = iso.DecoderPool(slug, install_dir=Path("."), manifest=None, spawn=spawn, prepare=prepare,
                           gate=gate, timeout=timeout, image_timeout=timeout, ready_timeout=ready_timeout,
                           host_event_sec=0.05)
    pool.answer = answer
    await pool.prepare()
    if schema is not None:
        pool.set_schema(schema)
    iso.POOLS[slug] = pool
    return pool, procs


@pytest.fixture
def fake_docker(monkeypatch):
    calls: list[tuple] = []

    async def docker(*args, timeout=60.0, stdin=None, env=None):
        calls.append(args)
        return 0, "", ""

    monkeypatch.setattr(iso, "_docker", docker)
    return calls


@pytest.fixture
async def house(db_session, tmp_path, monkeypatch, fake_docker):
    """This process's house with plugin ``games`` ready, its decoder (echo),
    and an ``on_mail`` handler that records what it got."""
    from domovoi.friend_houses import service
    from domovoi.sdk.friend_houses import FriendHousesAPI

    keys = fid.create(tmp_path / "me" / "house-keys.json")
    original = fid.load
    monkeypatch.setattr(fid, "load", lambda path=None: keys if path is None else original(path))
    states = {"games": "ready"}

    async def plugin_state(slug):
        return states.get(slug, "off")

    async def permitted():
        return sorted(s for s, v in states.items() if v != "off")

    monkeypatch.setattr(pm, "plugin_state", plugin_state)
    monkeypatch.setattr(pm, "permitted_slugs", permitted)
    monkeypatch.setattr(ps, "gate_open", lambda: False)
    files: list[dict[str, list[str]]] = []

    async def write_files(**kw):
        async with session_scope() as s:
            files.append(await pm.listener_apps(s))

    monkeypatch.setattr(service, "write_listener_files", write_files)
    async with session_scope() as s:
        await s.execute(text(
            "INSERT INTO friend_houses_state (id, epoch, marker) VALUES (1, :e, 'm') "
            "ON CONFLICT (id) DO NOTHING"), {"e": b"\x01" * 8})
    pm.install_hooks()
    delivered: list[tuple] = []
    api = FriendHousesAPI("games", permitted=True)

    async def handler(house_id, mail_id, seq, value):
        delivered.append((house_id, mail_id, seq, value))

    api.on_mail(handler, SCHEMA)
    pool, procs = await make_pool("games")
    h = SimpleNamespace(keys=keys, states=states, files=files, delivered=delivered, api=api,
                        pool=pool, procs=procs)
    try:
        yield h
    finally:
        await pm.shutdown()
        ps.stop_all()
        api.teardown()
        for slug in list(iso.POOLS):
            await iso.POOLS.pop(slug).stop()
        iso.CHARGE_SINK = None
        iso.PAUSE_LISTENERS.clear()
        iso.DOCKER_NOTICE.update(at=None, why=None)
        iso.FRIEND_APPS_CHECK = None


async def add_friend(friend_id: str = "fa", *, keys: fid.HouseKeys | None = None, apps_on: bool = True,
                     slugs: tuple[str, ...] = ("games",), tmp: Path | None = None,
                     caps: bool = False) -> fid.HouseKeys:
    """A friend house (its own keys), apps on, ``slugs`` allowed. With
    ``caps`` it has also told this house it has them (a fresh CAPS)."""
    if keys is None:
        assert tmp is not None
        keys = fid.create(tmp / friend_id / "house-keys.json")
    st = keys.address_statement(secrets.token_bytes(32), int(time.time()))
    async with session_scope() as s:
        await s.execute(text(
            "INSERT INTO friend_houses_friends (id, house_key, session_key, onion_key, statement, "
            "statement_at, reach_public, local_name, state, apps_on) VALUES "
            "(:i, :h, :k, :o, :st, :sa, :rp, :n, 'active', :on)"),
            {"i": friend_id, "h": keys.house_key, "k": keys.session_key, "o": st.onion_key,
             "st": st.to_bytes(), "sa": st.issued_at, "rp": secrets.token_bytes(32),
             "n": f"House {friend_id}", "on": apps_on})
        for slug in slugs:
            await s.execute(text(
                "INSERT INTO friend_houses_plugin_pairs (friend_id, slug, allowed, confirmed_at) "
                "VALUES (:i, :s, TRUE, now())"), {"i": friend_id, "s": slug})
        if caps:
            await s.execute(text(
                "UPDATE friend_houses_friends SET peer_proto = 2, peer_caps = :c, peer_caps_at = now(), "
                "peer_caps_epoch = :e, peer_epoch = :e WHERE id = :i"),
                {"c": list(slugs), "e": EPOCH_F, "i": friend_id})
    return keys


def inbound(friend: fid.HouseKeys, me: fid.HouseKeys, payload: Any, *, slug: str = "games",
            seq: int = 1, epoch: bytes = EPOCH_F, ttl_s: int = 600,
            hello_epoch: bytes | None = None) -> tuple[dict, fe.Envelope]:
    """A friend's plugin mail as the listener reports it."""
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    env = fe.plugin_mail(friend, to=me.house_key, slug=slug, payload=raw, ttl_s=ttl_s, epoch=epoch,
                         seq=seq, sent_at=int(time.time()))
    return ({"event": "plugin_mail", "house": friend.house_key.hex(), "envelope": env.to_bytes().hex(),
             "hello_epoch": (hello_epoch or epoch).hex()}, env)


async def settle() -> None:
    """Run the dispatcher until nothing is left running."""
    for _ in range(20):
        await pm.dispatch_once()
        running = [t for t in list(pm._RUNNING.values()) if not t.done()]
        if not running:
            return
        await asyncio.gather(*running, return_exceptions=True)


async def q(sql: str, **params) -> list[tuple]:
    async with session_scope() as s:
        res = await s.execute(text(sql), params)
        return [tuple(r) for r in res] if res.returns_rows else []
