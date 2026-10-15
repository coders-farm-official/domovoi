"""Make the signed test APKs that domovoi/tests/test_apk_signature.py and
test_android_app.py check (domovoi/tests/fixtures/android_app/).

Each fixture is a tiny ZIP laid out like an APK (a manifest placeholder, a
dex placeholder and a native library under lib/<abi>/), signed by the real
``apksigner`` from the Android SDK's build-tools with keys made up for this
run: an RSA-4096 "release" key (apksigner signs it with SHA-512), an
RSA-2048 "other" key (SHA-256) and an EC P-256 key. The keystores live in a
temporary folder and are deleted at the end; no key is ever written into
the repo, and none of them is the project's release key.

``fixtures.json`` beside the APKs records, for each file, its size and
SHA-256 and what ``apksigner verify --print-certs`` says about it (the
certificate SHA-256, the schemes), so the tests compare the core's own
verifier with Google's.

Also one forgery (forged-cert-*.apk): the release certificate in a v2
signer whose signature and key are the other key's, made here with openssl
(a build-time tool) reading that throwaway key.

Run from the repo root with JAVA_HOME set and the SDK at ANDROID_HOME or
%LOCALAPPDATA%\\Android\\Sdk:

    python scripts/dev/make_apk_fixtures.py

Re-running makes new keys, so every file (and fixtures.json) changes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "domovoi" / "tests" / "fixtures" / "android_app"
BUILD_TOOLS = "35.0.0"


def sdk_dir() -> Path:
    env = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if env:
        return Path(env)
    return Path(os.environ.get("LOCALAPPDATA", "")) / "Android" / "Sdk"


def java_bin(name: str) -> str:
    home = os.environ.get("JAVA_HOME")
    if home:
        exe = Path(home) / "bin" / (name + (".exe" if sys.platform == "win32" else ""))
        if exe.is_file():
            return str(exe)
    found = shutil.which(name)
    if not found:
        sys.exit(f"{name} not found: set JAVA_HOME")
    return found


APKSIGNER = sdk_dir() / "build-tools" / BUILD_TOOLS / "lib" / "apksigner.jar"


def apksigner(*args: str) -> str:
    r = subprocess.run([java_bin("java"), "-jar", str(APKSIGNER), *args], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"apksigner {args[0]} failed: {r.stdout}{r.stderr}")
    return r.stdout


def make_key(dest: Path, alg: str, size: int, password: str) -> None:
    subprocess.run(
        [java_bin("keytool"), "-genkeypair", "-keystore", str(dest), "-storetype", "PKCS12",
         "-alias", "test", "-keyalg", alg, "-keysize", str(size), "-validity", "36500",
         "-dname", f"CN=Domovoi test fixture {alg}-{size}, O=Not a release key",
         "-storepass", password, "-keypass", password],
        check=True, capture_output=True,
    )


def unsigned_apk(dest: Path, abis: list[str]) -> None:
    with zipfile.ZipFile(dest, "w") as z:
        z.writestr("AndroidManifest.xml", b"\x03\x00\x08\x00 test fixture, not a real manifest")
        z.writestr(zipfile.ZipInfo("classes.dex"), b"dex\n035\x00" + b"\x00" * 64)
        for abi in abis:
            # Stored, like the real app's libraries; apksigner page-aligns them.
            z.writestr(zipfile.ZipInfo(f"lib/{abi}/libfixture.so"), b"\x7fELF" + abi.encode() * 40)
        z.writestr("res/raw/notice.txt", "Domovoi test fixture: signed with a throwaway key.\n",
                   compress_type=zipfile.ZIP_DEFLATED)


def sign(src: Path, dest: Path, ks: Path, password: str, *, v1: bool = False, v2: bool = True,
         v3: bool = True) -> None:
    apksigner("sign", "--ks", str(ks), "--ks-pass", f"pass:{password}", "--min-sdk-version", "26",
              "--v1-signing-enabled", str(v1).lower(), "--v2-signing-enabled", str(v2).lower(),
              "--v3-signing-enabled", str(v3).lower(), "--v4-signing-enabled", "false",
              "--out", str(dest), str(src))


# ─── a forgery: the pinned certificate, signed by another key ─────────────
# What an attacker who can't sign with the release key would try: put the
# release key's certificate in the v2 signer and sign the signed data with
# a key of their own (whose public key goes in the signer's key field).
# Only the check that the certificate carries the signing key catches it.

def _lp(b: bytes) -> bytes:
    return struct.pack("<I", len(b)) + b


def _seq(items: list[bytes]) -> bytes:
    return _lp(b"".join(_lp(i) for i in items))


class _R:
    def __init__(self, b: bytes) -> None:
        self.b, self.p = b, 0

    def u32(self) -> int:
        v = struct.unpack_from("<I", self.b, self.p)[0]
        self.p += 4
        return v

    def lp(self) -> bytes:
        n = self.u32()
        v = self.b[self.p:self.p + n]
        self.p += n
        return v

    def items(self) -> list[bytes]:
        inner = _R(self.lp())
        out = []
        while inner.p < len(inner.b):
            out.append(inner.lp())
        return out


def _der_ints(der: bytes) -> list[int]:
    """The INTEGERs of a DER SEQUENCE of INTEGERs (an RSAPrivateKey)."""
    def length(pos: int) -> tuple[int, int]:
        first = der[pos]
        if first < 0x80:
            return first, pos + 1
        n = first & 0x7F
        return int.from_bytes(der[pos + 1:pos + 1 + n], "big"), pos + 1 + n
    assert der[0] == 0x30
    _, pos = length(1)
    out = []
    while pos < len(der):
        assert der[pos] == 0x02
        n, pos = length(pos + 1)
        out.append(int.from_bytes(der[pos:pos + n], "big"))
        pos += n
    return out


def private_key(ks: Path, password: str) -> tuple[int, int]:
    """(n, d) of the keystore's RSA key, through openssl (a build-time tool)."""
    pem = subprocess.run(["openssl", "pkcs12", "-in", str(ks), "-nocerts", "-nodes", "-passin", f"pass:{password}"],
                         check=True, capture_output=True).stdout
    der = subprocess.run(["openssl", "rsa", "-traditional", "-outform", "DER"], input=pem,
                         check=True, capture_output=True).stdout
    ints = _der_ints(der)
    return ints[1], ints[3]


