"""The network name's state machine (RFC 6762 s8-s10), with no sockets and
no clock of its own: the caller hands it packets and the time, and sends
what it returns. The Linux core drives it from its own socket
(:mod:`domovoi.lan_name.service`); DomovoiHost's C# copy
(``windows/common/MdnsResponder.cs``) follows the same steps, and both are
tested with a fake clock.

What it does, in order:

1. **Probe** the candidate name three times, 250 ms apart (after a random
   0-250 ms wait), an ANY question with the QU bit and our A record in the
   authority section.
2. Any **response** carrying a record for that name, heard once our first
   probe is out, is a conflict, unless every A record in it is THIS
   address (the LAN address the name points at, nothing else of this
   machine's): then this machine's own name service already answers to it
   (the PC is itself called ``domovoi``, or Avahi on this box publishes
   it), and the name is left to it: state ``held``, looked at again every
   :data:`HELD_RECHECK`. A simultaneous probe for the same name is settled
   by RFC 6762 s8.2's comparison; the loser waits a second and probes again.
3. No conflict: **announce** twice, 1 s apart, then answer questions for the
   name: A (with the NSEC that says there is no AAAA), AAAA (the NSEC),
   ANY (both). Multicast answers go to the group even for QU questions and
   probes (the Windows spike: a unicast reply reaches only one of the
   sockets sharing 5353 on a host); a query from a port other than 5353
   (legacy unicast) gets a unicast answer, only to a source on the LAN, at
   most :data:`LEGACY_PER_SEC` a second in all and one a second per source.
4. **Taken**: the default walks ``domovoi``, ``domovoi2`` ... ``domovoi99``;
   a name the household typed is never changed by us: the name held before
   is kept (probed again), and the state says ``taken``. With every name
   taken it tries again after :data:`RETRY_AFTER_GIVING_UP`.
5. A **conflict after announcing** (s9) probes the name again, at most once
   per :data:`REPROBE_GAP`; losing it then is a forced rename, and after
   :data:`MAX_FORCED_RENAMES` in an hour the responder stops and says
   ``taken`` for the rest of that hour, so a hostile device can't make the
   name flap.
6. **Goodbye** (TTL 0) for a name it stops announcing: on stop, on a rename,
   when the feature is turned off, when the address changes.

Bounded everywhere (anyone on the LAN can send these packets; security
review 2026-11-12): at most :data:`INBOUND_PER_SEC` packets a second are
parsed, the rest dropped before parsing; a packet that fails
:func:`packet.parse` (which has a per-packet label budget) is dropped; a
packet from outside the LAN's subnet, or a response not from port 5353, is
dropped; at most :data:`MAX_SENDS_PER_SEC` packets go out a second whatever
comes in; a multicast answer for one type at most once a second (a probe's
defence at most every 250 ms).
"""

from __future__ import annotations

import ipaddress
import random
from collections import deque
from dataclasses import dataclass
from typing import Callable

from domovoi.lan_name import names, packet
from domovoi.lan_name.packet import Message, Name

PROBE_COUNT = 3
PROBE_GAP = 0.25
ANNOUNCE_COUNT = 2
ANNOUNCE_GAP = 1.0
#: RFC 6762 s8.1: after 15 conflicts in 10 s, wait 5 s before probing again.
CONFLICT_BURST = 15
CONFLICT_WINDOW = 10.0
CONFLICT_PAUSE = 5.0
#: A name this machine's own name service answers to is looked at again
#: this often: if it stops, the name is announced by us instead.
HELD_RECHECK = 60.0
REPROBE_GAP = 10.0
MAX_FORCED_RENAMES = 3
RENAME_WINDOW = 3600.0
#: Every name taken (the default's 99, or a typed one and the one before):
#: start again after this long, never give up for good.
RETRY_AFTER_GIVING_UP = RENAME_WINDOW
MAX_SENDS_PER_SEC = 20
#: Legacy-unicast answers have their own budget: in all, and per source.
LEGACY_PER_SEC = 5
LEGACY_PER_SOURCE_GAP = 1.0
LEGACY_SOURCES_KEPT = 256
#: Packets parsed a second; the rest are dropped unread (M1).
INBOUND_PER_SEC = 200
#: How long a packet we sent is remembered, to recognise it when the
#: multicast loop hands it back to us.
ECHO_WINDOW = 2.0
ECHO_MAX = 32

