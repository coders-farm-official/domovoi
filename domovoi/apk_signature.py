"""Who signed an Android APK: APK Signature Scheme v2 and v3, checked in
pure Python.

The household's server hands phones the Domovoi Android app
(domovoi/android_app.py). Before it keeps or serves an APK it holds the
file to the release manifest (``android/release.json``): its size and
SHA-256, and **who signed it** — the signing certificate's SHA-256 must be
the release key's pinned one. That last check is only worth something if
it reads the signature the way Android does, so :func:`verify` does what
the platform's verifier does before it believes a certificate:

1. finds the APK Signing Block (the bytes between the last ZIP entry and
   the central directory, ending ``APK Sig Block 42``);
2. takes the v2 block (and the v3 block when there is one) and its one
   signer;
3. verifies the signer's signature over its *signed data* with the
   signer's public key;
4. checks that the first certificate in the signed data carries that same
   public key;
5. recomputes the APK's content digest (1 MiB chunks of the ZIP entries,
   the central directory and the end record, with the central directory's
   offset read as the signing block's) and compares it with the digest
   the signed data holds.

Only then is the certificate's SHA-256 returned. A hash read from the same
file proves nothing; a signature by the pinned key over a digest of every
byte does.

Scope, on purpose narrow (a refusal names what is missing):

* RSA keys with PKCS#1 v1.5 signatures, SHA-256 or SHA-512 — what
  ``apksigner`` makes for the RSA release key ``android/README.md`` has
  you create (SHA-512 for keys over 3072 bits). RSA-PSS, ECDSA and DSA are
  refused, as are keys under 2048 or over 8192 bits and exponents over 2^32.
* A v2 block is required: Android 8 (the app's minimum) reads v2, not v3.
  A v3 block, when present, is verified too and must name the same
  certificate (key rotation is not supported); a v3 block the v2 signer's
  stripping-protection attribute names must be present; any v3.1 block is
  refused (an Android 13+ phone would believe its signer instead).
* Exactly one signer.

Pure Python (the core has no ``cryptography`` dependency, see
domovoi/_ed25519.py); RSA verification touches only public data, so it
need not be constant-time. Reference: source.android.com, "APK Signature
Scheme v2" and "v3".
"""

from __future__ import annotations

import hashlib
import hmac
import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

APK_SIG_BLOCK_MAGIC = b"APK Sig Block 42"
V2_BLOCK_ID = 0x7109871A
V3_BLOCK_ID = 0xF05368C0
#: APK Signature Scheme v3.1 (key rotation aimed at Android 13+): an
#: Android 13+ phone believes its signer over v3's, so an APK carrying one
#: is refused outright (this verifier supports no rotation).
V31_BLOCK_ID = 0x1B93AD61
#: A v2 signer's attribute naming a newer scheme that must be present too
#: (Android refuses the APK when that block was stripped).
STRIPPING_PROTECTION_ATTR_ID = 0xBEEFF00D

#: Signature algorithm ids this verifier checks: (digest, Android's
#: preference order — higher wins when a signer lists both).
SUPPORTED = {
    0x0103: ("sha256", 1),  # RSASSA-PKCS1-v1_5 with SHA2-256
    0x0104: ("sha512", 2),  # RSASSA-PKCS1-v1_5 with SHA2-512
}
#: Ids Android knows that this verifier refuses, named in the refusal.
KNOWN_UNSUPPORTED = {
    0x0101: "RSA-PSS with SHA-256", 0x0102: "RSA-PSS with SHA-512",
    0x0201: "ECDSA with SHA-256", 0x0202: "ECDSA with SHA-512",
    0x0301: "DSA with SHA-256",
    0x0421: "RSA PKCS#1 v1.5 with SHA-256 (verity)", 0x0423: "ECDSA with SHA-256 (verity)",
    0x0425: "DSA with SHA-256 (verity)",
}

