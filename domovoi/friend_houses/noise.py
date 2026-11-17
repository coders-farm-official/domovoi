"""The Noise handshakes Friend Houses uses between two houses.

Friend Houses ("serverless messaging and friend system between households")
connects houses over Tor onion services. Tor's client authorization only
decides who can *find* a house; it is not proof of who is on the other end,
and a removed friend can keep reaching the onion for minutes after removal.
So every connection runs a Noise handshake bound to the two houses' session
keys, and the house that answers checks the caller against its current
friend list before anything else is read.

Two patterns from the Noise Protocol Framework (revision 34), with X25519,
ChaCha20-Poly1305 and SHA-256:

``IK``
    Every connection between friends. The caller already knows the answering
    house's session key (from its address statement), so the handshake is
    one round trip and both sides end up authenticated, with fresh
    ephemeral keys (forward secrecy: a stolen session key does not decrypt
    recorded traffic).

``IKpsk2``
    A friend request to a house's pairing address. The caller knows the
    inviting house's session key from the friend file, and the pre-shared
    key is the friend file's invite secret. The secret itself never crosses
    the wire, and a caller without the file cannot complete the handshake.

Only these two patterns are implemented, by design: the code does exactly
what the two houses need and nothing a peer could steer it into. It is
built on ``cryptography`` (Apache-2.0 OR BSD-3-Clause), a core dependency,
and checked against the published cacophony test vectors
(domovoi/tests/fixtures/noise_vectors.json) in
domovoi/tests/test_friend_houses_noise.py.
"""

from __future__ import annotations

import hashlib
import hmac
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

DHLEN = 32
HASHLEN = 32
TAGLEN = 16
PSKLEN = 32
MAX_MESSAGE = 65535
_MAX_NONCE = 2**64 - 1  # reserved by the spec; never used as a nonce

# Message patterns. Each entry is the token list of one handshake message;
# the initiator writes the even-numbered ones.
_PATTERNS: dict[str, tuple[tuple[str, ...], ...]] = {
    "IK": (("e", "es", "s", "ss"), ("e", "ee", "se")),
    "IKpsk2": (("e", "es", "s", "ss"), ("e", "ee", "se", "psk")),
}


class NoiseError(Exception):
    """A handshake or transport message was refused.

    The message never says why in a way a peer can use: callers drop the
    connection and log the event, they don't answer it.
    """


def public_bytes(key: X25519PrivateKey | X25519PublicKey) -> bytes:
    """The raw 32-byte public key of an X25519 key (private or public)."""
    pub = key.public_key() if isinstance(key, X25519PrivateKey) else key
    return pub.public_bytes(Encoding.Raw, PublicFormat.Raw)


def _dh(private: X25519PrivateKey, public: bytes) -> bytes:
    try:
        return private.exchange(X25519PublicKey.from_public_bytes(public))
    except ValueError as exc:  # all-zero result: a low-order point
        raise NoiseError("bad key") from exc