def _first_cert(apk: Path) -> bytes:
    data = apk.read_bytes()
    eocd = data.rindex(b"PK\x05\x06")
    _, cd_off = struct.unpack_from("<II", data, eocd + 12)
    size = struct.unpack_from("<Q", data, cd_off - 24)[0]
    pairs, pos = data[cd_off - size - 8 + 8:cd_off - 24], 0
    while pos < len(pairs):
        ln = struct.unpack_from("<Q", pairs, pos)[0]
        if struct.unpack_from("<I", pairs, pos + 8)[0] == 0x7109871A:
            signer = _R(_R(pairs[pos + 12:pos + 8 + ln]).items()[0])
            sd = _R(signer.lp())
            sd.items()
            return sd.items()[0]
        pos += 8 + ln
    raise SystemExit(f"no v2 block in {apk}")


def _blocks(data: bytes) -> tuple[int, int, int, dict[int, bytes]]:
    """(signing block offset, central directory offset, end record offset,
    {scheme id: value}) of a signed APK."""
    eocd = data.rindex(b"PK\x05\x06")
    _, cd_off = struct.unpack_from("<II", data, eocd + 12)
    size = struct.unpack_from("<Q", data, cd_off - 24)[0]
    block_off = cd_off - size - 8
    pairs, pos, out = data[block_off + 8:cd_off - 24], 0, {}
    while pos < len(pairs):
        ln = struct.unpack_from("<Q", pairs, pos)[0]
        out[struct.unpack_from("<I", pairs, pos + 8)[0]] = pairs[pos + 12:pos + 8 + ln]
        pos += 8 + ln
    return block_off, cd_off, eocd, out


def _assemble(data: bytes, values: list[tuple[int, bytes]]) -> bytes:
    """``data`` with its signing block replaced by one holding ``values``."""
    block_off, cd_off, eocd, _ = _blocks(data)
    pairs = b"".join(struct.pack("<Q", 4 + len(v)) + struct.pack("<I", i) + v for i, v in values)
    size = len(pairs) + 24
    block = struct.pack("<Q", size) + pairs + struct.pack("<Q", size) + b"APK Sig Block 42"
    new_eocd = bytearray(data[eocd:])
    struct.pack_into("<I", new_eocd, 16, block_off + len(block))
    return data[:block_off] + block + data[cd_off:eocd] + bytes(new_eocd)