MIN_RSA_BITS = 2048
#: Caps like Java's provider: no key here is bigger, and pow() with a
#: huge modulus or exponent is a cheap way to burn the server's CPU.
MAX_RSA_BITS = 8192
MAX_RSA_EXPONENT = 2 ** 32
_CHUNK = 1024 * 1024
_EOCD_MIN = 22
_EOCD_SIG = b"PK\x05\x06"
_ZIP64_LOCATOR_SIG = b"PK\x06\x07"
# DigestInfo prefixes, RFC 8017 section 9.2 note 1.
_DIGEST_INFO = {
    "sha256": bytes.fromhex("3031300d060960864801650304020105000420"),
    "sha512": bytes.fromhex("3051300d060960864801650304020305000440"),
}
_RSA_OID = bytes.fromhex("2a864886f70d010101")  # 1.2.840.113549.1.1.1


class ApkSignatureError(ValueError):
    """The APK is not signed in a way this verifier accepts, or its
    signature does not hold. The message is safe to show."""


@dataclass(frozen=True)
class Verified:
    """A verified signature: the signer's certificate (its SHA-256, the
    value ``apksigner verify --print-certs`` prints as "certificate SHA-256
    digest"), the schemes that vouched for it, the key size."""

    cert_sha256: str
    schemes: tuple[int, ...]
    key_bits: int


# ─── the ZIP's end and the signing block ──────────────────────────────────

def _read_at(f: BinaryIO, offset: int, size: int) -> bytes:
    f.seek(offset)
    data = f.read(size)
    if len(data) != size:
        raise ApkSignatureError("the file ends early")
    return data


def _find_eocd(f: BinaryIO, file_size: int) -> tuple[int, bytes]:
    """The end-of-central-directory record: (its offset, its bytes). The
    record whose comment runs exactly to the end of the file, as Android's
    ZipUtils finds it."""
    if file_size < _EOCD_MIN:
        raise ApkSignatureError("the file is too small to be an APK")
    tail_len = min(file_size, _EOCD_MIN + 0xFFFF)
    tail = _read_at(f, file_size - tail_len, tail_len)
    for comment_len in range(0, tail_len - _EOCD_MIN + 1):
        pos = tail_len - _EOCD_MIN - comment_len
        if tail[pos:pos + 4] == _EOCD_SIG and struct.unpack_from("<H", tail, pos + 20)[0] == comment_len:
            return file_size - tail_len + pos, tail[pos:]
    raise ApkSignatureError("the file is not a ZIP archive (no end of central directory)")


def _signing_block(f: BinaryIO, file_size: int) -> tuple[int, int, int, bytes, bytes]:
    """(signing block offset, central directory offset, end record
    offset, the end record, the block's ID-value pairs)."""
    eocd_off, eocd = _find_eocd(f, file_size)
    if eocd_off >= 20 and _read_at(f, eocd_off - 20, 4) == _ZIP64_LOCATOR_SIG:
        raise ApkSignatureError("ZIP64 APKs are not supported")
    cd_size, cd_off = struct.unpack_from("<II", eocd, 12)
    if cd_off + cd_size != eocd_off:
        raise ApkSignatureError("the ZIP central directory is not right before its end record")
    if cd_off < 32:
        raise ApkSignatureError("the APK has no signing block (it is not signed with v2 or v3)")
    footer = _read_at(f, cd_off - 24, 24)
    if footer[8:] != APK_SIG_BLOCK_MAGIC:
        raise ApkSignatureError("the APK has no signing block (it is not signed with v2 or v3)")
    size_in_footer = struct.unpack_from("<Q", footer, 0)[0]
    if size_in_footer < 24 or size_in_footer > cd_off - 8:
        raise ApkSignatureError("the APK signing block's size is out of range")
    block_off = cd_off - size_in_footer - 8
    head = _read_at(f, block_off, 8)
    if struct.unpack_from("<Q", head, 0)[0] != size_in_footer:
        raise ApkSignatureError("the APK signing block's two sizes differ")
    pairs = _read_at(f, block_off + 8, size_in_footer - 24)
    return block_off, cd_off, eocd_off, eocd, pairs