def _hash(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def _hmac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


def _hkdf(chaining_key: bytes, ikm: bytes, outputs: int) -> tuple[bytes, ...]:
    temp = _hmac(chaining_key, ikm)
    out1 = _hmac(temp, b"\x01")
    out2 = _hmac(temp, out1 + b"\x02")
    if outputs == 2:
        return out1, out2
    return out1, out2, _hmac(temp, out2 + b"\x03")


class CipherState:
    """One direction of an encrypted channel: a key and a message counter."""

    def __init__(self, key: bytes | None = None) -> None:
        self._key = key
        self._nonce = 0

    @property
    def has_key(self) -> bool:
        return self._key is not None

    def _aead(self) -> ChaCha20Poly1305:
        assert self._key is not None
        return ChaCha20Poly1305(self._key)

    def _next_nonce(self) -> bytes:
        if self._nonce >= _MAX_NONCE:
            raise NoiseError("channel exhausted")
        nonce = b"\x00\x00\x00\x00" + struct.pack("<Q", self._nonce)
        self._nonce += 1
        return nonce

    def encrypt(self, ad: bytes, plaintext: bytes) -> bytes:
        if self._key is None:
            return plaintext
        return self._aead().encrypt(self._next_nonce(), plaintext, ad)

    def decrypt(self, ad: bytes, ciphertext: bytes) -> bytes:
        if self._key is None:
            return ciphertext
        if self._nonce >= _MAX_NONCE:
            raise NoiseError("channel exhausted")
        nonce = b"\x00\x00\x00\x00" + struct.pack("<Q", self._nonce)
        try:
            plaintext = self._aead().decrypt(nonce, ciphertext, ad)
        except InvalidTag as exc:
            raise NoiseError("message refused") from exc
        self._nonce += 1  # only after a successful decrypt (spec 5.1)
        return plaintext


class _SymmetricState:
    def __init__(self, protocol_name: bytes) -> None:
        if len(protocol_name) <= HASHLEN:
            self.h = protocol_name.ljust(HASHLEN, b"\x00")
        else:
            self.h = _hash(protocol_name)
        self.ck = self.h
        self.cipher = CipherState()

    def mix_key(self, ikm: bytes) -> None:
        self.ck, temp_k = _hkdf(self.ck, ikm, 2)
        self.cipher = CipherState(temp_k)

    def mix_hash(self, data: bytes) -> None:
        self.h = _hash(self.h + data)

    def mix_key_and_hash(self, ikm: bytes) -> None:
        self.ck, temp_h, temp_k = _hkdf(self.ck, ikm, 3)
        self.mix_hash(temp_h)
        self.cipher = CipherState(temp_k)

    def encrypt_and_hash(self, plaintext: bytes) -> bytes:
        ciphertext = self.cipher.encrypt(self.h, plaintext)
        self.mix_hash(ciphertext)
        return ciphertext

    def decrypt_and_hash(self, ciphertext: bytes) -> bytes:
        plaintext = self.cipher.decrypt(self.h, ciphertext)
        self.mix_hash(ciphertext)
        return plaintext

    def split(self) -> tuple[CipherState, CipherState]:
        k1, k2 = _hkdf(self.ck, b"", 2)
        return CipherState(k1), CipherState(k2)


class Handshake:
    """One side of an ``IK`` or ``IKpsk2`` handshake.

    ``static`` is this house's session key. The initiator must pass the
    answering house's session key as ``remote_static``; the responder learns
    the initiator's from the first message (:attr:`remote_static`) and must
    check it against its friend list before acting on the payload.

    ``ephemeral`` exists for the test vectors only; real handshakes generate
    a fresh one.
    """

    def __init__(
        self,
        pattern: str,
        *,
        initiator: bool,
        prologue: bytes,
        static: X25519PrivateKey,
        remote_static: bytes | None = None,
        psk: bytes | None = None,
        ephemeral: X25519PrivateKey | None = None,
    ) -> None:
        if pattern not in _PATTERNS:
            raise ValueError(f"unsupported pattern {pattern!r}")
        if ("psk" in pattern) != (psk is not None):
            raise ValueError("a psk is required by IKpsk2 and refused by IK")
        if psk is not None and len(psk) != PSKLEN:
            raise ValueError("psk must be 32 bytes")
        if initiator and (remote_static is None or len(remote_static) != DHLEN):
            raise ValueError("the initiator must know the responder's static key")
        self._messages = _PATTERNS[pattern]
        self._has_psk = psk is not None
        self._psk = psk
        self.initiator = initiator
        self._s = static
        self._e = ephemeral
        self._rs = remote_static if initiator else None
        self._re: bytes | None = None
        self._step = 0
        self._result: tuple[CipherState, CipherState] | None = None
        self._ss = _SymmetricState(f"Noise_{pattern}_25519_ChaChaPoly_SHA256".encode())
        self._ss.mix_hash(prologue)
        # Pre-message pattern for IK:  <- s
        responder_static = remote_static if initiator else public_bytes(static)
        self._ss.mix_hash(responder_static)

    # -- state ---------------------------------------------------------------

    @property
    def complete(self) -> bool:
        return self._result is not None

    @property
    def remote_static(self) -> bytes | None:
        return self._rs

    @property
    def handshake_hash(self) -> bytes:
        return self._ss.h

    def _my_turn(self) -> bool:
        return (self._step % 2 == 0) == self.initiator

    def _token_dh(self, token: str) -> bytes:
        e, re, rs = self._e, self._re, self._rs
        if token == "ee":
            assert e is not None and re is not None
            return _dh(e, re)
        if token == "ss":
            assert rs is not None
            return _dh(self._s, rs)
        if token == "es":  # the initiator's ephemeral with the responder's static
            if self.initiator:
                assert e is not None and rs is not None
                return _dh(e, rs)
            assert re is not None
            return _dh(self._s, re)
        # se: the initiator's static with the responder's ephemeral
        if self.initiator:
            assert re is not None
            return _dh(self._s, re)
        assert e is not None and rs is not None
        return _dh(e, rs)

    def _finish_if_done(self) -> None:
        self._step += 1
        if self._step == len(self._messages):
            self._result = self._ss.split()
            self._psk = None

    # -- messages ------------------------------------------------------------

    def write_message(self, payload: bytes = b"") -> bytes:
        if self.complete or not self._my_turn():
            raise NoiseError("out of turn")
        out = bytearray()
        for token in self._messages[self._step]:
            if token == "e":
                if self._e is None:
                    self._e = X25519PrivateKey.generate()
                e_pub = public_bytes(self._e)
                out += e_pub
                self._ss.mix_hash(e_pub)
                if self._has_psk:
                    self._ss.mix_key(e_pub)
            elif token == "s":
                out += self._ss.encrypt_and_hash(public_bytes(self._s))
            elif token == "psk":
                assert self._psk is not None
                self._ss.mix_key_and_hash(self._psk)
            else:
                self._ss.mix_key(self._token_dh(token))
        out += self._ss.encrypt_and_hash(payload)
        if len(out) > MAX_MESSAGE:
            raise NoiseError("message too large")
        self._finish_if_done()
        return bytes(out)

    def read_message(self, message: bytes) -> bytes:
        if self.complete or self._my_turn():
            raise NoiseError("out of turn")
        if len(message) > MAX_MESSAGE:
            raise NoiseError("message too large")
        view = memoryview(message)
        pos = 0
        for token in self._messages[self._step]:
            if token == "e":
                if len(view) - pos < DHLEN:
                    raise NoiseError("message too short")
                self._re = bytes(view[pos:pos + DHLEN])
                pos += DHLEN
                self._ss.mix_hash(self._re)
                if self._has_psk:
                    self._ss.mix_key(self._re)
            elif token == "s":
                size = DHLEN + (TAGLEN if self._ss.cipher.has_key else 0)
                if len(view) - pos < size:
                    raise NoiseError("message too short")
                self._rs = self._ss.decrypt_and_hash(bytes(view[pos:pos + size]))
                pos += size
            elif token == "psk":
                assert self._psk is not None
                self._ss.mix_key_and_hash(self._psk)
            else:
                self._ss.mix_key(self._token_dh(token))
        payload = self._ss.decrypt_and_hash(bytes(view[pos:]))
        self._finish_if_done()
        return payload

    def transport(self) -> Transport:
        """The two encrypted directions, once the handshake is complete."""
        if self._result is None:
            raise NoiseError("handshake not complete")
        c1, c2 = self._result
        send, recv = (c1, c2) if self.initiator else (c2, c1)
        return Transport(send, recv, self.handshake_hash)


class Transport:
    """The encrypted channel after a handshake: one counter per direction."""

    def __init__(self, send: CipherState, recv: CipherState, handshake_hash: bytes) -> None:
        self._send = send
        self._recv = recv
        self.handshake_hash = handshake_hash

    def encrypt(self, plaintext: bytes) -> bytes:
        if len(plaintext) + TAGLEN > MAX_MESSAGE:
            raise NoiseError("message too large")
        return self._send.encrypt(b"", plaintext)

    def decrypt(self, ciphertext: bytes) -> bytes:
        if len(ciphertext) > MAX_MESSAGE:
            raise NoiseError("message too large")
        return self._recv.decrypt(b"", ciphertext)
