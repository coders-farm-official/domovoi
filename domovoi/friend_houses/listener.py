"""The inbound side of Friend Houses, in a process of its own.

Friend Houses ("serverless messaging and friend system between households")
receives connections from other houses through the Tor daemon, which hands
each one to a unix socket. Whatever arrives there came from the internet,
from a house this one only partly trusts, and every Domovoi runs the same
code: a defect here could spread from house to house along friendships
(threat-model finding 2). So the code that reads those bytes doesn't run in
the core process:

* it runs in a container of its own (:mod:`domovoi.friend_houses.listener_runtime`):
  no network at all, a read-only root, no capabilities, and only this
  house's keys and three small files core writes (the current friend list,
  its own hello, the open friend files) mounted read-only, plus the folder
  its unix sockets live in (audit R6 FH-12). Core starts and watches it
  (:mod:`domovoi.friend_houses.service`) and talks to it over stdin and
  stdout; when stdin closes (core went away) it stops;
* it has **no database access and no network**: it answers on unix
  sockets only;
* it refuses in the cheapest order: size, then the Noise handshake, then
  "is this a current friend (or an open invite)?", then a fixed-schema
  parse;
* it hands core only what survived, one JSON line per event on stdout.
  Core checks each event again before acting on it (an invite is used
  once, an accept must match a request this house sent).

Plugin mail (1.5.0)
-------------------
Two frames are no longer fixed-size: a friend's ``CAPS`` and a plugin mail
envelope (``KIND_PLUGIN``). The listener parses CAPS (bounded, exact
length) and, of a plugin mail, only what it must to sort it: the kind byte
and the app's slug. **The payload stays opaque here**; it is checked only
by its signature. In order, for a ``MESSAGE`` frame:

1. size (the session's frame cap; plugin mail has its own, smaller one);
2. peek the envelope's kind (:func:`envelope.peek`);
3. plugin mail: only on a connection whose caller sent a CAPS with
   ``proto >= 2`` first (else the connection closes), and only for an app
   in this friend's ``apps`` list in ``friends.json``: an unlisted one gets
   ``MESSAGE_NACK not_allowed`` and **spends no budget**;
4. the plugin buckets (:mod:`domovoi.friend_houses.budget`: per friend and
   app, per friend, per friend in bytes, per house, per house in bytes) or
   ``MESSAGE_NACK rate`` (a house bucket answers a fixed 60 s, L7);
   people's 60 an hour is spent only on text and list changes;
5. the full parse and the signature;
6. a ``plugin_mail`` event for core (never ``message``), then the ACK or
   ``MESSAGE_NACK`` with core's reason (``refused <id> <reason> <secs>``).

Core waits at most :data:`PLUGIN_STORE_WAIT_SEC` (20 s, under the sender's
30, L10); after :data:`NOT_ALLOWED_PER_CONNECTION` refusals of unlisted
apps the connection is closed (L9). A CAPS is reported to core whenever it
differs from the last one reported for that friend; only a repeat spends
the CAPS budget (L8).

Three sockets:

``main.sock``
    the house's own onion: friends only (:mod:`domovoi.friend_houses.session`).
    A house this one *asked* to be friends with (a pending request) may
    connect too, but only to deliver its accept.
``pairing.sock``
    the pairing onion, open while friend files are outstanding
    (:mod:`domovoi.friend_houses.pairing`): at most :data:`PAIRING_PER_HOUR`
    handshakes an hour, and only for an invite id in the open list.
``away.sock``
    away chat's onion (1.5.0): the household's own phones, each a key in
    ``phones.json`` (written by core, read-only here), never a friend
    (:mod:`domovoi.away.listener`). Its Noise key is the house's away key,
    not the session key, and it is served whether or not Friend Houses is
    on: the house keys and the two friends' sockets are only given when it
    is (``--keys``).

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
from typing import Any

from domovoi.friend_houses import budget as fb
from domovoi.friend_houses import envelope as fe
from domovoi.friend_houses import identity as fid
from domovoi.friend_houses import pairing as fp
from domovoi.friend_houses import plugin_wire as pw
from domovoi.friend_houses import session as fs

log = logging.getLogger("domovoi.friend_houses.listener")

MAX_CONNECTIONS = 32       # on main.sock
MAX_PAIRING_CONNECTIONS = 8  # on pairing.sock: its own slots (audit R6 FH-9)
MAX_PER_FRIEND = 4
# Connections still before their handshake (main.sock) or before naming an
# open friend file (pairing.sock). When these are full, the oldest is
# closed to make room, so callers that say nothing can't hold every slot.
MAX_HANDSHAKING = 8
MAX_PAIRING_HANDSHAKING = 4
PINGS_PER_CONNECTION = 16  # a house pings once per check; more is closed
# Frames that ask nothing of us (a PONG we didn't ask for, "I'm back" again,
# an ACCEPT_OK unasked): a few are harmless, a stream of them is closed.
IDLE_FRAMES_PER_CONNECTION = 16
PAIRING_PER_HOUR = 20      # requests that name one open friend file, per file
MESSAGES_PER_HOUR = 60     # per friend (threat-model finding 32)
EVENTS_PER_HOUR = 120      # per house: connected / I'm back / accepted (R6 FH-3)
HANDSHAKE_DEADLINE_SEC = 20.0  # preface + handshake, all of it (R6 FH-9)
PAIRING_DEADLINE_SEC = 90.0    # a whole friend request, round trips over Tor
STORE_WAIT_SEC = 30.0      # how long a message waits for core to store it
# A plugin mail waits less than its sender (30 s), so core's "unavailable"
# reaches the sender before it gives up (L10).
PLUGIN_STORE_WAIT_SEC = 20.0
# Refusals of unlisted apps on one connection before it is closed (L9).
NOT_ALLOWED_PER_CONNECTION = 16
# A plugin mail core didn't answer in time is refused as "unavailable".
UNANSWERED_RETRY_SEC = 10

# {"<session key hex>": {"house": hex, "pending": bool, "apps": [slug, ...]}}
# "apps": the plugins this friend may mail (an admin's "Apps with this
# friend" and each app allowed); missing or unreadable means none.
FRIENDS_FILE = "friends.json"
HELLO_FILE = "hello.bin"          # this house's Hello (statement + epoch)
INVITES_FILE = "invites.json"     # {"<invite id hex>": <expires, unix seconds>}

# core's answer words for a refused plugin mail -> the NACK reason
_REASONS = {"full": fs.Nack.FULL, "not_allowed": fs.Nack.NOT_ALLOWED, "rate": fs.Nack.RATE,
            "unavailable": fs.Nack.UNAVAILABLE}


@dataclass(frozen=True)
class Peer:
    friend: fs.Friend
    pending: bool  # a house this one asked: may only deliver its accept
    # The apps this friend may mail. Part of the equality the per-frame
    # check uses, so any change closes the friend's connection (§6.8).
    apps: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Answer:
    """Core's answer about one envelope."""

    stored: bool
    reason: fs.Nack = fs.Nack.UNAVAILABLE
    retry_after: int = UNANSWERED_RETRY_SEC


