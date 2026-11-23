"""Plugin mail on the wire: CAPS, the legacy probe and the sender's answers.

Friend Houses ("serverless messaging and friend system between households")
lets two houses' copies of one plugin exchange mail ("apps with friends",
1.5.0). This module is the wire part (build slice 3 of
``FRIEND-HOUSES-PLUGINS``); the mail service that stores, sends and hands
mail to plugins comes with slice 4. **Until then core refuses every plugin
mail** (``MESSAGE_NACK`` ``unavailable``) and never routes one into
people's messages (review D12).

CAPS
----
A ``CAPS`` frame (:class:`domovoi.friend_houses.session.Kind`) tells a
friend this house's protocol number and which of its apps that friend may
mail: ``proto u16``, ``count u8`` (at most 32), each slug as ``len u8`` +
slug by the plugin manifest's rule, ascending, no duplicates, consumed to
the exact length. No plugin versions, ever (review D14).

* It is sent **only to friends an admin turned "Apps with this friend" on
  for**; other friends never learn the protocol number (D14). Turning it
  off sends one empty CAPS, then nothing.
* The receiving listener takes it after its pending-friend check, at most
  one per connection (a second closes the connection), with its own event
  budget of :data:`CAPS_PER_HOUR` per friend.
* What a friend's CAPS says is **soft state** (:class:`Known`): it is
  forgotten after :data:`STALE_SEC` without a fresh one, on the friend's
  epoch change (a restore), on remove or block, and per app on a
  ``not_allowed`` refusal. Mail goes only to a fresh ``has_plugin``, so a
  downgraded or restored friend never has plugin mail spend its people's
  budget.

The legacy probe (review D6)
----------------------------
A house before 1.5.0 doesn't know the CAPS kind and closes the connection.
So :func:`offer` first records presence for the handshake that just worked
(the house is online whatever it makes of CAPS), then sends CAPS and a
PING in a ``try`` of their own: a PONG means the friend speaks plugin mail
and has this house's CAPS (``caps_needed`` may be cleared, slice 4); a
close means a house before 1.5.0 (:data:`~domovoi.friend_houses.session.PROTO_LEGACY`),
never "offline"; no answer in time decides nothing. A legacy friend is
asked again at most hourly, normally daily, and once after its epoch
changes (:meth:`Known.probe_due`); its address statement changing doesn't
count, because the friend controls that.

Only the plugin sender's connections and the CAPS pass probe (slice 4).
People's flushes and presence pings send CAPS only to friends already known
to speak it, so a house before 1.5.0 is never sent a frame it doesn't know
on a connection that carries people's messages.
"""

from __future__ import annotations

import asyncio
import enum
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field

from domovoi.friend_houses import envelope as fe
from domovoi.friend_houses import session as fs

MAX_SLUGS = 32
CAPS_PER_HOUR = 12               # per friend, at the listener: its own budget
STALE_SEC = 6 * 3600             # a friend's CAPS older than this is forgotten
REFRESH_SEC = 3 * 3600           # this house re-sends its CAPS at least this often
PROBE_LEGACY_SEC = 24 * 3600     # a legacy friend is asked again daily...
PROBE_MIN_SEC = 3600             # ...and never more than hourly
PROBE_DEADLINE_SEC = 45.0        # CAPS + PING round trip
ACK_DEADLINE_SEC = 30.0          # one plugin mail's ack or refusal, overall


class BadCaps(ValueError):
    """A CAPS body that breaks the format."""


@dataclass(frozen=True)
class Caps:
    proto: int
    slugs: tuple[str, ...]


def encode_caps(slugs: Iterable[str], *, proto: int = fs.PROTO) -> bytes:
    """A CAPS body: sorted, de-duplicated, every slug checked."""
    wanted = sorted(set(slugs))
    if len(wanted) > MAX_SLUGS:
        raise ValueError(f"at most {MAX_SLUGS} apps")
    out = bytearray(proto.to_bytes(2, "big") + bytes([len(wanted)]))
    for slug in wanted:
        if not fe.valid_slug(slug):
            raise ValueError("bad plugin slug")
        out += bytes([len(slug)]) + slug.encode("ascii")
    return bytes(out)


