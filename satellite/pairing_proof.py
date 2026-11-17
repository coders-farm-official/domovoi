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
VERSION = 1

CONTEXT = b"domovoi-pairing-proof-v1"

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


def _tags(*, room_id: str, fingerprint: str, x_elem: int, y_elem: int,
          z_elem: int, w: int) -> tuple[bytes, bytes]:
    transcript = (
        CONTEXT
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

    def answer(self, server_message: object, *, room_id: str, fingerprint: str) -> str | None:
        """The device's confirmation for a server's ``Y``, or None when
        ``Y`` is not a valid element (nothing is computed from it then).

        ``fingerprint`` is the identity this device proved for the address
        it dialled — never one the server merely claims in this exchange."""
        y_elem = decode(server_message)
        if y_elem is None:
            return None
        n_w = pow(_N, self._w, _P)
        z_elem = pow(y_elem * pow(n_w, -1, _P) % _P, self._x, _P)
        device, server = _tags(
            room_id=room_id, fingerprint=fingerprint, x_elem=self._x_elem,
            y_elem=y_elem, z_elem=z_elem, w=self._w,
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
    that is not a valid element — the caller refuses the run then."""

    def __init__(self, code: str, device_message: object, *, room_id: str,
                 fingerprint: str) -> None:
        w = _code_scalar(code)
        x_elem = decode(device_message)
        if x_elem is None:
            raise ValueError("the device's message is not a group element")
        y = _secret_exponent()
        self._y_elem = pow(_G, y, _P) * pow(_N, w, _P) % _P
        m_w = pow(_M, w, _P)
        z_elem = pow(x_elem * pow(m_w, -1, _P) % _P, y, _P)
        self._device_tag, self._server_tag = _tags(
            room_id=room_id, fingerprint=fingerprint, x_elem=x_elem,
            y_elem=self._y_elem, z_elem=z_elem, w=w,
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
