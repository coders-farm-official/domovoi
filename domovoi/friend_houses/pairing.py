"""A friend request: one exchange on the inviting house's pairing address.

Friend Houses ("serverless messaging and friend system between households").
House B imported A's friend file; it now connects to A's **pairing onion**
(an open onion that exists only while A has unused friend files) and asks.

On the wire, after the ``DFP1`` preface, all of it inside Noise
``IKpsk2`` (A's session key from the file; psk = the file's invite secret)::

    B -> A  message 1   invite id (16) + commitment to B's house key and nB (32)
    A -> B  message 2   nA (32)                       [psk proven by A]
    B -> A  transport   nB (32) + the request         [psk proven by B]
    A -> B  transport   0x01 "received"

The request is B's signed address statement (its identity, session key and
main onion), a **reach key** for A (the private half of a Tor client key B
has already authorized, so A can answer on B's main onion when it accepts)
and the name B calls itself.

**The 6-digit code** (owner decision 14; 6 digits since pairing v2, in
1.5.0): both sides compute
``SHA-256("domovoi-fh-code-v3" ‖ h ‖ A ‖ B ‖ nA ‖ nB) mod 1,000,000``,
where h is the Noise handshake hash. B commits to **its house key and nB**
before it sees nA, and A sends nA before it sees nB, so someone who
swapped the file and sits between the two houses can't steer either code:
not by choosing a nonce, and not by generating house keys after seeing nA
(audit R6 FH-1: the first version committed to nB alone, and the house key
that arrives later could be ground to any code in under a second). A swap
survives a comparison with a 1-in-1,000,000 chance per run (finding 13).

**Versions.** This is pairing v2: preface ``DFP2``, its own Noise prologue
and code context, so its handshake hash and code can't be mixed with v1's
(4 digits, ``DFP1``). The version is also the friend file's signed version
byte (:mod:`domovoi.friend_houses.friend_file`), so a house asks only with
a v2 file and answers only v2. v1 isn't spoken at all, in either role:
there is nothing to fall back to, so nobody can talk a house into the
4-digit code. Friends made with v1 stay friends; pairing is only how they
met, and the session protocol (:mod:`domovoi.friend_houses.session`) is
unchanged.

Nothing in a request is trusted until the handshake proved the file's
secret, the statement's signature checks out, and the statement names the
very session key the handshake authenticated.
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import struct
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from domovoi.friend_houses import friend_file as ff
from domovoi.friend_houses import identity as fid
from domovoi.friend_houses import session as fs
from domovoi.friend_houses.noise import TAGLEN, Handshake, NoiseError, Transport

PROTOCOL_VERSION = 2
PREFACE = b"DFP2"
PROLOGUE = b"domovoi-fh-pairing-v2"
CODE_CONTEXT = b"domovoi-fh-code-v3"
COMMIT_CONTEXT = b"domovoi-fh-code-commit-v3"
CODE_DIGITS = 6
TIMEOUT_SEC = 60.0

_MSG1_PAYLOAD = 16 + 32
_MSG1_SIZE = 32 + 32 + TAGLEN + _MSG1_PAYLOAD + TAGLEN
_MSG2_SIZE = 32 + 32 + TAGLEN
_REQUEST_FIXED = struct.Struct(f">32s{fid.STATEMENT_SIZE}s32sB")  # nB, statement, reach key, name length
_REQUEST_MAX = _REQUEST_FIXED.size + ff.NAME_MAX_BYTES + TAGLEN
_RECEIVED = b"\x01"


class PairingRefused(Exception):
    """The request didn't go through. Never says why to the other end."""


def commitment(nonce: bytes, house_key: bytes) -> bytes:
    """What the asking house commits to before it sees the inviter's
    nonce: its nonce AND its identity, so neither can be chosen later."""
    return hashlib.sha256(COMMIT_CONTEXT + house_key + nonce).digest()


def code(handshake_hash: bytes, inviter_house: bytes, asker_house: bytes, n_inviter: bytes,
         n_asker: bytes) -> str:
    digest = hashlib.sha256(CODE_CONTEXT + handshake_hash + inviter_house + asker_house
                            + n_inviter + n_asker).digest()
    return f"{int.from_bytes(digest, 'big') % 10 ** CODE_DIGITS:0{CODE_DIGITS}d}"


def is_code(value: object) -> bool:
    """A code this house's pairing makes: exactly :data:`CODE_DIGITS` digits."""
    return isinstance(value, str) and len(value) == CODE_DIGITS and value.isascii() and value.isdigit()


@dataclass(frozen=True)
class Request:
    """A friend request as the inviting house received it."""

    invite_id: bytes
    statement: fid.AddressStatement
    reach_private: bytes
    name: str
    code: str

    @property
    def house_key(self) -> bytes:
        return self.statement.house_key