def _scheme_blocks(pairs: bytes) -> dict[int, bytes]:
    """The v2 and v3 values among the block's ID-value pairs; each at most
    once. A v3.1 block is refused (see :data:`V31_BLOCK_ID`)."""
    out: dict[int, bytes] = {}
    pos = 0
    while pos < len(pairs):
        if len(pairs) - pos < 8:
            raise ApkSignatureError("a truncated entry in the APK signing block")
        (length,) = struct.unpack_from("<Q", pairs, pos)
        pos += 8
        if length < 4 or length > len(pairs) - pos:
            raise ApkSignatureError("an entry in the APK signing block has a bad length")
        (block_id,) = struct.unpack_from("<I", pairs, pos)
        if block_id == V31_BLOCK_ID:
            raise ApkSignatureError(
                "the APK carries an APK Signature Scheme v3.1 block (key rotation), which is not supported"
            )
        if block_id in (V2_BLOCK_ID, V3_BLOCK_ID):
            if block_id in out:
                raise ApkSignatureError("the APK signing block names a scheme twice")
            out[block_id] = pairs[pos + 4:pos + length]
        pos += length
    return out


# ─── length-prefixed fields ───────────────────────────────────────────────

class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def remaining(self) -> int:
        return len(self.data) - self.pos

    def u32(self) -> int:
        if self.remaining() < 4:
            raise ApkSignatureError("a truncated signature record")
        (v,) = struct.unpack_from("<I", self.data, self.pos)
        self.pos += 4
        return v

    def prefixed(self) -> bytes:
        n = self.u32()
        if n > self.remaining():
            raise ApkSignatureError("a signature record runs past its end")
        v = self.data[self.pos:self.pos + n]
        self.pos += n
        return v

    def sequence(self) -> list[bytes]:
        inner = _Reader(self.prefixed())
        items = []
        while inner.remaining():
            items.append(inner.prefixed())
        return items


# ─── DER: just enough for an X.509 certificate's public key ───────────────

def _der(data: bytes, pos: int, want: int | None = None) -> tuple[int, int, int]:
    """(tag, value start, value end) of the DER element at ``pos``."""
    if pos + 2 > len(data):
        raise ApkSignatureError("a truncated certificate")
    tag = data[pos]
    if want is not None and tag != want:
        raise ApkSignatureError("the certificate is not DER as expected")
    if tag & 0x1F == 0x1F:
        raise ApkSignatureError("the certificate uses a tag this reader does not know")
    first = data[pos + 1]
    pos += 2
    if first < 0x80:
        length = first
    else:
        n = first & 0x7F
        if n == 0 or n > 4 or pos + n > len(data):
            raise ApkSignatureError("the certificate has a bad length")
        length = int.from_bytes(data[pos:pos + n], "big")
        pos += n
    if pos + length > len(data):
        raise ApkSignatureError("the certificate runs past its end")
    return tag, pos, pos + length


def certificate_public_key(cert: bytes) -> bytes:
    """The SubjectPublicKeyInfo of an X.509 certificate, as DER bytes."""
    _, start, end = _der(cert, 0, 0x30)               # Certificate
    _, pos, tbs_end = _der(cert, start, 0x30)          # TBSCertificate
    if pos < tbs_end and cert[pos] == 0xA0:            # [0] version
        pos = _der(cert, pos)[2]
    for want in (0x02, 0x30, 0x30, 0x30, 0x30):        # serial, signature, issuer, validity, subject
        pos = _der(cert, pos, want)[2]
    spki_start = pos
    spki_end = _der(cert, pos, 0x30)[2]                # subjectPublicKeyInfo
    if spki_end > tbs_end:
        raise ApkSignatureError("the certificate's public key runs past its body")
    return cert[spki_start:spki_end]


def rsa_public_key(spki: bytes) -> tuple[int, int]:
    """(modulus, exponent) of an RSA SubjectPublicKeyInfo."""
    _, start, end = _der(spki, 0, 0x30)
    if end != len(spki):
        raise ApkSignatureError("the public key has bytes after it")
    _, alg_start, alg_end = _der(spki, start, 0x30)
    _, oid_start, oid_end = _der(spki, alg_start, 0x06)
    if spki[oid_start:oid_end] != _RSA_OID:
        raise ApkSignatureError("the signing key is not an RSA key (only RSA release keys are supported)")
    _, bits_start, bits_end = _der(spki, alg_end, 0x03)
    if bits_end != end or bits_start >= bits_end or spki[bits_start] != 0:
        raise ApkSignatureError("the RSA public key is malformed")
    key = spki[bits_start + 1:bits_end]
    _, k_start, k_end = _der(key, 0, 0x30)
    if k_end != len(key):
        raise ApkSignatureError("the RSA public key is malformed")
    _, n_start, n_end = _der(key, k_start, 0x02)
    _, e_start, e_end = _der(key, n_end, 0x02)
    if e_end != k_end:
        raise ApkSignatureError("the RSA public key is malformed")
    if key[n_start] & 0x80 or key[e_start] & 0x80:
        raise ApkSignatureError("the RSA public key is negative")
    n = int.from_bytes(key[n_start:n_end], "big")
    e = int.from_bytes(key[e_start:e_end], "big")
    if n.bit_length() < MIN_RSA_BITS:
        raise ApkSignatureError(f"the RSA key is {n.bit_length()} bits; at least {MIN_RSA_BITS} are needed")
    if n.bit_length() > MAX_RSA_BITS:
        raise ApkSignatureError(f"the RSA key is {n.bit_length()} bits; at most {MAX_RSA_BITS} are accepted")
    if e < 3 or e % 2 == 0 or e > MAX_RSA_EXPONENT:
        raise ApkSignatureError("the RSA public exponent is not usable")
    return n, e