def parse_caps(body: bytes) -> Caps:
    """A friend's CAPS body (BadCaps): exact length, ascending, unique,
    each slug by the manifest's rule, ``proto`` at least 2."""
    if not 3 <= len(body) <= fs.CAPS_BODY_MAX:
        raise BadCaps("size")
    proto = int.from_bytes(body[:2], "big")
    count = body[2]
    if proto < fs.PROTO:
        raise BadCaps("protocol number")
    if count > MAX_SLUGS:
        raise BadCaps("too many apps")
    slugs: list[str] = []
    at = 3
    for _ in range(count):
        if at >= len(body):
            raise BadCaps("short")
        n = body[at]
        raw = bytes(body[at + 1: at + 1 + n])
        if len(raw) != n:
            raise BadCaps("short")
        try:
            slug = raw.decode("ascii")
        except UnicodeDecodeError as exc:
            raise BadCaps("slug") from exc
        if not fe.valid_slug(slug):
            raise BadCaps("slug")
        if slugs and slug <= slugs[-1]:
            raise BadCaps("not ascending")
        slugs.append(slug)
        at += 1 + n
    if at != len(body):
        raise BadCaps("trailing bytes")
    return Caps(proto, tuple(slugs))


# ─── what this house knows of its friends (soft state) ───────────────────


@dataclass
class _Friend:
    epoch: bytes | None = None
    proto: int | None = None          # None unknown, 1 legacy, >= 2 plugin mail
    slugs: frozenset[str] = frozenset()
    caps_at: float | None = None      # when its CAPS last came
    probed_at: float | None = None


@dataclass
class Known:
    """Each friend's protocol number and apps, as last heard. In memory in
    slice 3; slice 4 keeps it in the friend's row (``peer_proto``,
    ``peer_caps``, ``peer_caps_at``, ``peer_caps_epoch``, ``probe_at``)."""

    clock: Callable[[], float] = time.time
    _friends: dict[bytes, _Friend] = field(default_factory=dict)

    def _get(self, house: bytes) -> _Friend:
        return self._friends.setdefault(house, _Friend())

    def saw_epoch(self, house: bytes, epoch: bytes) -> None:
        """A connection presented ``epoch``. A change (a restore) forgets
        the friend's apps and protocol, so it is probed again."""
        f = self._get(house)
        if f.epoch is not None and f.epoch != epoch:
            self._friends[house] = _Friend(epoch=epoch, probed_at=f.probed_at)
        else:
            f.epoch = epoch

    def caps(self, house: bytes, caps: Caps, epoch: bytes) -> None:
        """The friend's own CAPS arrived (the listener's ``caps`` event)."""
        self.saw_epoch(house, epoch)
        f = self._get(house)
        f.proto, f.slugs, f.caps_at = caps.proto, frozenset(caps.slugs), self.clock()

    def probed(self, house: bytes, proto: int) -> None:
        """A probe's outcome: :data:`fs.PROTO` (it answered) or
        :data:`fs.PROTO_LEGACY` (it closed on CAPS)."""
        f = self._get(house)
        f.proto, f.probed_at = proto, self.clock()
        if proto < fs.PROTO:
            f.slugs, f.caps_at = frozenset(), None

    def not_allowed(self, house: bytes, slug: str) -> None:
        """A ``not_allowed`` refusal: that app isn't open to this house."""
        f = self._friends.get(house)
        if f is not None:
            f.slugs = f.slugs - {slug}

    def forget(self, house: bytes) -> None:
        """Removed or blocked."""
        self._friends.pop(house, None)

    def proto(self, house: bytes) -> int | None:
        f = self._friends.get(house)
        return None if f is None else f.proto

    def has_plugin(self, house: bytes, slug: str) -> bool:
        """Whether the friend said, within :data:`STALE_SEC`, that it takes
        ``slug``'s mail from this house."""
        f = self._friends.get(house)
        if f is None or f.caps_at is None or f.proto is None or f.proto < fs.PROTO:
            return False
        if self.clock() - f.caps_at > STALE_SEC:
            return False
        return slug in f.slugs

    def probe_due(self, house: bytes) -> bool:
        """Unknown: yes. Legacy: daily, and after an epoch change, but never
        within an hour of the last probe. Known to speak it: no."""
        f = self._friends.get(house)
        if f is None or f.proto is None:
            return f is None or f.probed_at is None or self.clock() - f.probed_at >= PROBE_MIN_SEC
        if f.proto >= fs.PROTO:
            return False
        return f.probed_at is None or self.clock() - f.probed_at >= PROBE_LEGACY_SEC