#: The states :meth:`Responder.snapshot` reports (the API's words), and
#: ``public``, which only DomovoiHost reports (a network marked Public in
#: Windows: nothing is announced there).
STATES = ("announced", "held", "checking", "taken", "off", "no_lan", "failed", "public")


@dataclass(frozen=True)
class Out:
    data: bytes
    #: None: the group (224.0.0.251:5353); otherwise a unicast address.
    dest: tuple[str, int] | None = None


_PRIVATE = tuple(ipaddress.IPv4Network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16"))


def private_source(ip: str) -> bool:
    """RFC 1918 or IPv4 link-local, never a public address."""
    try:
        a = ipaddress.IPv4Address(ip)
    except ValueError:
        return False
    return any(a in net for net in _PRIVATE)


def lan_address_ok(ip: str | None) -> bool:
    """The address a name may point at: RFC 1918 only (the plan's rule,
    the same as Lan.cs): no loopback, no link-local, no public address."""
    if not ip:
        return False
    try:
        a = ipaddress.IPv4Address(ip)
    except ValueError:
        return False
    return any(a in net for net in _PRIVATE[:3])


class _Bucket:
    """A token bucket on the caller's clock."""

    def __init__(self, rate: float) -> None:
        self.rate = rate
        self.tokens = rate
        self.at: float | None = None

    def take(self, now: float, *, force: bool = False) -> bool:
        if self.at is not None:
            self.tokens = min(self.rate, self.tokens + max(0.0, now - self.at) * self.rate)
        self.at = now
        if self.tokens < 1.0 and not force:
            return False
        self.tokens = max(0.0, self.tokens - 1.0)
        return True


class Responder:
    """One host name on one address. Not thread-safe: one caller drives it."""

    def __init__(self, *, rng: Callable[[], float] = random.random) -> None:
        self._rng = rng
        self.requested: str | None = None
        self.enabled = False
        self.address: bytes | None = None
        self.subnet: ipaddress.IPv4Network | None = None
        # off, no_lan, probing, announcing, announced, held, waiting,
        # gave_up, failed (or another state set by fail())
        self.phase = "off"
        self.candidate: str | None = None
        self.claimed: str | None = None
        self.taken: str | None = None
        self.error: str | None = None
        self.next_at: float | None = None
        self._queue: list[str] = []
        self._tried: set[str] = set()
        self._probes = 0
        self._announces = 0
        self._reprobe = False
        self._from_held = False
        self._reprobe_not_before = 0.0
        self._renames: deque[float] = deque()
        self._conflicts: deque[float] = deque()
        self._announced: tuple[Name, bytes] | None = None
        self._sent: deque[tuple[bytes, float]] = deque()
        self._last_mcast: dict[int, float] = {}
        self._out = _Bucket(MAX_SENDS_PER_SEC)
        self._legacy = _Bucket(LEGACY_PER_SEC)
        self._legacy_by_source: dict[str, float] = {}
        self._inbound = _Bucket(INBOUND_PER_SEC)

    # ─── configuration ────────────────────────────────────────────────

    def configure(self, requested: str, enabled: bool, address: str | None, now: float,
                  *, sticky: str | None = None, prefix: int | None = None) -> list[Out]:
        """(Re)start for this name, switch and address (``prefix``: the LAN
        adapter's, so packets from outside its subnet are dropped; None
        when unknown). ``sticky``: the name held last time (saved state),
        tried first when it fits. Nothing happens when nothing changed."""
        addr = _addr_bytes(address) if lan_address_ok(address) else None
        net = None
        if addr is not None and prefix is not None and 8 <= prefix <= 32:
            net = ipaddress.IPv4Network(f"{address}/{prefix}", strict=False)
        if (requested == self.requested and enabled == self.enabled and addr == self.address
                and net == self.subnet and self.phase in _CONFIGURED):
            return []
        out = self._goodbye(now)
        previous = self.claimed or sticky
        self.requested, self.enabled, self.address, self.subnet = requested, enabled, addr, net
        self.candidate = None
        self.claimed = None
        self.taken = None
        self.error = None
        self._tried = set()
        self._reprobe = False
        self._conflicts.clear()
        self.next_at = None
        if not enabled:
            self.phase = "off"
            return out
        if addr is None:
            self.phase = "no_lan"
            return out
        self._queue = self._order(requested, previous)
        self._next_candidate(now, first=True)
        return out + self.on_timer(now)

    def fail(self, error: str, state: str = "failed") -> None:
        """Nothing can be announced (the socket couldn't be opened; on
        Windows a network marked Public): ``state`` says why."""
        self.phase = state if state in STATES else "failed"
        self.error = error
        self.next_at = None
        self.requested = None   # the next configure() starts afresh

    @staticmethod
    def _order(requested: str, previous: str | None) -> list[str]:
        fam = names.family(requested)
        if requested == names.DEFAULT:
            first = [previous] if previous in fam else []
            return _dedupe(first + fam)
        # A typed name: that name only, then the one held before (and, if
        # that was the default's, the rest of the default's list).
        if previous and previous != requested:
            tail = [previous] + (names.family(names.DEFAULT) if previous in names.family(names.DEFAULT) else [])
            return _dedupe([requested] + tail)
        return [requested]

    def _next_candidate(self, now: float, *, first: bool = False, delay: float | None = None) -> None:
        nxt = next((n for n in self._queue if n not in self._tried), None)
        if nxt is None:
            # Every name taken: try again later, never for good (a burst of
            # forged answers must not silence the name forever).
            self.phase = "gave_up"
            self.candidate = None
            self.claimed = None
            self.next_at = now + RETRY_AFTER_GIVING_UP
            return
        self._start_probe(nxt, now, delay if delay is not None else (self._rng() * PROBE_GAP if first else 0.0))

    def _start_probe(self, name: str, now: float, delay: float) -> None:
        self.phase = "probing"
        self.candidate = name
        self._probes = 0
        self.next_at = now + max(0.0, delay)

    # ─── time ──────────────────────────────────────────────────────────

    def on_timer(self, now: float) -> list[Out]:
        out: list[Out] = []
        for _ in range(8):   # each step moves the phase on; bounded anyway
            if self.next_at is None or now < self.next_at:
                break
            if self.phase == "probing":
                if self._probes < PROBE_COUNT:
                    out += self._send(packet.probe(self._wire(self.candidate), self.address), None, now)
                    self._probes += 1
                    self.next_at = now + PROBE_GAP
                    continue
                self.claimed = self.candidate
                self._reprobe = False
                self.phase = "announcing"
                self._announces = 0
                self.next_at = now
                continue
            if self.phase == "announcing":
                name = self._wire(self.claimed)
                out += self._send(packet.response(name, self.address, packet.HOST_TTL), None, now)
                self._announced = (name, self.address)
                self._last_mcast[packet.T_A] = now
                self._announces += 1
                if self._announces >= ANNOUNCE_COUNT:
                    self.phase = "announced"
                    self.next_at = None
                else:
                    self.next_at = now + ANNOUNCE_GAP
                continue
            if self.phase in ("held", "announced"):
                # held: look again (the other responder may be gone);
                # announced: a re-probe that waited for REPROBE_GAP.
                self._from_held = self.phase == "held"
                self._start_probe(self.claimed, now, 0.0)
                self._reprobe = True
                continue
            if self.phase in ("waiting", "gave_up"):
                # The hour after too many forced renames, or after every
                # name was taken, is over: start the list again.
                self._renames.clear()
                self._tried = set()
                self.error = None
                self._next_candidate(now)
                continue
            break
        return out

    # ─── packets ──────────────────────────────────────────────────────

    def on_packet(self, data: bytes, src: tuple[str, int], now: float) -> list[Out]:
        if self.phase not in _LISTENING or self.address is None:
            return []
        if len(data) > packet.MAX_PACKET:
            return []
        if self.subnet is not None:
            try:
                if ipaddress.IPv4Address(src[0]) not in self.subnet:
                    return []
            except ValueError:
                return []
        # Cheap checks above; the rate limit before any parsing (M1).
        if not self._inbound.take(now):
            return []
        if self._is_echo(data, now):
            return []
        try:
            msg = packet.parse(data)
        except packet.ParseError:
            return []
        if msg.is_response:
            if src[1] != packet.PORT:
                return []   # RFC 6762 s11: responses come from 5353
            return self._on_response(msg, now)
        return self._on_query(msg, src, now)

    def _on_response(self, msg: Message, now: float) -> list[Out]:
        if self.phase == "probing":
            if self._probes == 0:
                return []   # nothing asked yet (a pause between candidates)
            name = self._wire(self.candidate)
            recs = [r for r in msg.records() if r.name == name]
            if not recs:
                return []
            a = [r.rdata for r in recs if r.rtype == packet.T_A and len(r.rdata) == 4]
            if a and all(x == self.address for x in a):
                # This machine's own name service answers to it, with our
                # LAN address: leave it to it, and look again soon.
                self.claimed = self.candidate
                self.phase = "held"
                self.next_at = now + HELD_RECHECK
                self._reprobe = False
                return []
            return self._lost(now)
        if self.phase in ("announcing", "announced") and self.claimed:
            name = self._wire(self.claimed)
            for r in msg.records():
                if (r.name == name and r.rtype == packet.T_A and r.rclass == packet.C_IN and r.ttl > 0
                        and len(r.rdata) == 4 and r.rdata != self.address):
                    return self._conflict_after_announce(now)
        return []

    def _conflict_after_announce(self, now: float) -> list[Out]:
        if self.phase == "announced" and self.next_at is not None:
            return []   # a re-probe is already scheduled
        self._from_held = False
        when = max(now, self._reprobe_not_before)
        self._reprobe_not_before = when + REPROBE_GAP
        if when > now:
            self.phase = "announced"
            self.next_at = when
            return []
        self._start_probe(self.claimed, now, 0.0)
        self._reprobe = True
        return []

    def _lost(self, now: float) -> list[Out]:
        """The candidate is someone else's."""
        lost = self.candidate
        out: list[Out] = []
        self._tried.add(lost)
        if self.taken is None:
            self.taken = lost
        if self._reprobe:
            # We held it and lost it: a forced rename.
            out += self._goodbye(now)
            self.claimed = None
            self._reprobe = False
            self._renames.append(now)
            while self._renames and now - self._renames[0] > RENAME_WINDOW:
                self._renames.popleft()
            if len(self._renames) >= MAX_FORCED_RENAMES:
                self.phase = "waiting"
                self.candidate = None
                self.error = "the name kept being taken by another device; trying again within the hour"
                self.next_at = self._renames[0] + RENAME_WINDOW
                return out
        if lost == self.claimed:
            self.claimed = None
        self._conflicts.append(now)
        while self._conflicts and now - self._conflicts[0] > CONFLICT_WINDOW:
            self._conflicts.popleft()
        pause = CONFLICT_PAUSE if len(self._conflicts) >= CONFLICT_BURST else 0.0
        if pause:
            self._conflicts.clear()
        self._next_candidate(now, delay=pause)
        return out

    def _on_query(self, msg: Message, src: tuple[str, int], now: float) -> list[Out]:
        if self.phase == "probing":
            return self._tiebreak(msg, now)
        if self.phase != "announced" or not self.claimed:
            return []
        name = self._wire(self.claimed)
        legacy = src[1] != packet.PORT
        is_probe = any(r.name == name for r in msg.authority)
        known = any(r.name == name and r.rtype == packet.T_A and r.rdata == self.address
                    and r.ttl >= packet.HOST_TTL // 2 for r in msg.answers)
        for q in msg.questions:
            if q.name != name or q.qclass not in (packet.C_IN, packet.T_ANY):
                continue
            if q.qtype not in (packet.T_A, packet.T_AAAA, packet.T_ANY):
                continue
            if legacy:
                return self._legacy_answer(msg.qid, name, q.qtype, src, now)
            if known and q.qtype != packet.T_AAAA:
                continue
            gap = PROBE_GAP if is_probe else 1.0
            last = self._last_mcast.get(q.qtype)
            if last is not None and now - last < gap:
                continue
            self._last_mcast[q.qtype] = now
            return self._send(packet.response(name, self.address, packet.HOST_TTL, qtype=q.qtype), None, now)
        return []

    def _legacy_answer(self, qid: int, name: Name, qtype: int, src: tuple[str, int], now: float) -> list[Out]:
        """A unicast answer: only to a source on the LAN (its subnet when
        known, a private address otherwise), at most one a second per
        source and LEGACY_PER_SEC in all, apart from the main budget."""
        ip, port = src
        if port == 0 or not private_source(ip):
            return []
        last = self._legacy_by_source.get(ip)
        if last is not None and now - last < LEGACY_PER_SOURCE_GAP:
            return []
        if not self._legacy.take(now):
            return []
        if len(self._legacy_by_source) >= LEGACY_SOURCES_KEPT:
            self._legacy_by_source = {k: v for k, v in self._legacy_by_source.items()
                                      if now - v < LEGACY_PER_SOURCE_GAP}
            if len(self._legacy_by_source) >= LEGACY_SOURCES_KEPT:
                return []
        self._legacy_by_source[ip] = now
        return self._remember(packet.legacy_response(qid, name, self.address, qtype), src, now)

    def _tiebreak(self, msg: Message, now: float) -> list[Out]:
        """RFC 6762 s8.2: another host probes the same name right now.
        Compare its proposed records with ours; the lower loses, waits a
        second and probes again."""
        name = self._wire(self.candidate)
        if not any(q.name == name for q in msg.questions):
            return []
        theirs = sorted((r.rclass, r.rtype, r.rdata) for r in msg.authority if r.name == name)
        if not theirs:
            return []
        ours = [(packet.C_IN, packet.T_A, self.address)]
        if theirs == ours:
            return []   # the same record: our own probe through another socket
        if _greater(theirs, ours):
            self._start_probe(self.candidate, now, 1.0)
        return []

    # ─── stopping ─────────────────────────────────────────────────────

    def stop(self, now: float) -> list[Out]:
        """Goodbye for the name, and nothing more until :meth:`configure`."""
        out = self._goodbye(now)
        self.phase = "off"
        self.requested = None
        self.next_at = None
        return out

    def _goodbye(self, now: float) -> list[Out]:
        if self._announced is None:
            return []
        name, addr = self._announced
        self._announced = None
        return self._send(packet.response(name, addr, 0), None, now, force=True)

    # ─── helpers ───────────────────────────────────────────────────────

    def _wire(self, label: str | None) -> Name:
        return packet.name_of(label or "")

    def _send(self, data: bytes, dest: tuple[str, int] | None, now: float, *, force: bool = False) -> list[Out]:
        if not self._out.take(now, force=force):
            return []
        return self._remember(data, dest, now)

    def _remember(self, data: bytes, dest: tuple[str, int] | None, now: float) -> list[Out]:
        self._sent.append((data, now))
        while len(self._sent) > ECHO_MAX:
            self._sent.popleft()
        return [Out(data, dest)]

    def _is_echo(self, data: bytes, now: float) -> bool:
        while self._sent and now - self._sent[0][1] > ECHO_WINDOW:
            self._sent.popleft()
        return any(d == data for d, _ in self._sent)

    # ─── what Settings shows ──────────────────────────────────────────

    def snapshot(self) -> dict:
        """``{requested, claimed, state, taken, error}`` in the API's words."""
        return {
            "requested": self.requested,
            "claimed": self.claimed if self.phase in ("announcing", "announced", "held", "probing") else None,
            "state": self.state,
            "taken": self.taken,
            "error": self.error,
        }

    @property
    def state(self) -> str:
        p = self.phase
        if p in ("gave_up", "waiting"):
            return "taken"
        if p in STATES and p not in ("announced",):
            return p   # off, no_lan, failed, held, public
        if p == "probing" or (p == "announcing" and self._announces == 0):
            # A re-probe of a name we hold still counts as held.
            if not (self._reprobe and self.claimed):
                return "checking"
            if self._from_held:
                return "held"
        if self.requested and self.claimed and not names.in_family(self.requested, self.claimed):
            return "taken"   # a typed name was taken; the old one is kept
        return "announced"


# Phases in which configure() with the same settings changes nothing.
_CONFIGURED = ("off", "no_lan", "probing", "announcing", "announced", "held", "waiting", "gave_up")
# Phases in which packets are read.
_LISTENING = ("probing", "announcing", "announced", "held")


def _greater(a: list[tuple[int, int, bytes]], b: list[tuple[int, int, bytes]]) -> bool:
    for x, y in zip(a, b):
        if x != y:
            return x > y
    return len(a) > len(b)


def _dedupe(seq: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for s in seq:
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _addr_bytes(ip: str) -> bytes:
    return ipaddress.IPv4Address(ip).packed