@dataclass
class _Cached:
    mtime: int | None = None
    value: dict = field(default_factory=dict)


@dataclass
class State:
    """What the listener knows, refreshed from core's files on change."""

    folder: Path
    keys: fid.HouseKeys | None  # None: away chat only (Friend Houses is off)
    _friends: _Cached = field(default_factory=_Cached)
    _invites: _Cached = field(default_factory=_Cached)
    _open: int = 0
    _open_pairing: int = 0
    _handshaking: deque = field(default_factory=deque)          # main.sock writers
    _pairing_handshaking: deque = field(default_factory=deque)  # pairing.sock writers
    _per_friend: dict[bytes, int] = field(default_factory=dict)
    _event_times: dict[bytes, deque] = field(default_factory=dict)
    _pairing_times: dict[bytes, deque] = field(default_factory=dict)
    # invite id -> runs broken off after this house's nonce went out, plus
    # runs still going (each holds its place until it ends)
    _abandoned: dict[bytes, int] = field(default_factory=dict)
    _closed_said: set = field(default_factory=set)
    _message_times: dict[bytes, deque] = field(default_factory=dict)
    _caps_times: dict[bytes, deque] = field(default_factory=dict)
    # house -> (proto, slugs) of the last CAPS reported to core (L8)
    _caps_reported: dict[bytes, tuple[int, tuple[str, ...]]] = field(default_factory=dict)
    # Plugin mail's own buckets, separate from people's (review D7).
    budgets: fb.PluginBudgets = field(default_factory=fb.PluginBudgets)
    # msg_id -> the future core's "stored"/"refused" line resolves
    _waiting: dict[bytes, asyncio.Future] = field(default_factory=dict)

    def message_allowed(self, house: bytes) -> bool:
        """People's budget: text and list changes only, never plugin mail."""
        return _within(self._message_times.setdefault(house, deque()), MESSAGES_PER_HOUR)

    def caps_allowed(self, house: bytes) -> bool:
        """CAPS events have their own budget, not the shared 120."""
        return _within(self._caps_times.setdefault(house, deque()), pw.CAPS_PER_HOUR)

    def caps_reportable(self, house: bytes, caps: pw.Caps) -> bool:
        """L8: a CAPS that differs from the last one reported for this
        friend is always reported; a repeat only within its budget."""
        content = (caps.proto, tuple(caps.slugs))
        if self._caps_reported.get(house) != content:
            if house not in self._caps_reported and len(self._caps_reported) >= 4 * MAX_CONNECTIONS:
                self._caps_reported.pop(next(iter(self._caps_reported)))
            self._caps_reported[house] = content
            return True
        return self.caps_allowed(house)

    def event_allowed(self, house: bytes) -> bool:
        """Whether core hears about one more connected / I'm back / accept
        from this house this hour. Each becomes a task in core with its own
        database connection, so none of them is unbounded (R6 FH-3)."""
        return _within(self._event_times.setdefault(house, deque()), EVENTS_PER_HOUR)

    async def stored(self, msg_id: bytes) -> bool:
        """Wait for core to say it stored (True) or refused (False) a
        message. No answer in time counts as refused: the sender keeps it
        and tries again later, so nothing is lost by saying no."""
        return (await self.answer_for(msg_id)).stored

    async def answer_for(self, msg_id: bytes, wait: float | None = None) -> Answer:
        """Core's whole answer; no answer in time is ``unavailable``."""
        if msg_id in self._waiting:  # the same id twice at once: refuse the second
            return Answer(False)
        fut = asyncio.get_running_loop().create_future()
        self._waiting[msg_id] = fut
        try:
            return await asyncio.wait_for(fut, STORE_WAIT_SEC if wait is None else wait)
        except asyncio.TimeoutError:
            return Answer(False)
        finally:
            self._waiting.pop(msg_id, None)

    def answer(self, line: str) -> None:
        """One line from core on stdin: ``stored <hex>``, ``refused <hex>``
        or ``refused <hex> <reason> <secs>`` (plugin mail)."""
        parts = line.strip().split()
        if len(parts) not in (2, 4) or parts[0] not in ("stored", "refused"):
            return
        if len(parts) == 4 and (parts[0] != "refused" or parts[2] not in _REASONS
                                or not parts[3].isdigit() or len(parts[3]) > 6):
            return
        try:
            msg_id = bytes.fromhex(parts[1])
        except ValueError:
            return
        fut = self._waiting.get(msg_id)
        if fut is None or fut.done():
            return
        if parts[0] == "stored":
            fut.set_result(Answer(True))
        elif len(parts) == 4:
            fut.set_result(Answer(False, _REASONS[parts[2]], int(parts[3])))
        else:
            fut.set_result(Answer(False))

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
        if self._abandoned.get(invite_id, 0) >= fp.ABANDONED_MAX:
            return False  # closed here; core closes it for good
        expires = self._reload(INVITES_FILE, self._invites, _read_invites).get(invite_id)
        return expires is not None and expires > time.time()

    def hello(self) -> fs.Hello | None:
        try:
            return fs.Hello.parse((self.folder / HELLO_FILE).read_bytes())
        except (OSError, fs.SessionRefused):
            return None

    def pairing_allowed(self, invite_id: bytes) -> bool:
        """Per friend file, so the holder of one file cannot spend the
        budget of the others."""
        return _within(self._pairing_times.setdefault(invite_id, deque()), PAIRING_PER_HOUR)

    def start_try(self, invite_id: bytes) -> bool:
        """Whether a run on this friend file may go on, holding its place
        among the file's tries until it ends (:meth:`end_try`). Counted
        before the nonce can go out, with no await between the check and
        the count, so runs in parallel cannot each find a try left."""
        if not (self.invite_open(invite_id) and self.pairing_allowed(invite_id)):
            return False
        self._abandoned[invite_id] = self._abandoned.get(invite_id, 0) + 1
        return True

    def end_try(self, invite_id: bytes, *, counts: bool) -> bool:
        """A run on ``invite_id`` ended. One that finished, or ended before
        this house's nonce went out, gives its place back; one broken off
        after it keeps it. True the first time the file runs out of tries."""
        if not counts:
            left = self._abandoned.get(invite_id, 1) - 1
            if left > 0:
                self._abandoned[invite_id] = left
            else:
                self._abandoned.pop(invite_id, None)
            return False
        if self._abandoned.get(invite_id, 0) >= fp.ABANDONED_MAX and invite_id not in self._closed_said:
            self._closed_said.add(invite_id)
            return True
        return False


