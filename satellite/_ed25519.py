"""Ed25519 (RFC 8032) in pure Python — the fallback when ``cryptography``
is not installed.

Why this exists at all. The server identity that satellites pin has to be
verifiable in three places with three different dependency budgets:

  * the core, which can have ``cryptography`` from PyPI;
  * the satellite venv on a Pi — ``cryptography`` ships aarch64 wheels, but
    a 32-bit Pi OS (armv7l) has none and would need a Rust toolchain;
  * this repo's test environment, which must not grow a build dependency.

So the identity layer prefers ``cryptography`` and falls back to this. The
two produce identical signatures — the RFC 8032 test vectors in
``domovoi/tests/test_server_identity.py`` pin that.

This implementation is straightforward field arithmetic and is **not**
constant-time. That is fine for verification, which only ever touches
public data. For signing, :mod:`domovoi.server_identity` uses this only
when ``cryptography`` is missing, and says so in its log line: a timing
side channel on the core's signing key needs local code execution on the
core host, which is already game over.

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


def verify(public: bytes, message: bytes, signature: bytes) -> bool:
    """Whether ``signature`` is this key's signature over ``message``.

    Never raises: a malformed key, a malformed signature and a wrong
    signature are all the same answer to the caller — no."""
    if len(public) != KEY_SIZE or len(signature) != SIGNATURE_SIZE:
        return False
    point_a = _decode_point(public)
    point_r = _decode_point(signature[:32])
    if point_a is None or point_r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= L:
        return False
    k = int.from_bytes(
        hashlib.sha512(signature[:32] + public + message).digest(), "little"
    ) % L
    lhs = _scalar_mult(BASE, s)
    rhs = _add(point_r, _scalar_mult(point_a, k))
    return _encode_point(lhs) == _encode_point(rhs)