async def _send(writer: asyncio.StreamWriter, data: bytes) -> None:
    writer.write(fs.frame(data))
    await writer.drain()


async def ask(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    file: ff.FriendFile,
    keys: fid.HouseKeys,
    statement: fid.AddressStatement,
    reach_private: bytes,
    name: str,
) -> str:
    """House B's side, over a stream already connected to A's pairing onion.
    Returns the 6-digit code. Raises :class:`PairingRefused`."""
    hs = Handshake("IKpsk2", initiator=True, prologue=PROLOGUE, static=keys.session,
                   remote_static=file.session_key, psk=file.invite_secret)
    n_asker = secrets.token_bytes(32)
    try:
        writer.write(PREFACE + fs.frame(hs.write_message(
            file.invite_id + commitment(n_asker, keys.house_key))))
        await writer.drain()
        msg2 = await fs.read_frame(reader, max_size=_MSG2_SIZE, timeout=TIMEOUT_SEC)
        n_inviter = hs.read_message(msg2)
        if len(n_inviter) != 32:
            raise PairingRefused("bad answer")
        transport = hs.transport()
        name_bytes = ff.clean_name(name).encode("utf-8")
        request = _REQUEST_FIXED.pack(n_asker, statement.to_bytes(), reach_private,
                                      len(name_bytes)) + name_bytes
        await _send(writer, transport.encrypt(request))
        reply = await fs.read_frame(reader, max_size=1 + TAGLEN, timeout=TIMEOUT_SEC)
        if transport.decrypt(reply) != _RECEIVED:
            raise PairingRefused("not received")
    except (NoiseError, fs.SessionRefused, ConnectionError) as exc:
        writer.close()  # don't leave the other end waiting for a request
        raise PairingRefused("the other house didn't take the request") from exc
    return code(transport.handshake_hash, file.house_key, keys.house_key, n_inviter, n_asker)


async def answer(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    keys: fid.HouseKeys,
    invite_open: Callable[[bytes], Awaitable[bool]],
) -> Request:
    """House A's side, on its pairing listener. ``invite_open(id)`` says
    whether that invite is unused and unexpired. Raises
    :class:`PairingRefused` for anything else, without a word to the caller."""
    try:
        preface = await asyncio.wait_for(reader.readexactly(len(PREFACE)), TIMEOUT_SEC)
    except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError) as exc:
        raise PairingRefused("no preface") from exc
    if preface != PREFACE:
        raise PairingRefused("unknown protocol")  # v1 (DFP1) included: never answered
    try:
        msg1 = await fs.read_frame(reader, max_size=_MSG1_SIZE, timeout=TIMEOUT_SEC)
        hs = Handshake("IKpsk2", initiator=False, prologue=PROLOGUE, static=keys.session)
        first = hs.read_message(msg1)
        if len(first) != _MSG1_PAYLOAD:
            raise PairingRefused("bad first message")
        invite_id, commit = first[:16], first[16:]
        if not await invite_open(invite_id):
            raise PairingRefused("no such invite")
        hs.provide_psk(keys.invite_secret(invite_id))
        n_inviter = secrets.token_bytes(32)
        await _send(writer, hs.write_message(n_inviter))
        transport: Transport = hs.transport()
        sealed = await fs.read_frame(reader, max_size=_REQUEST_MAX, timeout=TIMEOUT_SEC)
        request = transport.decrypt(sealed)  # fails unless the caller held the file
    except (NoiseError, fs.SessionRefused, ConnectionError) as exc:
        raise PairingRefused("handshake failed") from exc
    if len(request) < _REQUEST_FIXED.size:
        raise PairingRefused("request too short")
    n_asker, raw_statement, reach_private, name_len = _REQUEST_FIXED.unpack(
        request[: _REQUEST_FIXED.size])
    if len(request) != _REQUEST_FIXED.size + name_len or name_len > ff.NAME_MAX_BYTES:
        raise PairingRefused("request has the wrong size")
    statement = fid.AddressStatement.parse(raw_statement)
    if statement is None or not secrets.compare_digest(statement.session_key, hs.remote_static or b""):
        raise PairingRefused("statement doesn't match the caller")
    if not secrets.compare_digest(commitment(n_asker, statement.house_key), commit):
        raise PairingRefused("commitment doesn't match")  # a house key chosen after nA
    if secrets.compare_digest(statement.house_key, keys.house_key):
        raise PairingRefused("a house can't befriend itself")
    try:
        name = ff.clean_name(request[_REQUEST_FIXED.size:].decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise PairingRefused("bad name") from exc
    try:
        await _send(writer, transport.encrypt(_RECEIVED))
    except (NoiseError, ConnectionError) as exc:
        raise PairingRefused("stream ended") from exc
    return Request(invite_id, statement, reach_private, name,
                   code(transport.handshake_hash, keys.house_key, statement.house_key,
                        n_inviter, n_asker))