def _admit(open_now: int, max_open: int, waiting: deque, max_waiting: int) -> bool:
    """Whether a new connection gets a slot. When every slot is taken, or
    too many are still before their handshake, the oldest of those is
    closed to make room (its handler sees its stream end and gives the slot
    back); with every slot past its handshake, the newcomer is refused."""
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


def _within(times: deque, limit: int, window: float = 3600.0) -> bool:
    now = time.monotonic()
    while times and now - times[0] > window:
        times.popleft()
    if len(times) >= limit:
        return False
    times.append(now)
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
                pending = bool(entry.get("pending"))
                apps = frozenset() if pending else _read_apps(entry.get("apps"))
                out[session_key] = Peer(fs.Friend(house_key, session_key), pending, apps)
        return out
    except (OSError, ValueError, AttributeError, KeyError, TypeError, RecursionError) as e:
        # An unreadable list means nobody gets in, never everybody.
        log.error("the friend list can't be read (%s); refusing every caller", e)
        return {}


def _read_apps(raw: object) -> frozenset[str]:
    """A friend entry's ``apps``: a list of at most 32 plugin slugs by the
    manifest's rule. Anything else means no apps for that friend (its
    people's messages are unaffected): a bad list never opens anything."""
    if raw is None:
        return frozenset()
    if not isinstance(raw, list) or len(raw) > pw.MAX_SLUGS or \
            not all(fe.valid_slug(s) for s in raw):
        log.error("a friend's app list can't be read; no apps for that friend")
        return frozenset()
    return frozenset(raw)


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
    if not _admit(state._open, MAX_CONNECTIONS, state._handshaking, MAX_HANDSHAKING):
        writer.close()
        return
    state._open += 1
    state._handshaking.append(writer)
    caller: bytes | None = None
    pings = 0
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

        # One deadline for the preface and the whole handshake: a caller
        # that connects and says nothing gives its slot back soon (R6 FH-9).
        sess = await asyncio.wait_for(
            fs.respond(reader, writer, keys=state.keys, me=hello, friend_for=friend_for),
            HANDSHAKE_DEADLINE_SEC)
        _done_waiting(state._handshaking, writer)
        assert peer is not None
        caller = sess.peer.statement.session_key
        state._per_friend[caller] = state._per_friend.get(caller, 0) + 1
        house = sess.peer_house_key.hex()
        house_key = sess.peer_house_key
        if not peer.pending and state.event_allowed(house_key):
            emit("connected", house=house, statement=sess.peer.statement.to_bytes().hex(),
                 epoch=sess.peer.epoch.hex())
        said_back = False
        caps_seen = False
        refusals = [0]  # not_allowed refusals on this connection (L9)
        proto: int | None = None  # what the caller's CAPS said, on THIS connection
        idle = 0
        while True:
            kind, body = await sess.recv()
            # The friend list is checked on every frame, not just at the
            # handshake: a friend removed (or blocked) mid-session is cut
            # off at its next frame (R6 FH-10).
            if state.peers().get(caller) != peer:
                break
            if kind is fs.Kind.ACCEPT:
                if state.event_allowed(house_key):
                    emit("accepted", house=house, statement=sess.peer.statement.to_bytes().hex(),
                         reach_private=body.hex())
                await sess.send(fs.Kind.ACCEPT_OK)
                break  # one accept per connection (R6 FH-3)
            elif kind is fs.Kind.BYE:
                break
            elif peer.pending:
                break  # a pending house may only deliver its accept
            elif kind is fs.Kind.PING:
                pings += 1
                if pings > PINGS_PER_CONNECTION:
                    break
                await sess.send(fs.Kind.PONG, body)
            elif kind is fs.Kind.IM_BACK and not said_back:
                if state.event_allowed(house_key):
                    emit("im_back", house=house)
                said_back = True  # once per connection
            elif kind is fs.Kind.CAPS:
                # After the pending check above; one per connection (D6, D14).
                if caps_seen:
                    break
                caps_seen = True
                caps = pw.parse_caps(body)  # BadCaps closes the connection
                proto = caps.proto
                if state.caps_reportable(house_key, caps):  # a repeat over its budget: not reported
                    emit("caps", house=house, proto=str(caps.proto), apps=",".join(caps.slugs),
                         epoch=sess.peer.epoch.hex())
            elif kind is fs.Kind.MESSAGE:
                head = fe.peek(body)  # BadEnvelope closes the connection
                if head.kind == fe.KIND_PLUGIN:
                    if proto is None or proto < fs.PROTO:
                        break  # plugin mail only after a CAPS on this connection
                    await _plugin_mail(sess, state, peer, head, body, refusals)
                    if refusals[0] >= NOT_ALLOWED_PER_CONNECTION:
                        break  # L9: a friend looping unlisted apps is cut off
                    continue
                if not state.message_allowed(sess.peer_house_key):
                    break  # over the hourly limit: the sender keeps it for later
                env = fe.parse(body, sender=sess.peer_house_key, recipient=state.keys.house_key)
                if env.kind not in fe.PEOPLE_KINDS:
                    break  # never: peek sorted it; checked again all the same
                emit("message", house=house, envelope=env.to_bytes().hex())
                if await state.stored(env.msg_id):
                    await sess.send(fs.Kind.MESSAGE_ACK, env.msg_id)
            else:
                # PONG without a PING of ours, "I'm back" again, ACCEPT_OK or
                # MESSAGE_NACK unasked: ignored, up to a point.
                idle += 1
                if idle > IDLE_FRAMES_PER_CONNECTION:
                    break
    except (fs.SessionRefused, fe.BadEnvelope, pw.BadCaps, asyncio.TimeoutError) as e:
        log.info("connection closed: %s", e)
    except Exception:  # noqa: BLE001 — one bad connection never stops the listener
        log.exception("connection failed")
    finally:
        state._open -= 1
        _done_waiting(state._handshaking, writer)
        if caller is not None:
            state._per_friend[caller] -= 1
            if not state._per_friend[caller]:
                del state._per_friend[caller]
        writer.close()


