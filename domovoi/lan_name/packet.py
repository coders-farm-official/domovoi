"""mDNS packets for one host name (RFC 6762 / RFC 1035): the parser and
the few packets the responder sends. Standard library only.

Anyone on the home network can send these packets, so the parser is the
attacker-facing part of the feature (a security review reads this file):

* a packet over :data:`MAX_PACKET` bytes, a non-zero opcode or rcode, more
  than :data:`MAX_QUESTIONS` questions or :data:`MAX_RECORDS` records is
  dropped whole (:class:`ParseError`), never partly read;
* a name is read in a loop, never by recursion. A compression pointer must
  point strictly BEFORE the place the pointer itself was found, so every
  jump goes backwards and a loop can't be built; at most
  :data:`MAX_POINTER_JUMPS` jumps, :data:`MAX_LABELS` labels and 255 bytes
  of name (RFC 1035 s3.1) per name;
* the 0x40 / 0x80 label types (extended, unused) are refused;
* every length is checked against the packet before it is read;
* labels stay bytes, lowercased in ASCII only: a name is compared, never
  decoded, printed or stored.

Nothing here keeps state; :mod:`domovoi.lan_name.responder` decides what
to do with a parsed packet.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

GROUP = "224.0.0.251"
PORT = 5353

MAX_PACKET = 9000
MAX_QUESTIONS = 32
MAX_RECORDS = 64
MAX_POINTER_JUMPS = 16
MAX_LABELS = 128
MAX_NAME_BYTES = 255

T_A = 1
T_AAAA = 28
T_NSEC = 47
T_ANY = 255
C_IN = 1
#: The top bit of a question's class (QU: "answer me by unicast") and of a
#: record's class (cache-flush: "this is the whole set").
TOP_BIT = 0x8000

FLAG_QR = 0x8000
FLAG_AA = 0x0400
_OPCODE_MASK = 0x7800
_RCODE_MASK = 0x000F

#: The host record's TTL (RFC 6762 s10: 120 s for records with a host
#: name), and the cap for a legacy-unicast answer (s6.7: at most 10 s).
HOST_TTL = 120
LEGACY_TTL = 10

Name = tuple[bytes, ...]


class ParseError(ValueError):
    """A packet that is dropped whole."""


@dataclass(frozen=True)
class Question:
    name: Name
    qtype: int
    qclass: int          # without the QU bit
    unicast: bool        # the QU bit


@dataclass(frozen=True)
class Record:
    name: Name
    rtype: int
    rclass: int          # without the cache-flush bit
    flush: bool
    ttl: int
    rdata: bytes         # raw; an A record's is its 4 address bytes


@dataclass(frozen=True)
class Message:
    qid: int
    flags: int
    questions: tuple[Question, ...]
    answers: tuple[Record, ...]
    authority: tuple[Record, ...]
    additional: tuple[Record, ...]

    @property
    def is_response(self) -> bool:
        return bool(self.flags & FLAG_QR)

    def records(self) -> tuple[Record, ...]:
        return self.answers + self.authority + self.additional


def _lower(label: bytes) -> bytes:
    # ASCII only (RFC 6762 s16: names compare case-insensitively in ASCII);
    # other bytes are kept as they are, so they simply never match ours.
    return label.lower()


def read_name(buf: bytes, off: int) -> tuple[Name, int]:
    """The name at ``off``: (labels lowercased, offset just past the name
    where it was written). :class:`ParseError` for anything malformed."""
    labels: list[bytes] = []
    total = 1                    # the root's zero byte
    end: int | None = None
    jumps = 0
    # Where the current run of labels started: a pointer must point before
    # it, so the walk only ever moves backwards and always ends.
    run_start = off
    n = len(buf)
    while True:
        if off >= n:
            raise ParseError("name runs past the packet")
        ln = buf[off]
        if ln == 0:
            off += 1
            break
        kind = ln & 0xC0
        if kind == 0xC0:
            if off + 1 >= n:
                raise ParseError("truncated pointer")
            ptr = ((ln & 0x3F) << 8) | buf[off + 1]
            if end is None:
                end = off + 2
            jumps += 1
            if jumps > MAX_POINTER_JUMPS:
                raise ParseError("too many pointers")
            if ptr >= run_start:
                raise ParseError("pointer does not point backwards")
            off = run_start = ptr
            continue
        if kind:
            raise ParseError("unsupported label type")
        if off + 1 + ln > n:
            raise ParseError("label runs past the packet")
        total += 1 + ln
        if total > MAX_NAME_BYTES:
            raise ParseError("name too long")
        labels.append(_lower(buf[off + 1:off + 1 + ln]))
        if len(labels) > MAX_LABELS:
            raise ParseError("too many labels")
        off += 1 + ln
    return tuple(labels), (end if end is not None else off)


def _read_record(buf: bytes, off: int) -> tuple[Record, int]:
    name, off = read_name(buf, off)
    if off + 10 > len(buf):
        raise ParseError("record header runs past the packet")
    rtype, rclass, ttl, rdlen = struct.unpack_from("!HHIH", buf, off)
    off += 10
    if off + rdlen > len(buf):
        raise ParseError("record data runs past the packet")
    rdata = bytes(buf[off:off + rdlen])
    return Record(name, rtype, rclass & ~TOP_BIT, bool(rclass & TOP_BIT), ttl, rdata), off + rdlen


def parse(buf: bytes) -> Message:
    """One datagram into a :class:`Message`; :class:`ParseError` when any
    part of it is out of bounds."""
    if len(buf) > MAX_PACKET:
        raise ParseError("packet too large")
    if len(buf) < 12:
        raise ParseError("short header")
    qid, flags, qd, an, ns, ar = struct.unpack_from("!HHHHHH", buf, 0)
    if flags & _OPCODE_MASK or flags & _RCODE_MASK:
        raise ParseError("opcode or rcode not zero")
    if qd > MAX_QUESTIONS:
        raise ParseError("too many questions")
    if an + ns + ar > MAX_RECORDS:
        raise ParseError("too many records")
    off = 12
    questions: list[Question] = []
    for _ in range(qd):
        name, off = read_name(buf, off)
        if off + 4 > len(buf):
            raise ParseError("question runs past the packet")
        qtype, qclass = struct.unpack_from("!HH", buf, off)
        off += 4
        questions.append(Question(name, qtype, qclass & ~TOP_BIT, bool(qclass & TOP_BIT)))
    sections: list[list[Record]] = [[], [], []]
    for i, count in enumerate((an, ns, ar)):
        for _ in range(count):
            rec, off = _read_record(buf, off)
            sections[i].append(rec)
    return Message(qid, flags, tuple(questions), tuple(sections[0]), tuple(sections[1]), tuple(sections[2]))


# ─── building ───────────────────────────────────────────────────────────


def name_of(host: str) -> Name:
    """``"domovoi"`` (a checked label, :mod:`domovoi.lan_name.names`) as
    the wire name ``domovoi.local``."""
    return (host.encode("ascii"), b"local")


def encode_name(name: Name) -> bytes:
    """Uncompressed: the packets are small, and one encoder is easier to
    hold to the C# copy byte for byte."""
    return b"".join(bytes([len(label)]) + label for label in name) + b"\x00"


