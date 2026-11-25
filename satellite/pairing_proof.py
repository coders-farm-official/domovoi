"""Prove an approval: the house a person approved this device on knows the
code the device said out loud — and nobody learns that code by watching.

Why this exists
---------------
A new room device that found its server by itself (the setup portal's
``auto``) asks EVERY Domovoi server it finds on the network for approval,
because the first one to answer is not necessarily the household's: a
neighbour's server, a test server on the same /24, or something hostile
that merely answers as Domovoi. So the device needs to tell which of them a
person actually approved it on. "Approved" from a server proves nothing —
any server can say it. What only the household's server can have is the
six digits the person read off the device and typed into THAT server's
dashboard.

Why not just send the code, or a hash of it
-------------------------------------------
The code is six digits: a million possibilities, which a computer tries in
well under a second. Anything the device sent that is computed from the
code alone — the code, a salted hash, an HMAC under a nonce the server can
see — lets whoever receives it test every code offline and find the right
one. The same holds the other way round: if the server proved itself with
an HMAC under the code, a rogue device holding the pending request could
test every code against THAT. A short secret shared by two parties who
both might be lying needs a password-authenticated key exchange (PAKE),
where each run lets a party test exactly one guess, and only by taking part
in a run the other side counts.

This is SPAKE2 (Abdalla & Pointcheval, 2005; RFC 9382 describes the same
exchange over elliptic curves) over the 2048-bit MODP group of RFC 3526
(group 14), with Python's integers. Stdlib only: the satellite runs it
before its venv has anything else, and the core imports this very file, so
there is one implementation and nothing to drift.

The exchange, for code ``c``, room ``r`` and server fingerprint ``f``::

    w  = H(c) mod q                          (both sides, from the code)
    device:  x random,  X = g^x * M^w        -> hello  "approval_pake"
    server:  y random,  Y = g^y * N^w        -> frame  "approval_challenge"
    both:    Z = g^(xy)   (device: (Y/N^w)^x,  server: (X/M^w)^y)
    K  = SHA-256(transcript: context, r, f, X, Y, Z, w)
    device:  Td = HMAC(K, "device confirms") -> frame  "approval_confirm"
    server:  checks Td (constant time); only then
             Ts = HMAC(K, "server confirms") -> frame  "approval_proof"
    device:  checks Ts, and only then trusts this server

``M`` and ``N`` are fixed group elements whose discrete logarithms nobody
knows (hashed from a public label, then squared into the prime-order
subgroup). ``X`` and ``Y`` each look uniformly random to anyone who does
not know ``w``, so an eavesdropper — or either party lying — learns
nothing it can test codes against offline: confirming a guess takes a
whole run with the other side, and both sides count runs. The transcript
binds the room and the server's fingerprint, so a proof minted in one
house's run is worthless in any other run, with any other server key.

Version 2 (1.5.0) also binds the pairing token the core is about to store
for the room: the transcript carries ``SHA-256(pairing token)`` — the
device hashes the token it put in its hello, the core the token it
received — under its own context string. A party in the middle that
forwards the exchange but swaps the hello's token for its own makes the
two sides compute different keys: the device's confirmation does not
check out, and nothing is bound. A device uses version 2 only with a
server that SIGNED ``pairing_proof >= 2`` in its health answer, and says
so with ``pairing_proof: 2`` in its hello; a hello without it is a
version-1 device, which a version-2 core still serves.

Version 3 (1.5.0) also binds the device's support key: the public half of
the Ed25519 key a support-session satellite signs its session frames with
(``support_key`` in the hello, satellite/support_shell.py), exactly as the
hello carries it — an empty field for a device that sends none. The core
pins that key for the device when this run approves it
(``satellites.support_key``), so the key a person approved is the one the
device holds: a party in the middle that swaps, adds or strips the hello's
key makes the two transcripts differ, and nothing is bound. A device uses
version 3 only with a server that SIGNED ``pairing_proof >= 3``.

What each side limits (documented in docs/SECURITY_PRIVACY.md):

* the device answers at most :data:`satellite.house_pairing.BUDGET_MAX`
  challenges per :data:`satellite.house_pairing.BUDGET_WINDOW_SEC`, across
  every server — a hostile server gets that many guesses at a
  million-to-one each;
* the core uses each code a person typed for exactly one run, and accepts
  a handful of typed codes per room per five minutes — a rogue device
  holding the pending request gets one guess per code typed.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

#: What a server advertises in ``/v1/health`` as ``pairing_proof`` when it
#: speaks this exchange. Bump on any change to the messages below.
#: 2 (1.5.0): the transcript binds the pairing token as well.
#: 3 (1.5.0): and the device's support key.
VERSION = 3

#: Every version a core runs: a version-3 core still serves version-1 and
#: version-2 devices (a version-1 hello carries no ``pairing_proof``).
SUPPORTED = (1, 2, 3)

#: The first version whose transcript binds the pairing token.
TOKEN_BOUND = 2

#: The first version whose transcript binds the support key.
KEY_BOUND = 3

CONTEXT = b"domovoi-pairing-proof-v1"
CONTEXT_V2 = b"domovoi-pairing-proof-v2"
CONTEXT_V3 = b"domovoi-pairing-proof-v3"

#: The proof flow takes six-digit codes only. A four-digit code (what the
#: setup portal minted before 2026-09-22) is ten thousand possibilities —
#: too few to leave to an online budget.
CODE_DIGITS = 6

# RFC 3526, group 14: the 2048-bit MODP group. p is a safe prime
# (q = (p-1)/2 is prime), g = 2 generates the subgroup of order q.
_P = int(
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD1"
    "29024E088A67CC74020BBEA63B139B22514A08798E3404DD"
    "EF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245"
    "E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED"
    "EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3D"
    "C2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F"
    "83655D23DCA3AD961C62F356208552BB9ED529077096966D"
    "670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B"
    "E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9"
    "DE2BCBF6955817183995497CEA956AE515D2261898FA0510"
    "15728E5A8AACAA68FFFFFFFFFFFFFFFF",
    16,
)
_Q = (_P - 1) // 2
_G = 2
_ELEMENT_BYTES = 256
#: An element on the wire: 256 bytes, big-endian, as lowercase hex.
ELEMENT_HEX_LEN = 2 * _ELEMENT_BYTES
_TAG_BYTES = 32


def _expand(label: bytes, length: int) -> bytes:
    """``length`` bytes from SHA-256 in counter mode over ``label``."""
    out = b""
    counter = 0
    while len(out) < length:
        out += hashlib.sha256(counter.to_bytes(4, "big") + label).digest()
        counter += 1
    return out[:length]


def _hash_to_group(label: bytes) -> int:
    """A subgroup element nobody knows the discrete logarithm of: a hash,
    reduced mod p and squared (squares are exactly the order-q subgroup of
    a safe-prime group)."""
    value = int.from_bytes(_expand(CONTEXT + b"\n" + label, _ELEMENT_BYTES + 32), "big")
    element = pow(value % _P, 2, _P)
    if element in (0, 1):      # pragma: no cover — would need a broken hash
        raise RuntimeError("degenerate group element")
    return element


_M = _hash_to_group(b"M")
_N = _hash_to_group(b"N")


def valid_code(code: object) -> bool:
    """Exactly :data:`CODE_DIGITS` ASCII digits (``str.isdigit`` alone would
    also take superscripts and other scripts' digits)."""
    return (
        isinstance(code, str)
        and len(code) == CODE_DIGITS
        and code.isascii()
        and code.isdigit()
    )


def _code_scalar(code: str) -> int:
    if not valid_code(code):
        raise ValueError(f"an approval code is {CODE_DIGITS} digits")
    digest = _expand(CONTEXT + b"\ncode\n" + code.encode("ascii"), 64)
    return int.from_bytes(digest, "big") % (_Q - 1) + 1


def _valid_element(value: int) -> bool:
    """In range and in the prime-order subgroup: refuses 0, 1, p-1 and
    every element of the small subgroups, which would otherwise let a
    party pin the shared secret to a value it can enumerate."""
    return 1 < value < _P - 1 and pow(value, _Q, _P) == 1


def encode(element: int) -> str:
    return element.to_bytes(_ELEMENT_BYTES, "big").hex()


def decode(text: object) -> int | None:
    """The element a peer sent, or None for anything that is not exactly a
    valid subgroup element in our encoding."""
    if not isinstance(text, str) or len(text) != ELEMENT_HEX_LEN:
        return None
    try:
        raw = bytes.fromhex(text)
    except ValueError:
        return None
    value = int.from_bytes(raw, "big")
    return value if _valid_element(value) else None


def _lp(data: bytes) -> bytes:
    return len(data).to_bytes(8, "little") + data


def token_digest(token: str) -> str:
    """``SHA-256(token)`` as lowercase hex: the core's stored form of a
    pairing token, and what a version-2 transcript binds."""
    return hashlib.sha256(token.encode()).hexdigest()


def _token_hash_bytes(token_hash: object) -> bytes:
    if not isinstance(token_hash, str) or len(token_hash) != 64:
        raise ValueError("a token hash is 64 hex characters")
    try:
        return bytes.fromhex(token_hash)
    except ValueError:
        raise ValueError("a token hash is 64 hex characters") from None


def _support_key_bytes(support_key: object) -> bytes:
    """The support key as a version-3 transcript binds it: the hello's
    string as sent, UTF-8 (empty for none)."""
    if not isinstance(support_key, str) or len(support_key) > 256:
        raise ValueError("a support key is a short string ('' for none)")
    return support_key.encode("utf-8")


def _check_versioned(token_hash: object, support_key: object) -> None:
    if support_key is not None:
        if token_hash is None:
            raise ValueError("a support-key-bound run binds the token as well")
        _support_key_bytes(support_key)
    if token_hash is not None:
        _token_hash_bytes(token_hash)


def _tags(*, room_id: str, fingerprint: str, x_elem: int, y_elem: int,
          z_elem: int, w: int, token_hash: str | None = None,
          support_key: str | None = None) -> tuple[bytes, bytes]:
    """The two confirmation tags. With ``token_hash`` (version 2) the
    transcript binds the pairing token too, under its own context; with
    ``support_key`` as well (version 3, ``""`` for a device with none) it
    binds the support key after the token."""
    _check_versioned(token_hash, support_key)
    if token_hash is None:
        head = CONTEXT
    elif support_key is None:
        head = CONTEXT_V2 + _lp(_token_hash_bytes(token_hash))
    else:
        head = (CONTEXT_V3 + _lp(_token_hash_bytes(token_hash))
                + _lp(_support_key_bytes(support_key)))
    transcript = (
        head
        + _lp(room_id.encode("utf-8"))
        + _lp(fingerprint.encode("utf-8"))
        + _lp(x_elem.to_bytes(_ELEMENT_BYTES, "big"))
        + _lp(y_elem.to_bytes(_ELEMENT_BYTES, "big"))
        + _lp(z_elem.to_bytes(_ELEMENT_BYTES, "big"))
        + _lp(w.to_bytes(_ELEMENT_BYTES, "big"))
    )
    key = hashlib.sha256(transcript).digest()
    device = hmac.new(key, b"device confirms", hashlib.sha256).digest()
    server = hmac.new(key, b"server confirms", hashlib.sha256).digest()
    return device, server


def _tag_bytes(tag: object) -> bytes | None:
    if not isinstance(tag, str) or len(tag) != 2 * _TAG_BYTES:
        return None
    try:
        return bytes.fromhex(tag)
    except ValueError:
        return None


def _secret_exponent() -> int:
    return secrets.randbelow(_Q - 1) + 1


class DeviceExchange:
    """One run, the device's side. Make a new one for every connection: the
    secret exponent must never be reused."""

    def __init__(self, code: str) -> None:
        self._w = _code_scalar(code)
        self._x = _secret_exponent()
        self._x_elem = pow(_G, self._x, _P) * pow(_M, self._w, _P) % _P
        self._server_tag: bytes | None = None

    @property
    def message(self) -> str:
        """``X`` — what goes in the hello as ``approval_pake``."""
        return encode(self._x_elem)

    def answer(self, server_message: object, *, room_id: str, fingerprint: str,
               token_hash: str | None = None,
               support_key: str | None = None) -> str | None:
        """The device's confirmation for a server's ``Y``, or None when
        ``Y`` is not a valid element (nothing is computed from it then).

        ``fingerprint`` is the identity this device proved for the address
        it dialled — never one the server merely claims in this exchange.
        ``token_hash`` (:func:`token_digest` of the token in this device's
        hello) makes it a version-2 run: pass it ONLY to a server that
        signed ``pairing_proof >= 2``. ``support_key`` as well (the
        ``support_key`` string in that hello, ``""`` for none) makes it a
        version-3 run: ONLY to a server that signed ``pairing_proof >= 3``."""
        _check_versioned(token_hash, support_key)
        y_elem = decode(server_message)
        if y_elem is None:
            return None
        n_w = pow(_N, self._w, _P)
        z_elem = pow(y_elem * pow(n_w, -1, _P) % _P, self._x, _P)
        device, server = _tags(
            room_id=room_id, fingerprint=fingerprint, x_elem=self._x_elem,
            y_elem=y_elem, z_elem=z_elem, w=self._w, token_hash=token_hash,
            support_key=support_key,
        )
        self._server_tag = server
        return device.hex()

    def server_proved(self, tag: object) -> bool:
        """Whether ``tag`` is the server's half: it knew the code. False
        before :meth:`answer`, and for anything malformed."""
        got = _tag_bytes(tag)
        if self._server_tag is None or got is None:
            return False
        return hmac.compare_digest(self._server_tag, got)


class ServerExchange:
    """One run, the server's side, for the code a person typed.

    Raises ValueError for a code that is not six digits or a device message
    that is not a valid element — the caller refuses the run then.

    ``token_hash`` (the stored form of the token in the device's hello)
    makes it a version-2 run: only for a hello that says
    ``pairing_proof: 2``. ``support_key`` as well (the hello's
    ``support_key`` string exactly as received, ``""`` for none) makes it
    a version-3 run: only for a hello that says ``pairing_proof: 3``."""

    def __init__(self, code: str, device_message: object, *, room_id: str,
                 fingerprint: str, token_hash: str | None = None,
                 support_key: str | None = None) -> None:
        w = _code_scalar(code)
        _check_versioned(token_hash, support_key)
        x_elem = decode(device_message)
        if x_elem is None:
            raise ValueError("the device's message is not a group element")
        y = _secret_exponent()
        self._y_elem = pow(_G, y, _P) * pow(_N, w, _P) % _P
        m_w = pow(_M, w, _P)
        z_elem = pow(x_elem * pow(m_w, -1, _P) % _P, y, _P)
        self._device_tag, self._server_tag = _tags(
            room_id=room_id, fingerprint=fingerprint, x_elem=x_elem,
            y_elem=self._y_elem, z_elem=z_elem, w=w, token_hash=token_hash,
            support_key=support_key,
        )
        self._device_proved = False

    @property
    def message(self) -> str:
        """``Y`` — what goes in the ``approval_challenge`` frame."""
        return encode(self._y_elem)

    def device_proved(self, tag: object) -> bool:
        """Constant-time check of the device's confirmation: it holds the
        same code the person typed."""
        got = _tag_bytes(tag)
        ok = got is not None and hmac.compare_digest(self._device_tag, got)
        self._device_proved = self._device_proved or ok
        return ok

    @property
    def proof(self) -> str:
        """The server's half, given out only AFTER the device's half
        checked out — so a run that fails on the device's side hands the
        device nothing to test codes against."""
        if not self._device_proved:
            raise PermissionError("the device has not confirmed the code")
        return self._server_tag.hex()