async def _plugin_mail(sess: fs.Session, state: State, peer: Peer, head: fe.Peek,
                       body: bytes, refusals: list[int] | None = None) -> None:
    """One plugin mail, in the order in the module docstring (steps 3-6).
    Its payload is never looked at here."""
    house_key = sess.peer_house_key
    assert head.slug is not None
    if head.slug not in peer.apps:
        # Unlisted: refused before any bucket is spent (review D7).
        if refusals is not None:
            refusals[0] += 1
        await sess.send(fs.Kind.MESSAGE_NACK, fs.nack_body(head.msg_id, fs.Nack.NOT_ALLOWED, 0))
        return
    wait = state.budgets.spend(house_key, head.slug, len(body))
    if wait:
        await sess.send(fs.Kind.MESSAGE_NACK, fs.nack_body(head.msg_id, fs.Nack.RATE, wait))
        return
    env = fe.parse(body, sender=house_key, recipient=state.keys.house_key)
    emit("plugin_mail", house=house_key.hex(), envelope=env.to_bytes().hex(),
         hello_epoch=sess.peer.epoch.hex())
    answer = await state.answer_for(env.msg_id, PLUGIN_STORE_WAIT_SEC)
    if answer.stored:
        await sess.send(fs.Kind.MESSAGE_ACK, env.msg_id)
    else:
        if refusals is not None and answer.reason is fs.Nack.NOT_ALLOWED:
            refusals[0] += 1
        await sess.send(fs.Kind.MESSAGE_NACK,
                        fs.nack_body(env.msg_id, answer.reason, answer.retry_after))


