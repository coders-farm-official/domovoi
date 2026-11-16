"""Ed25519 (RFC 8032) in pure Python — the satellites' fallback when
``cryptography`` can't be installed, and the encoding checks every
verifier runs first.

Where it runs:

  * the core (the server) signs and verifies with ``cryptography`` only,
    through :mod:`domovoi.crypto_backend`, and never signs with this. A
    server without ``cryptography`` is a broken install: signing is refused
    there, and only a signature *check* falls back to this, logged at
    ERROR at boot;
  * a satellite verifies the server with ``cryptography`` when its venv has
    it, else with this — the case it exists for is a 32-bit Pi OS image
    that has no wheel for it (:mod:`satellite.server_identity`);
  * the Pi's root helper (``domovoi_ed25519.py``, copied from the satellite
    twin by satellite-media prepare) uses it as is, stdlib only;
  * :func:`precheck` runs in front of ``cryptography`` everywhere, so both
    backends refuse exactly the same keys and signatures.

The two produce identical signatures — the RFC 8032 test vectors and the
cross-backend tests in ``domovoi/tests/test_crypto_backend.py`` pin that.

This implementation is straightforward field arithmetic and is **not**
constant-time. That is fine for verification, which only ever touches
public data; it is why the server never signs with it.

There is a byte-identical twin at ``satellite/_ed25519.py``. The satellite
tree is mirrored to the Pi on its own and cannot import ``domovoi.*``, so
the duplication is deliberate — the same reason
``satellite.portal_transport.generate_approval_code`` is a twin of the one
in ``domovoi.satellite_media.overlay``. Change both or neither; the
round-trip test in each tree fails if they drift.
"""

from __future__ import annotations

import hashlib

# Curve25519 field prime and the prime order of the base point's subgroup.
P = 2 ** 255 - 19
L = 2 ** 252 + 27742317777372353535851937790883648493

