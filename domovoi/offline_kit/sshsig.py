"""Check the release's signed manifest on a kit, with the standard library.

A release carries ``DomovoiSetup.manifest.json`` (``{kind, schema, tag,
version, commit, file, size, sha256}``) and a detached Ed25519 signature
over it, ``DomovoiSetup.manifest.json.sig``, made by
scripts/release/finish_release.py with ``ssh-keygen -Y sign`` (OpenSSH's
SSHSIG format, PROTOCOL.sshsig). The Mac package's manifest has the same
fields. The kit copies them unchanged into ``release/``.

``RELEASE_KEY`` is Domovoi's release key, the one ``install.sh`` pins and
``windows/installer/release-signing.pub`` holds (a test keeps them the
same). No new key: the kit's trust chain is the release's.

Python's standard library has no Ed25519, so the verification is the
curve arithmetic itself, the same check as windows/common/Ed25519.cs (and
OpenSSH's ref10): S below the group order, A and R canonical points on the
curve, and the encoding of [S]B - [k]A equal to R's bytes, with
k = SHA-512(R || A || M) mod L. Verification only: nothing secret goes
through it, so it needs no constant-time care. The tests run RFC 8032's
vectors and the release-signature fixtures ssh-keygen made through it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import struct

#: Domovoi's release key (windows/installer/release-signing.pub).
RELEASE_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILvBNVEbpKFujjOMo0O6hZ68A2HQDzuCPFbKabVts6fS"
RELEASE_SIGNER = "domovoi-release"

_P = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)

Point = tuple[int, int, int, int]  # extended coordinates X, Y, Z, T


def _recover_x(y: int, sign: int) -> int | None:
    """RFC 8032 5.1.3: x from y and its sign bit; None when y is no point's."""
    if y >= _P:
        return None
    yy = y * y % _P
    u = (yy - 1) % _P
    v = (_D * yy + 1) % _P
    v3 = v * v * v % _P
    x = u * v3 * pow(u * v3 * v3 * v % _P, (_P - 5) // 8, _P) % _P
    vxx = v * x * x % _P
    if vxx == u:
        pass
    elif vxx == (-u) % _P:
        x = x * _SQRT_M1 % _P
    else:
        return None
    if x == 0 and sign == 1:
        return None
    if x & 1 != sign:
        x = _P - x
    return x


def _base() -> Point:
    y = 4 * pow(5, _P - 2, _P) % _P
    x = _recover_x(y, 0)
    assert x is not None
    return (x, y, 1, x * y % _P)


_B = _base()
_IDENTITY: Point = (0, 1, 1, 0)


def _add(p: Point, q: Point) -> Point:
    """RFC 8032 5.1.4."""
    a = (p[1] - p[0]) * (q[1] - q[0]) % _P
    b = (p[1] + p[0]) * (q[1] + q[0]) % _P
    c = p[3] * 2 * _D * q[3] % _P
    d = p[2] * 2 * q[2] % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _mul(p: Point, k: int) -> Point:
    r = _IDENTITY
    while k > 0:
        if k & 1:
            r = _add(r, p)
        p = _add(p, p)
        k >>= 1
    return r


def _decode(b: bytes) -> Point | None:
    n = int.from_bytes(b, "little")
    sign = n >> 255
    y = n & ((1 << 255) - 1)
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


def _encode(p: Point) -> bytes:
    zi = pow(p[2], _P - 2, _P)
    x, y = p[0] * zi % _P, p[1] * zi % _P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def ed25519_verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """Whether ``signature`` (64 bytes) is ``public_key``'s (32 bytes)
    signature of ``message``."""
    if len(public_key) != 32 or len(signature) != 64:
        return False
    a = _decode(public_key)
    if a is None or _decode(signature[:32]) is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _L:
        return False
    k = int.from_bytes(hashlib.sha512(signature[:32] + public_key + message).digest(), "little") % _L
    neg_ka = _mul(a, k)
    neg_ka = ((-neg_ka[0]) % _P, neg_ka[1], neg_ka[2], (-neg_ka[3]) % _P)
    check = _add(_mul(_B, s), neg_ka)
    return hmac.compare_digest(_encode(check), signature[:32])


class _Reader:
    """SSH wire strings: a big-endian uint32 length, then the bytes."""

    def __init__(self, data: bytes):
        self.data, self.pos, self.bad = data, 0, False

    def raw(self, n: int) -> bytes:
        if self.bad or self.pos + n > len(self.data):
            self.bad = True
            return b""
        out = self.data[self.pos:self.pos + n]
        self.pos += n
        return out

    def uint32(self) -> int:
        b = self.raw(4)
        return struct.unpack(">I", b)[0] if len(b) == 4 else 0

    def string(self) -> bytes:
        return self.raw(self.uint32())

    @property
    def at_end(self) -> bool:
        return not self.bad and self.pos == len(self.data)


def _wire(b: bytes) -> bytes:
    return struct.pack(">I", len(b)) + b


def parse_public_key(line: str) -> bytes | None:
    """The 32 raw bytes of an ``ssh-ed25519 <base64> [comment]`` line (the
    first that isn't blank or a comment), or None."""
    for raw in line.splitlines():
        parts = raw.strip().split()
        if not parts or parts[0].startswith("#"):
            continue
        if len(parts) < 2 or parts[0] != "ssh-ed25519":
            return None
        try:
            blob = base64.b64decode(parts[1], validate=True)
        except ValueError:
            return None
        r = _Reader(blob)
        kind, key = r.string(), r.string()
        if kind != b"ssh-ed25519" or len(key) != 32 or not r.at_end:
            return None
        return key
    return None


_BEGIN = "-----BEGIN SSH SIGNATURE-----"
_END = "-----END SSH SIGNATURE-----"


def sshsig_problem(armored: bytes, message: bytes, key: bytes, namespace: str) -> str | None:
    """None when ``armored`` is a good SSHSIG of ``message`` by ``key``
    under ``namespace``; otherwise why not, in plain words."""
    try:
        text = armored.decode("ascii").replace("\r", "")
    except UnicodeDecodeError:
        return "its signature file isn't an SSH signature"
    a, z = text.find(_BEGIN), text.find(_END)
    if a < 0 or z < a:
        return "its signature file isn't an SSH signature"
    try:
        blob = base64.b64decode(text[a + len(_BEGIN):z].replace("\n", "").strip(), validate=True)
    except ValueError:
        return "its signature file isn't an SSH signature"
    r = _Reader(blob)
    magic, version = r.raw(6), r.uint32()
    pub, ns, reserved, hash_alg, sig_blob = r.string(), r.string(), r.string(), r.string(), r.string()
    if not r.at_end or magic != b"SSHSIG" or version != 1:
        return "its signature file isn't an SSH signature"
    pr = _Reader(pub)
    pub_type, pub_key = pr.string(), pr.string()
    if not pr.at_end or pub_type != b"ssh-ed25519":
        return "it isn't signed with an Ed25519 key"
    if not hmac.compare_digest(pub_key, key):
        return "it is signed with a key other than Domovoi's release key"
    if ns != namespace.encode("ascii"):
        shown = "".join(c for c in ns.decode("ascii", "replace") if " " <= c < "\x7f")[:60]
        return f'its signature is for something else ("{shown}", not "{namespace}")'
    sr = _Reader(sig_blob)
    sig_type, sig = sr.string(), sr.string()
    if not sr.at_end or sig_type != b"ssh-ed25519" or len(sig) != 64:
        return "its signature is malformed"
    if hash_alg == b"sha512":
        digest = hashlib.sha512(message).digest()
    elif hash_alg == b"sha256":
        digest = hashlib.sha256(message).digest()
    else:
        return "its signature uses an unknown hash"
    signed = b"SSHSIG" + _wire(ns) + _wire(reserved) + _wire(hash_alg) + _wire(digest)
    if not ed25519_verify(key, signed, sig):
        return "its signature doesn't match the file (it was changed, or signed by someone else)"
    return None