# ─── the pairing onion ────────────────────────────────────────────────────


async def handle_pairing(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                         state: State) -> None:
    """One friend request on pairing.sock. Never raises.

    Its connections have their own slots, separate from friends' (R6 FH-9),
    and only a handshake that names an open friend file spends the hourly
    budget: empty connections from anyone who once saw a file don't."""
    if not _admit(state._open_pairing, MAX_PAIRING_CONNECTIONS,
                  state._pairing_handshaking, MAX_PAIRING_HANDSHAKING):
        writer.close()
        return
    state._open_pairing += 1
    state._pairing_handshaking.append(writer)
    revealed: list[bytes] = []
    started: list[bytes] = []
    done = False

    def nonce_sent(invite_id: bytes) -> None:
        # It named an open friend file: no longer one that can be closed
        # to make room.
        _done_waiting(state._pairing_handshaking, writer)
        revealed.append(invite_id)

    try:
        async def invite_open(invite_id: bytes) -> bool:
            if started or not state.start_try(invite_id):
                return False
            started.append(invite_id)
            return True

        req = await asyncio.wait_for(
            fp.answer(reader, writer, keys=state.keys, invite_open=invite_open,
                      nonce_sent=nonce_sent),
            PAIRING_DEADLINE_SEC)
        done = True
        emit("request", invite=req.invite_id.hex(), statement=req.statement.to_bytes().hex(),
             reach_private=req.reach_private.hex(), name=req.name, code=req.code)
    except fp.PairingRefused as e:
        log.info("friend request refused: %s", e)
    except asyncio.TimeoutError:
        log.info("friend request took too long")
    except Exception:  # noqa: BLE001 — one bad connection never stops the listener
        log.exception("friend request failed")
    finally:
        state._open_pairing -= 1
        _done_waiting(state._pairing_handshaking, writer)
        if started:
            # A run broken off after the nonce went out is one try at the
            # code: a file allows only a few, then it is closed.
            invite_id = started[0]
            if state.end_try(invite_id, counts=bool(revealed) and not done):
                emit("invite_closed", invite=invite_id.hex())
        writer.close()