def _header(qid: int, flags: int, qd: int, an: int, ns: int, ar: int) -> bytes:
    return struct.pack("!HHHHHH", qid, flags, qd, an, ns, ar)


def a_record(name: Name, address: bytes, ttl: int, *, flush: bool = True) -> bytes:
    rclass = C_IN | (TOP_BIT if flush else 0)
    return encode_name(name) + struct.pack("!HHIH", T_A, rclass, ttl, 4) + address


def nsec_record(name: Name, ttl: int, *, flush: bool = True) -> bytes:
    """"This name has an A record and nothing else" (RFC 6762 s6.1): the
    next-name field is the name itself, the bitmap window 0 lists type 1."""
    rdata = encode_name(name) + b"\x00\x01\x40"
    rclass = C_IN | (TOP_BIT if flush else 0)
    return encode_name(name) + struct.pack("!HHIH", T_NSEC, rclass, ttl, len(rdata)) + rdata


def probe(name: Name, address: bytes) -> bytes:
    """RFC 6762 s8.1: an ANY question with the QU bit, our proposed record
    in the authority section."""
    return (_header(0, 0, 1, 0, 1, 0) + encode_name(name) + struct.pack("!HH", T_ANY, C_IN | TOP_BIT)
            + a_record(name, address, HOST_TTL, flush=False))


def response(name: Name, address: bytes, ttl: int, *, qtype: int = T_A) -> bytes:
    """A multicast answer (also the announcement and, with ttl 0, the
    goodbye): the A record, and the NSEC that says there is no AAAA.
    Asked for AAAA, the NSEC is the answer and the A the extra."""
    a = a_record(name, address, ttl)
    nsec = nsec_record(name, ttl)
    if qtype == T_AAAA:
        return _header(0, FLAG_QR | FLAG_AA, 0, 1, 0, 1) + nsec + a
    if qtype == T_ANY:
        return _header(0, FLAG_QR | FLAG_AA, 0, 2, 0, 0) + a + nsec
    return _header(0, FLAG_QR | FLAG_AA, 0, 1, 0, 1) + a + nsec


def legacy_response(qid: int, name: Name, address: bytes, qtype: int) -> bytes:
    """RFC 6762 s6.7: to a query from a port other than 5353, by unicast:
    its id, its question repeated, no cache-flush bit, a TTL of at most
    10 s."""
    question = encode_name(name) + struct.pack("!HH", qtype, C_IN)
    if qtype == T_AAAA:
        return _header(qid, FLAG_QR | FLAG_AA, 1, 1, 0, 0) + question + nsec_record(name, LEGACY_TTL, flush=False)
    return _header(qid, FLAG_QR | FLAG_AA, 1, 1, 0, 0) + question + a_record(name, address, LEGACY_TTL, flush=False)