def rsa_pkcs1_v15_verify(n: int, e: int, signature: bytes, message: bytes, digest: str) -> bool:
    """RSASSA-PKCS1-v1_5 verification (RFC 8017 section 8.2.2): the whole
    encoded message is rebuilt and compared, never parsed."""
    k = (n.bit_length() + 7) // 8
    if len(signature) != k:
        return False
    s = int.from_bytes(signature, "big")
    if s >= n:
        return False
    em = pow(s, e, n).to_bytes(k, "big")
    t = _DIGEST_INFO[digest] + hashlib.new(digest, message).digest()
    if k < len(t) + 11:
        return False
    expected = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    return hmac.compare_digest(em, expected)


# ─── the content digest ───────────────────────────────────────────────────

def _chunked_digest(f: BinaryIO, digest: str, sections: list[tuple[int, int] | bytes]) -> bytes:
    """Android's chunked digest: every section split into 1 MiB chunks,
    each hashed as 0xa5 || len || chunk, the lot as 0x5a || count || those."""
    leaves = []
    for section in sections:
        if isinstance(section, bytes):
            pieces = [section[i:i + _CHUNK] for i in range(0, len(section), _CHUNK)]
            for piece in pieces:
                leaves.append(hashlib.new(digest, b"\xa5" + struct.pack("<I", len(piece)) + piece).digest())
            continue
        start, end = section
        pos = start
        while pos < end:
            n = min(_CHUNK, end - pos)
            piece = _read_at(f, pos, n)
            leaves.append(hashlib.new(digest, b"\xa5" + struct.pack("<I", n) + piece).digest())
            pos += n
    return hashlib.new(digest, b"\x5a" + struct.pack("<I", len(leaves)) + b"".join(leaves)).digest()


# ─── one scheme block ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class _Signer:
    cert: bytes
    algorithm: int
    digest: bytes
    key_bits: int
    #: The schemes v2's stripping-protection attribute says must be there too.
    requires: frozenset[int] = frozenset()