async def _serve_unix(path: Path, handler) -> asyncio.AbstractServer:  # pragma: no cover — Linux
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    server = await asyncio.start_unix_server(handler, path=str(path))
    os.chmod(path, 0o600)
    return server


#: The longest line core writes on stdin: an away answer (a RESP of up to
#: 1 MiB of JSON, escaped to ASCII) and room. A longer one is dropped.
CORE_LINE_MAX = 8 * 1024 * 1024


async def read_answers(reader: asyncio.StreamReader, state: State, away: Any = None) -> None:
    """Core's answers, one line each, until stdin closes: about messages
    (short ASCII lines, :meth:`State.answer`) and, starting ``away ``,
    away chat's answers (:meth:`domovoi.away.listener.AwayState.from_core`)."""
    while True:
        try:
            line = await reader.readline()
        except ValueError:  # a line over the reader's limit: dropped, never fatal
            continue
        if not line:
            return
        if line.startswith(b"away "):
            if away is not None:
                away.from_core(line[5:])
            continue
        state.answer(line.decode("ascii", errors="ignore")[:80])


async def serve(main_socket: Path | None, pairing_socket: Path | None, state: State,
                away_socket: Path | None = None, away: Any = None) -> None:  # pragma: no cover
    servers = []
    if state.keys is not None and main_socket is not None and pairing_socket is not None:
        servers.append(await _serve_unix(main_socket, lambda r, w: handle(r, w, state)))
        servers.append(await _serve_unix(pairing_socket, lambda r, w: handle_pairing(r, w, state)))
    if away is not None and away_socket is not None:
        servers.append(await _serve_unix(away_socket, away.handle))
    loop = asyncio.get_running_loop()
    stdin = asyncio.StreamReader(limit=CORE_LINE_MAX)
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(stdin), sys.stdin)
    emit("listening")
    watch = loop.create_task(away.watch()) if away is not None else None
    try:
        for server in servers:
            await server.start_serving()
        # Serve until core goes away: its pipe closing is the signal to
        # stop, so a listener never outlives the core that started it.
        await read_answers(stdin, state, away)
        log.info("core closed the pipe; stopping")
    finally:
        if away is not None:
            away.shutdown()
            await asyncio.sleep(0.5)  # the BYEs out
        if watch is not None:
            watch.cancel()
        for server in servers:
            server.close()