_D = (-121665 * pow(121666, P - 2, P)) % P
_SQRT_M1 = pow(2, (P - 1) // 4, P)

# The neutral element in extended (X : Y : Z : T) coordinates.
_NEUTRAL = (0, 1, 1, 0)

KEY_SIZE = 32
SIGNATURE_SIZE = 64


def _recover_x(y: int, sign: int) -> int | None:
    """The x that goes with ``y`` on the curve, with the requested parity,
    or None when ``y`` is not on the curve at all."""
    if y >= P:
        return None
    xx = (y * y - 1) * pow(_D * y * y + 1, P - 2, P)
    x = pow(xx, (P + 3) // 8, P)
    if (x * x - xx) % P != 0:
        x = (x * _SQRT_M1) % P
    if (x * x - xx) % P != 0:
        return None
    if x == 0 and sign:
        return None
    if x % 2 != sign:
        x = P - x
    return x


def _add(p1: tuple[int, int, int, int],
         p2: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """Unified extended-coordinate addition (add-2008-hwcd-3, a = -1).
    Unified means it is also correct for doubling, so scalar
    multiplication needs no second formula."""
    x1, y1, z1, t1 = p1
    x2, y2, z2, t2 = p2
    a = (y1 - x1) * (y2 - x2) % P
    b = (y1 + x1) * (y2 + x2) % P
    c = 2 * t1 * t2 * _D % P
    dd = 2 * z1 * z2 % P
    e, f, g, h = b - a, dd - c, dd + c, b + a
    return (e * f % P, g * h % P, f * g % P, e * h % P)


def _scalar_mult(point: tuple[int, int, int, int], e: int) -> tuple[int, int, int, int]:
    result = _NEUTRAL
    while e > 0:
        if e & 1:
            result = _add(result, point)
        point = _add(point, point)
        e >>= 1
    return result


_BASE_Y = 4 * pow(5, P - 2, P) % P
_BASE_X = _recover_x(_BASE_Y, 0) or 0
BASE = (_BASE_X, _BASE_Y, 1, _BASE_X * _BASE_Y % P)


def _encode_point(point: tuple[int, int, int, int]) -> bytes:
    x, y, z, _t = point
    zi = pow(z, P - 2, P)
    x, y = x * zi % P, y * zi % P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decode_point(data: bytes) -> tuple[int, int, int, int] | None:
    if len(data) != KEY_SIZE:
        return None
    value = int.from_bytes(data, "little")
    sign = (value >> 255) & 1
    y = value & ((1 << 255) - 1)
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % P)


def _expand(seed: bytes) -> tuple[int, bytes]:
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def public_key(seed: bytes) -> bytes:
    """The 32-byte public key for a 32-byte private seed."""
    if len(seed) != KEY_SIZE:
        raise ValueError("an ed25519 private seed is 32 bytes")
    a, _prefix = _expand(seed)
    return _encode_point(_scalar_mult(BASE, a))


def sign(seed: bytes, message: bytes) -> bytes:
    """A 64-byte detached signature over ``message``."""
    if len(seed) != KEY_SIZE:
        raise ValueError("an ed25519 private seed is 32 bytes")
    a, prefix = _expand(seed)
    encoded_a = _encode_point(_scalar_mult(BASE, a))
    r = int.from_bytes(hashlib.sha512(prefix + message).digest(), "little") % L
    encoded_r = _encode_point(_scalar_mult(BASE, r))
    k = int.from_bytes(
        hashlib.sha512(encoded_r + encoded_a + message).digest(), "little"
    ) % L
    s = (r + k * a) % L
    return encoded_r + int.to_bytes(s, 32, "little")


_IDENTITY_ENCODED = bytes([1]) + bytes(31)


def _small_order(point: tuple[int, int, int, int]) -> bool:
    """Whether eight times ``point`` is the identity: the identity itself
    and the other points of order 2, 4 or 8."""
    for _ in range(3):
        point = _add(point, point)
    return _encode_point(point) == _IDENTITY_ENCODED


def small_order_part(public: bytes, signature: bytes) -> bool:
    """Whether the key, or the signature's R, is a point of small order.
    With such a key a signature can hold for any message (the identity key
    with R the identity and S = 0 verifies everything), so a verifier
    refuses it whatever does the arithmetic: :func:`verify` here, and
    :func:`precheck`, which the identity layers run before they hand off
    to ``cryptography`` (review of
    wf/win-sat-installer, round 2; the Windows satellite Setup's verifier
    refuses the same, and domovoi/tests/fixtures/
    ed25519-setup-vectors.json holds both to it)."""
    if len(public) != KEY_SIZE or len(signature) != SIGNATURE_SIZE:
        return False
    for encoded in (public, signature[:32]):
        point = _decode_point(encoded)
        if point is not None and _small_order(point):
            return True
    return False


def _parse(
    public: bytes, signature: bytes
) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int], int] | None:
    """(A, R, S) when the key and signature are encodings a verifier goes
    on to check, else None: the right lengths, A and R canonical points of
    the curve (``y`` below the field prime, no "negative zero" x), neither
    of small order (:func:`small_order_part`), S below L."""
    if len(public) != KEY_SIZE or len(signature) != SIGNATURE_SIZE:
        return None
    point_a = _decode_point(public)
    point_r = _decode_point(signature[:32])
    if point_a is None or point_r is None:
        return None
    if _small_order(point_a) or _small_order(point_r):
        return None
    s = int.from_bytes(signature[32:], "little")
    if s >= L:
        return None
    return point_a, point_r, s


def precheck(public: bytes, signature: bytes) -> bool:
    """Whether the key and signature get as far as the group equation:
    everything :func:`verify` refuses before it, and nothing more.

    An identity layer that hands the equation to ``cryptography`` runs this
    first, so both backends refuse the same inputs: OpenSSL decodes a
    non-canonical key encoding (``y`` of p or more, or x = 0 with the sign
    bit set) as the point it stands for, and with the identity spelled that
    way any R = [S]B verifies for any message. Public data only, so it need
    not be constant-time."""
    return _parse(public, signature) is not None


def verify(public: bytes, message: bytes, signature: bytes) -> bool:
    """Whether ``signature`` is this key's signature over ``message``.

    Never raises: a malformed key, a malformed signature, a key or R of
    small order (:func:`small_order_part`) and a wrong signature are all
    the same answer to the caller — no."""
    parsed = _parse(public, signature)
    if parsed is None:
        return False
    point_a, point_r, s = parsed
    k = int.from_bytes(
        hashlib.sha512(signature[:32] + public + message).digest(), "little"
    ) % L
    lhs = _scalar_mult(BASE, s)
    rhs = _add(point_r, _scalar_mult(point_a, k))
    return _encode_point(lhs) == _encode_point(rhs)
