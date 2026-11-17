"""The inbound side of Friend Houses, in a process of its own.

Friend Houses ("serverless messaging and friend system between households")
receives connections from other houses through the Tor daemon, which hands
each one to a unix socket. Whatever arrives there came from the internet,
from a house this one only partly trusts, and every Domovoi runs the same
code: a defect here could spread from house to house along friendships
(threat-model finding 2). So the code that reads those bytes doesn't run in
the core process:

* it runs as ``python -I -m domovoi.friend_houses.listener``, started and
  watched by core (:mod:`domovoi.friend_houses.service`);
* it has **no database access and no network**: it reads this house's keys
  and three small files core writes (the current friend list, its own
  hello, the open friend files), and it answers on unix sockets only;
* it refuses in the cheapest order: size, then the Noise handshake, then
  "is this a current friend (or an open invite)?", then a fixed-schema
  parse;
* it hands core only what survived, one JSON line per event on stdout.
  Core checks each event again before acting on it (an invite is used
  once, an accept must match a request this house sent).

Two sockets:

``main.sock``
    the house's own onion: friends only (:mod:`domovoi.friend_houses.session`).
    A house this one *asked* to be friends with (a pending request) may
    connect too, but only to deliver its accept.
``pairing.sock``
    the pairing onion, open while friend files are outstanding
    (:mod:`domovoi.friend_houses.pairing`): at most :data:`PAIRING_PER_HOUR`
    handshakes an hour, and only for an invite id in the open list.

The files are re-read whenever they change, before each handshake, so
removing a friend or cancelling a friend file takes effect on the next
connection, whatever Tor still has cached.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from domovoi.friend_houses import identity as fid
from domovoi.friend_houses import pairing as fp
from domovoi.friend_houses import session as fs

log = logging.getLogger("domovoi.friend_houses.listener")

MAX_CONNECTIONS = 32
MAX_PER_FRIEND = 4
PAIRING_PER_HOUR = 20

FRIENDS_FILE = "friends.json"     # {"<session key hex>": {"house": hex, "pending": bool}}
HELLO_FILE = "hello.bin"          # this house's Hello (statement + epoch)
INVITES_FILE = "invites.json"     # {"<invite id hex>": <expires, unix seconds>}


@dataclass(frozen=True)
class Peer:
    friend: fs.Friend
    pending: bool  # a house this one asked: may only deliver its accept


@dataclass
class _Cached:
    mtime: int | None = None
    value: dict = field(default_factory=dict)


@dataclass
class State:
    """What the listener knows, refreshed from core's files on change."""

    folder: Path
    keys: fid.HouseKeys
    _friends: _Cached = field(default_factory=_Cached)
    _invites: _Cached = field(default_factory=_Cached)
    _open: int = 0
    _per_friend: dict[bytes, int] = field(default_factory=dict)
    _pairing_times: deque = field(default_factory=deque)

    def _reload(self, name: str, cache: _Cached, parse) -> dict:
        path = self.folder / name
        try:
            mtime = path.stat().st_mtime_ns
        except FileNotFoundError:
            cache.mtime, cache.value = None, {}
            return cache.value
        if mtime != cache.mtime:
            cache.value, cache.mtime = parse(path), mtime
        return cache.value

    def peers(self) -> dict[bytes, Peer]:
        return self._reload(FRIENDS_FILE, self._friends, _read_friends)

    def friends(self) -> dict[bytes, fs.Friend]:
        """Current friends only (not pending)."""
        return {k: p.friend for k, p in self.peers().items() if not p.pending}

    def invite_open(self, invite_id: bytes) -> bool:
        expires = self._reload(INVITES_FILE, self._invites, _read_invites).get(invite_id)
        return expires is not None and expires > time.time()

    def hello(self) -> fs.Hello | None:
        try:
            return fs.Hello.parse((self.folder / HELLO_FILE).read_bytes())
        except (OSError, fs.SessionRefused):
            return None

    def pairing_allowed(self) -> bool:
        now = time.monotonic()
        while self._pairing_times and now - self._pairing_times[0] > 3600:
            self._pairing_times.popleft()
        if len(self._pairing_times) >= PAIRING_PER_HOUR:
            return False
        self._pairing_times.append(now)
        return True


def _read_friends(path: Path) -> dict[bytes, Peer]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        out = {}
        for session_hex, entry in doc.items():
            if isinstance(entry, str):  # F1's format: a plain house key
                entry = {"house": entry, "pending": False}
            session_key, house_key = bytes.fromhex(session_hex), bytes.fromhex(entry["house"])
            if len(session_key) == 32 and len(house_key) == 32:
                out[session_key] = Peer(fs.Friend(house_key, session_key), bool(entry.get("pending")))
        return out
    except (OSError, ValueError, AttributeError, KeyError, TypeError) as e:
        # An unreadable list means nobody gets in, never everybody.
        log.error("the friend list can't be read (%s); refusing every caller", e)
        return {}