def _sign_sha256(n: int, d: int, message: bytes) -> bytes:
    k = (n.bit_length() + 7) // 8
    t = bytes.fromhex("3031300d060960864801650304020105000420") + hashlib.sha256(message).digest()
    em = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    return pow(int.from_bytes(em, "big"), d, n).to_bytes(k, "big")


def _v2_parts(value: bytes) -> tuple[list[bytes], list[bytes], list[bytes], bytes]:
    """(digests, certificates, attributes, public key) of a v2 block's one signer."""
    signer = _R(_R(value).items()[0])
    signed, _sigs, pub = signer.lp(), signer.items(), signer.lp()
    sd = _R(signed)
    return sd.items(), sd.items(), sd.items(), pub


def _v2_value(digests: list[bytes], certs: list[bytes], attrs: list[bytes], pub: bytes,
              n: int, d: int) -> bytes:
    signed = _seq(digests) + _seq(certs) + _seq(attrs)
    signer = _lp(signed) + _seq([struct.pack("<I", 0x0103) + _lp(_sign_sha256(n, d, signed))]) + _lp(pub)
    return _seq([signer])


def forge_cert(src: Path, dest: Path, foreign_cert: bytes, n: int, d: int) -> None:
    """``src`` (signed by the key n, d; SHA-256) re-signed with
    ``foreign_cert`` in place of its own certificate; v2 only."""
    data = src.read_bytes()
    digests, _certs, attrs, pub = _v2_parts(_blocks(data)[3][0x7109871A])
    dest.write_bytes(_assemble(data, [(0x7109871A, _v2_value(digests, [foreign_cert], attrs, pub, n, d))]))


def forge_digest_list(src: Path, dest: Path, n: int, d: int) -> None:
    """``src`` re-signed (validly, by its own key) with one more digest in its
    signed data than it has signatures: Android refuses the mismatch."""
    data = src.read_bytes()
    digests, certs, attrs, pub = _v2_parts(_blocks(data)[3][0x7109871A])
    extra = struct.pack("<I", 0x0104) + _lp(b"\x00" * 64)
    dest.write_bytes(_assemble(data, [(0x7109871A, _v2_value(digests + [extra], certs, attrs, pub, n, d))]))


def forge_mixed_v3(src: Path, dest: Path, other_cert: bytes, n: int, d: int) -> None:
    """``src``'s own v2 block (the release key's) next to a v3 block signed,
    validly, by another key over the same contents: two signers."""
    sys.path.insert(0, str(REPO))
    from domovoi import apk_signature

    data = src.read_bytes()
    block_off, cd_off, eocd, values = _blocks(data)
    eocd_for_digest = data[eocd:eocd + 16] + struct.pack("<I", block_off) + data[eocd + 20:]
    tmp = dest.with_suffix(".tmp")
    tmp.write_bytes(data)
    with open(tmp, "rb") as f:
        digest = apk_signature._chunked_digest(f, "sha256", [(0, block_off), (cd_off, eocd), eocd_for_digest])
    tmp.unlink()
    sdk = struct.pack("<II", 26, 0x7FFFFFFF)
    signed = _seq([struct.pack("<I", 0x0103) + _lp(digest)]) + _seq([other_cert]) + sdk + _seq([])
    pub = apk_signature.certificate_public_key(other_cert)
    signer = _lp(signed) + sdk + _seq([struct.pack("<I", 0x0103) + _lp(_sign_sha256(n, d, signed))]) + _lp(pub)
    dest.write_bytes(_assemble(data, [(0x7109871A, values[0x7109871A]), (0xF05368C0, _seq([signer]))]))


def describe(apk: Path) -> dict:
    r = subprocess.run([java_bin("java"), "-jar", str(APKSIGNER), "verify", "--verbose", "--print-certs",
                        "--min-sdk-version", "26", str(apk)], capture_output=True, text=True)
    out = r.stdout
    cert = re.search(r"Signer #1 certificate SHA-256 digest: ([0-9a-f]{64})", out)
    return {
        "apksigner_verifies": r.returncode == 0,
        "apksigner_cert_sha256": cert.group(1) if cert else None,
        "apksigner_v2": "Verified using v2 scheme (APK Signature Scheme v2): true" in out,
        "apksigner_v3": "Verified using v3 scheme (APK Signature Scheme v3): true" in out,
    }