KNOWN = Known()


# ─── the probe ───────────────────────────────────────────────────────────


class Offer(enum.Enum):
    DELIVERED = "delivered"  # PONG after our CAPS: it has it, and speaks proto >= 2
    LEGACY = "legacy"        # it closed on CAPS: a house before 1.5.0
    NO_ANSWER = "no_answer"  # nothing in time: decides nothing


async def offer(sess: fs.Session, caps_body: bytes, *,
                saw: Callable[[bytes, object], Awaitable[None]] | None = None,
                deadline: float = PROBE_DEADLINE_SEC, known: Known | None = None) -> Offer:
    """Send this house's CAPS on an open session, then a PING, and read
    what that says about the friend (see the module docstring).

    Presence is recorded first, for the handshake that already worked, so
    an older house that closes on CAPS still reads as online."""
    if saw is None:
        from domovoi.friend_houses import presence

        saw = presence.saw
    house = sess.peer_house_key
    await saw(house, sess.peer.statement)
    known = KNOWN if known is None else known
    known.saw_epoch(house, sess.peer.epoch)
    token = b"caps-ok\x00"

    async def _round_trip() -> Offer:
        try:
            await sess.send(fs.Kind.CAPS, caps_body)
            await sess.send(fs.Kind.PING, token)
        except (ConnectionError, OSError, fs.SessionRefused):
            return Offer.LEGACY
        while True:
            try:
                kind, body = await sess.recv(timeout=deadline + 5, accept=fs.SENDER_ACCEPTS)
            except fs.SessionRefused:
                return Offer.LEGACY  # closed (or answered with a kind we don't take)
            if kind is fs.Kind.BYE:
                return Offer.LEGACY
            if kind is fs.Kind.PONG and body == token:
                return Offer.DELIVERED

    try:
        outcome = await asyncio.wait_for(_round_trip(), deadline)
    except asyncio.TimeoutError:
        return Offer.NO_ANSWER
    known.probed(house, fs.PROTO if outcome is Offer.DELIVERED else fs.PROTO_LEGACY)
    return outcome


# ─── the plugin sender's answers ─────────────────────────────────────────


@dataclass(frozen=True)
class Answer:
    acked: bool
    reason: fs.Nack | None = None
    retry_after: int = 0


async def await_answer(sess: fs.Session, msg_id: bytes, *,
                       deadline: float = ACK_DEADLINE_SEC) -> Answer | None:
    """The friend's ACK or NACK for one plugin mail, within one overall
    deadline that no other frame extends (review D4). Only fixed-size
    answers are read (:data:`fs.SENDER_ACCEPTS`); anything else, a close or
    the deadline is None: the sender ends its round and backs off."""

    async def _wait() -> Answer | None:
        while True:
            kind, body = await sess.recv(timeout=deadline + 5, accept=fs.SENDER_ACCEPTS)
            if kind is fs.Kind.BYE:
                return None
            if kind is fs.Kind.MESSAGE_ACK and body == msg_id:
                return Answer(True)
            if kind is fs.Kind.MESSAGE_NACK:
                got, why, secs = fs.parse_nack(body)
                if got == msg_id:
                    return Answer(False, why, secs)
            # A PONG, or an answer to another mail: keep waiting, same deadline.

    try:
        return await asyncio.wait_for(_wait(), deadline)
    except (asyncio.TimeoutError, fs.SessionRefused):
        return None


__all__ = ["ACK_DEADLINE_SEC", "Answer", "BadCaps", "CAPS_PER_HOUR", "Caps", "KNOWN", "Known",
           "Offer", "STALE_SEC", "await_answer", "encode_caps", "offer", "parse_caps"]