def main(argv: list[str] | None = None) -> int:  # pragma: no cover — run by core
    parser = argparse.ArgumentParser(description="Friend Houses inbound listener")
    parser.add_argument("--keys", type=Path, help="the house keys (Friend Houses on)")
    parser.add_argument("--folder", type=Path, required=True,
                        help="friends.json, hello.bin, invites.json and phones.json")
    parser.add_argument("--socket", type=Path, help="the house's onion")
    parser.add_argument("--pairing-socket", type=Path, help="the pairing onion")
    parser.add_argument("--away-socket", type=Path, help="away chat's onion")
    parser.add_argument("--away-key", type=Path, help="the house's away Noise key")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    keys = None
    if args.keys is not None:
        keys = fid.load(args.keys)
        if keys is None:
            log.error("no house keys at %s", args.keys)
            return 2
    away = None
    if args.away_socket is not None and args.away_key is not None:
        from domovoi.away import listener as al

        away = al.AwayState(args.folder, al.load_key(args.away_key))
        if away.key is None:
            log.error("no away key at %s", args.away_key)
            return 2
    if keys is None and away is None:
        log.error("nothing to serve")
        return 2
    asyncio.run(serve(args.socket, args.pairing_socket, State(args.folder, keys),
                      args.away_socket, away))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