def _read_invites(path: Path) -> dict[bytes, float]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return {bytes.fromhex(k): float(v) for k, v in doc.items() if len(k) == 32}
    except (OSError, ValueError, AttributeError, TypeError) as e:
        log.error("the open friend files can't be read (%s); refusing every request", e)
        return {}


def emit(event: str, **fields: str) -> None:
    """One event for core, on stdout. Hex strings, digits and one cleaned
    name only."""
    sys.stdout.write(json.dumps({"event": event, **fields}, separators=(",", ":")) + "\n")
    sys.stdout.flush()


# ─── the house's own onion ────────────────────────────────────────────────


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, state: State) -> None:
    """One inbound connection on main.sock, start to finish. Never raises."""
    if state._open >= MAX_CONNECTIONS:
        writer.close()
        return
    state._open += 1
    caller: bytes | None = None
    try:
        hello = state.hello()
        if hello is None:
            return
        peer: Peer | None = None

        async def friend_for(session_key: bytes) -> fs.Friend | None:
            nonlocal peer
            peer = state.peers().get(session_key)
            if peer is None or state._per_friend.get(session_key, 0) >= MAX_PER_FRIEND:
                return None
            return peer.friend

        sess = await fs.respond(reader, writer, keys=state.keys, me=hello, friend_for=friend_for)
        assert peer is not None
        caller = sess.peer.statement.session_key
        state._per_friend[caller] = state._per_friend.get(caller, 0) + 1
        house = sess.peer_house_key.hex()
        if not peer.pending:
            emit("connected", house=house, statement=sess.peer.statement.to_bytes().hex(),
                 epoch=sess.peer.epoch.hex())
        while True:
            kind, body = await sess.recv()
            if kind is fs.Kind.ACCEPT:
                emit("accepted", house=house, statement=sess.peer.statement.to_bytes().hex(),
                     reach_private=body.hex())
                await sess.send(fs.Kind.ACCEPT_OK)
            elif kind is fs.Kind.BYE:
                break
            elif peer.pending:
                break  # a pending house may only deliver its accept
            elif kind is fs.Kind.PING:
                await sess.send(fs.Kind.PONG, body)
            elif kind is fs.Kind.IM_BACK:
                emit("im_back", house=house)
            # PONG without a PING of ours, ACCEPT_OK unasked: ignored.
    except fs.SessionRefused as e:
        log.info("connection closed: %s", e)
    except Exception:  # noqa: BLE001 — one bad connection never stops the listener
        log.exception("connection failed")
    finally:
        state._open -= 1
        if caller is not None:
            state._per_friend[caller] -= 1
            if not state._per_friend[caller]:
                del state._per_friend[caller]
        writer.close()


# ─── the pairing onion ────────────────────────────────────────────────────


async def handle_pairing(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                         state: State) -> None:
    """One friend request on pairing.sock. Never raises."""
    if state._open >= MAX_CONNECTIONS or not state.pairing_allowed():
        writer.close()
        return
    state._open += 1
    try:
        async def invite_open(invite_id: bytes) -> bool:
            return state.invite_open(invite_id)

        req = await fp.answer(reader, writer, keys=state.keys, invite_open=invite_open)
        emit("request", invite=req.invite_id.hex(), statement=req.statement.to_bytes().hex(),
             reach_private=req.reach_private.hex(), name=req.name, code=req.code)
    except fp.PairingRefused as e:
        log.info("friend request refused: %s", e)
    except Exception:  # noqa: BLE001 — one bad connection never stops the listener
        log.exception("friend request failed")
    finally:
        state._open -= 1
        writer.close()


async def _serve_unix(path: Path, handler) -> asyncio.AbstractServer:  # pragma: no cover — Linux
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    server = await asyncio.start_unix_server(handler, path=str(path))
    os.chmod(path, 0o600)
    return server


async def serve(main_socket: Path, pairing_socket: Path, state: State) -> None:  # pragma: no cover
    main = await _serve_unix(main_socket, lambda r, w: handle(r, w, state))
    pairing = await _serve_unix(pairing_socket, lambda r, w: handle_pairing(r, w, state))
    emit("listening")
    async with main, pairing:
        await asyncio.gather(main.serve_forever(), pairing.serve_forever())


def main(argv: list[str] | None = None) -> int:  # pragma: no cover — run by core
    parser = argparse.ArgumentParser(description="Friend Houses inbound listener")
    parser.add_argument("--keys", type=Path, required=True)
    parser.add_argument("--folder", type=Path, required=True,
                        help="friends.json, hello.bin and invites.json")
    parser.add_argument("--socket", type=Path, required=True, help="the house's onion")
    parser.add_argument("--pairing-socket", type=Path, required=True, help="the pairing onion")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    keys = fid.load(args.keys)
    if keys is None:
        log.error("no house keys at %s", args.keys)
        return 2
    asyncio.run(serve(args.socket, args.pairing_socket, State(args.folder, keys)))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