def main() -> int:
    if not APKSIGNER.is_file():
        sys.exit(f"no apksigner at {APKSIGNER}")
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.apk"):
        old.unlink()
    tmp = Path(tempfile.mkdtemp(prefix="domovoi-apk-fixtures-"))
    try:
        pw = secrets.token_urlsafe(18)
        keys = {"release": tmp / "release.p12", "other": tmp / "other.p12", "ec": tmp / "ec.p12"}
        make_key(keys["release"], "RSA", 4096, pw)
        make_key(keys["other"], "RSA", 2048, pw)
        make_key(keys["ec"], "EC", 256, pw)
        plan = [
            # file, abis in the zip, key, signing schemes, what it is for
            ("release-arm64-v8a.apk", ["arm64-v8a"], "release", {}, "the release key, v2 + v3 (SHA-512)"),
            ("release-armeabi-v7a.apk", ["armeabi-v7a"], "release", {}, "the release key, 32-bit"),
            ("release-v2only-arm64-v8a.apk", ["arm64-v8a"], "release", {"v3": False}, "v2 only"),
            ("release-two-abis.apk", ["arm64-v8a", "armeabi-v7a"], "release", {}, "libraries for two ABIs"),
            ("other-arm64-v8a.apk", ["arm64-v8a"], "other", {}, "another RSA key (SHA-256)"),
            ("ec-arm64-v8a.apk", ["arm64-v8a"], "ec", {}, "an EC key: refused"),
            ("v1only-arm64-v8a.apk", ["arm64-v8a"], "other", {"v1": True, "v2": False, "v3": False},
             "JAR signing only: refused"),
        ]
        manifest: dict = {
            "about": "Made by scripts/dev/make_apk_fixtures.py with throwaway keys (deleted after); "
                     "not the release key. apksigner_* fields are what apksigner verify printed.",
            "files": {},
        }
        for name, abis, key, schemes, about in plan:
            raw = tmp / ("unsigned-" + name)
            unsigned_apk(raw, abis)
            sign(raw, OUT / name, keys[key], pw, **schemes)
        raw = OUT / "unsigned-arm64-v8a.apk"
        unsigned_apk(raw, ["arm64-v8a"])
        plan.append(("unsigned-arm64-v8a.apk", ["arm64-v8a"], None, {}, "not signed at all: refused"))
        # The release certificate (the first in the release APK's v2 signer),
        # signed by the "other" key: refused, the certificate does not carry
        # the key that signed.
        release_cert = _first_cert(OUT / "release-arm64-v8a.apk")
        n, d = private_key(keys["other"], pw)
        forge_cert(OUT / "other-arm64-v8a.apk", OUT / "forged-cert-arm64-v8a.apk", release_cert, n, d)
        plan.append(("forged-cert-arm64-v8a.apk", ["arm64-v8a"], "other",
                     {}, "the release certificate in a signer whose key is another: refused"))
        forge_digest_list(OUT / "other-arm64-v8a.apk", OUT / "digest-list-arm64-v8a.apk", n, d)
        plan.append(("digest-list-arm64-v8a.apk", ["arm64-v8a"], "other",
                     {}, "a digest with no signature beside it: refused"))
        forge_mixed_v3(OUT / "release-arm64-v8a.apk", OUT / "mixed-v3-arm64-v8a.apk",
                       _first_cert(OUT / "other-arm64-v8a.apk"), n, d)
        plan.append(("mixed-v3-arm64-v8a.apk", ["arm64-v8a"], "release",
                     {}, "the release key's v2 block, another key's (valid) v3 block: refused"))
        for name, abis, key, schemes, about in plan:
            data = (OUT / name).read_bytes()
            manifest["files"][name] = {
                "about": about, "key": key, "abis": abis,
                "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                **describe(OUT / name),
            }
        (OUT / "fixtures.json").write_bytes((json.dumps(manifest, indent=2) + "\n").encode("utf-8"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"wrote {len(plan)} APKs and fixtures.json to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