def _verify_signer(block: bytes, scheme: int) -> _Signer:
    signers = _Reader(block).sequence()
    if len(signers) != 1:
        raise ApkSignatureError(f"the v{scheme} block has {len(signers)} signers; exactly one is supported")
    r = _Reader(signers[0])
    signed_data = r.prefixed()
    if scheme == 3:
        outer_min, outer_max = r.u32(), r.u32()
    signatures = r.sequence()
    public_key = r.prefixed()

    sigs: list[tuple[int, bytes]] = []
    for item in signatures:
        sr = _Reader(item)
        sigs.append((sr.u32(), sr.prefixed()))
    if not sigs:
        raise ApkSignatureError(f"the v{scheme} signer has no signatures")
    supported = [(a, s) for a, s in sigs if a in SUPPORTED]
    if not supported:
        names = ", ".join(KNOWN_UNSUPPORTED.get(a, f"0x{a:04x}") for a, _ in sigs)
        raise ApkSignatureError(
            f"the APK is signed with {names}; only RSA PKCS#1 v1.5 (SHA-256 or SHA-512) is supported"
        )
    algorithm, signature = max(supported, key=lambda p: SUPPORTED[p[0]][1])
    digest_name = SUPPORTED[algorithm][0]
    n, e = rsa_public_key(public_key)
    if not rsa_pkcs1_v15_verify(n, e, signature, signed_data, digest_name):
        raise ApkSignatureError(f"the v{scheme} signature does not verify")

    sd = _Reader(signed_data)
    digests: list[tuple[int, bytes]] = []
    for item in sd.sequence():
        dr = _Reader(item)
        digests.append((dr.u32(), dr.prefixed()))
    certs = sd.sequence()
    if scheme == 3:
        inner_min, inner_max = sd.u32(), sd.u32()
        if (inner_min, inner_max) != (outer_min, outer_max):
            raise ApkSignatureError("the v3 signer's SDK range differs inside and outside its signed data")
    if [a for a, _ in sigs] != [a for a, _ in digests]:
        raise ApkSignatureError(f"the v{scheme} signer's digests and signatures name different algorithms")
    if not certs:
        raise ApkSignatureError(f"the v{scheme} signer has no certificate")
    if certificate_public_key(certs[0]) != public_key:
        raise ApkSignatureError(f"the v{scheme} certificate does not carry the key that signed")
    requires: set[int] = set()
    for item in sd.sequence():                      # the additional attributes
        ar = _Reader(item)
        attr_id = ar.u32()
        if scheme == 2 and attr_id == STRIPPING_PROTECTION_ATTR_ID:
            requires.add(ar.u32())
    wanted = dict(digests)[algorithm]
    return _Signer(cert=certs[0], algorithm=algorithm, digest=wanted, key_bits=n.bit_length(),
                   requires=frozenset(requires))


# ─── the whole file ───────────────────────────────────────────────────────

def verify(path: str | os.PathLike[str]) -> Verified:
    """Verify the APK at ``path`` (see the module docstring) and return its
    signer. Raises :class:`ApkSignatureError`."""
    p = Path(path)
    try:
        size = p.stat().st_size
        f = open(p, "rb")
    except OSError as e:
        raise ApkSignatureError(f"the APK can't be read: {e}") from e
    with f:
        block_off, cd_off, eocd_off, eocd, pairs = _signing_block(f, size)
        blocks = _scheme_blocks(pairs)
        if V2_BLOCK_ID not in blocks:
            raise ApkSignatureError(
                "the APK has no APK Signature Scheme v2 block, which Android 8 needs"
            )
        signers = {2: _verify_signer(blocks[V2_BLOCK_ID], 2)}
        if V3_BLOCK_ID in blocks:
            signers[3] = _verify_signer(blocks[V3_BLOCK_ID], 3)
        elif 3 in signers[2].requires:
            raise ApkSignatureError("the APK's v3 signature was stripped (its v2 signer says there was one)")
        if len({s.cert for s in signers.values()}) != 1:
            raise ApkSignatureError("the v2 and v3 signers differ (key rotation is not supported)")
        # The end record as it was before the signing block went in: its
        # central-directory offset pointing at the block.
        eocd_for_digest = eocd[:16] + struct.pack("<I", block_off) + eocd[20:]
        sections: list[tuple[int, int] | bytes] = [(0, block_off), (cd_off, eocd_off), eocd_for_digest]
        computed: dict[str, bytes] = {}
        for scheme, signer in signers.items():
            name = SUPPORTED[signer.algorithm][0]
            if name not in computed:
                computed[name] = _chunked_digest(f, name, sections)
            if not hmac.compare_digest(computed[name], signer.digest):
                raise ApkSignatureError(
                    f"the APK's contents do not match its v{scheme} signature (the file was changed after signing)"
                )
    signer = signers[2]
    return Verified(
        cert_sha256=hashlib.sha256(signer.cert).hexdigest(),
        schemes=tuple(sorted(signers)),
        key_bits=signer.key_bits,
    )


def _main(argv: list[str]) -> int:
    """``python -m domovoi.apk_signature APK...``: each file's signer, or why not."""
    import json
    import sys

    status = 0
    for arg in argv:
        try:
            v = verify(arg)
            print(json.dumps({"file": arg, "cert_sha256": v.cert_sha256, "schemes": v.schemes, "key_bits": v.key_bits}))
        except ApkSignatureError as e:
            print(json.dumps({"file": arg, "error": str(e)}), file=sys.stderr)
            status = 1
    return status


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv[1:]))
