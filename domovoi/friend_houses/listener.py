"""The inbound side of Friend Houses, in a process of its own.

Friend Houses ("serverless messaging and friend system between households")
receives connections from friend houses through the Tor daemon, which hands
each one to a unix socket. Whatever arrives there came from the internet,
from a house this one only partly trusts, and every Domovoi runs the same
code: a defect here could spread from house to house along friendships
(threat-model finding 2). So the code that reads those bytes doesn't run in
the core process:

* it runs as ``python -I -m domovoi.friend_houses.listener``, started and
  watched by core (:mod:`domovoi.friend_houses.service`);
* it has **no database access and no network**: it reads this house's keys,
  the current friend list and its own hello from files core writes, and it
  answers on the unix socket only;
* it refuses in the cheapest order: size, then the Noise handshake, then
  "is this a current friend?", then a fixed-schema parse;
* it hands core only what survived, one JSON line per event on stdout.

The friend list is re-read whenever it changes, before each handshake, so
removing a friend takes effect on the next connection, whatever Tor still
has cached.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from domovoi.friend_houses import identity as fid
from domovoi.friend_houses import session as fs

log = logging.getLogger("domovoi.friend_houses.listener")

MAX_CONNECTIONS = 32
MAX_PER_FRIEND = 4

FRIENDS_FILE = "friends.json"     # {"<session key hex>": "<house key hex>"}
HELLO_FILE = "hello.bin"          # this house's Hello (statement + epoch)


@dataclass
class State:
    """What the listener knows, refreshed from core's files on change."""

    folder: Path
    keys: fid.HouseKeys
    _friends: dict[bytes, fs.Friend] = field(default_factory=dict)
    _friends_mtime: float | None = None
    _open: int = 0
    _per_friend: dict[bytes, int] = field(default_factory=dict)

    def friends(self) -> dict[bytes, fs.Friend]:
        path = self.folder / FRIENDS_FILE
        try:
            mtime = path.stat().st_mtime_ns
        except FileNotFoundError:
            self._friends, self._friends_mtime = {}, None
            return self._friends
        if mtime != self._friends_mtime:
            self._friends = _read_friends(path)
            self._friends_mtime = mtime
        return self._friends

    def hello(self) -> fs.Hello | None:
        try:
            return fs.Hello.parse((self.folder / HELLO_FILE).read_bytes())
        except (OSError, fs.SessionRefused):
            return None


def _read_friends(path: Path) -> dict[bytes, fs.Friend]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        out = {}
        for session_hex, house_hex in doc.items():
            session_key, house_key = bytes.fromhex(session_hex), bytes.fromhex(house_hex)
            if len(session_key) == 32 and len(house_key) == 32:
                out[session_key] = fs.Friend(house_key, session_key)
        return out
    except (OSError, ValueError, AttributeError) as e:
        # An unreadable list means nobody gets in, never everybody.
        log.error("the friend list can't be read (%s); refusing every caller", e)
        return {}


def emit(event: str, **fields: str) -> None:
    """One event for core, on stdout. Only hex strings and fixed words."""
    sys.stdout.write(json.dumps({"event": event, **fields}, separators=(",", ":")) + "\n")
    sys.stdout.flush()


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, state: State) -> None:
    """One inbound connection, start to finish. Never raises."""
    if state._open >= MAX_CONNECTIONS:
        writer.close()
        return
    state._open += 1
    caller: bytes | None = None
    try:
        hello = state.hello()
        if hello is None:
            return

        async def friend_for(session_key: bytes) -> fs.Friend | None:
            friend = state.friends().get(session_key)
            if friend is not None and state._per_friend.get(session_key, 0) >= MAX_PER_FRIEND:
                return None
            return friend

        sess = await fs.respond(reader, writer, keys=state.keys, me=hello, friend_for=friend_for)
        caller = sess.peer.statement.session_key
        state._per_friend[caller] = state._per_friend.get(caller, 0) + 1
        emit("connected", house=sess.peer_house_key.hex(),
             statement=sess.peer.statement.to_bytes().hex(), epoch=sess.peer.epoch.hex())
        while True:
            kind, body = await sess.recv()
            if kind is fs.Kind.PING:
                await sess.send(fs.Kind.PONG, body)
            elif kind is fs.Kind.IM_BACK:
                emit("im_back", house=sess.peer_house_key.hex())
            elif kind is fs.Kind.BYE:
                break
            # PONG without a PING of ours: ignored.
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


async def serve(socket_path: Path, state: State) -> None:  # pragma: no cover — Linux
    try:
        socket_path.unlink()
    except FileNotFoundError:
        pass
    server = await asyncio.start_unix_server(
        lambda r, w: handle(r, w, state), path=str(socket_path)
    )
    os.chmod(socket_path, 0o600)
    emit("listening")
    async with server:
        await server.serve_forever()


def main(argv: list[str] | None = None) -> int:  # pragma: no cover — run by core
    parser = argparse.ArgumentParser(description="Friend Houses inbound listener")
    parser.add_argument("--keys", type=Path, required=True)
    parser.add_argument("--folder", type=Path, required=True, help="friends.json and hello.bin")
    parser.add_argument("--socket", type=Path, required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    keys = fid.load(args.keys)
    if keys is None:
        log.error("no house keys at %s", args.keys)
        return 2
    asyncio.run(serve(args.socket, State(args.folder, keys)))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
