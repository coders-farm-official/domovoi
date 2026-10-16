"""The core's APK signature check (domovoi/apk_signature.py) against APKs
signed by Google's own apksigner with throwaway keys
(domovoi/tests/fixtures/android_app/, made by scripts/dev/make_apk_fixtures.py,
whose fixtures.json records what apksigner said about each file).

What must hold: the certificate it returns is the one apksigner prints; a
file changed anywhere a signature covers is refused; what it doesn't
support (EC keys, JAR-only signing, no signature) is refused by name; and
damage to the signing block is a refusal, never a crash.
"""

from __future__ import annotations

import hashlib
import json
import random
import struct
from pathlib import Path

import pytest

from domovoi import apk_signature as sig

FIX = Path(__file__).resolve().parent / "fixtures" / "android_app"
META = json.loads((FIX / "fixtures.json").read_text(encoding="utf-8"))["files"]
SIGNED_RSA = [n for n, m in META.items()
              if m["key"] in ("release", "other") and m["apksigner_v2"] and "refused" not in m["about"]]


def test_the_fixtures_are_what_fixtures_json_says():
    for name, m in META.items():
        data = (FIX / name).read_bytes()
        assert len(data) == m["size"] and hashlib.sha256(data).hexdigest() == m["sha256"], name


@pytest.mark.parametrize("name", SIGNED_RSA)
def test_the_certificate_is_the_one_apksigner_prints(name):
    m = META[name]
    v = sig.verify(FIX / name)
    assert v.cert_sha256 == m["apksigner_cert_sha256"]
    assert v.schemes == ((2, 3) if m["apksigner_v3"] else (2,))
    assert v.key_bits == (4096 if m["key"] == "release" else 2048)


def test_both_digests_apksigner_uses_are_checked():
    # The release key (RSA-4096) is signed with SHA-512, the other (2048) with SHA-256.
    for name, algo in (("release-arm64-v8a.apk", 0x0104), ("other-arm64-v8a.apk", 0x0103)):
        data = (FIX / name).read_bytes()
        assert struct.pack("<I", algo) in data


@pytest.mark.parametrize("name,words", [
    ("ec-arm64-v8a.apk", "ECDSA"),
    ("v1only-arm64-v8a.apk", "no signing block"),
    ("unsigned-arm64-v8a.apk", "no signing block"),
])
def test_what_it_does_not_support_is_refused_by_name(name, words):
    with pytest.raises(sig.ApkSignatureError) as e:
        sig.verify(FIX / name)
    assert words in str(e.value)


def _layout(data: bytes) -> dict:
    """Where the signing block, the central directory and the end record are."""
    eocd = data.rindex(b"PK\x05\x06")
    cd_size, cd_off = struct.unpack_from("<II", data, eocd + 12)
    size = struct.unpack_from("<Q", data, cd_off - 24)[0]
    return {"block": cd_off - size - 8, "cd": cd_off, "eocd": eocd}


def _flip(data: bytes, at: int) -> bytes:
    return data[:at] + bytes([data[at] ^ 0x01]) + data[at + 1:]


@pytest.fixture
def release_apk() -> bytes:
    return (FIX / "release-arm64-v8a.apk").read_bytes()


def _verify_bytes(tmp_path: Path, data: bytes) -> sig.Verified:
    p = tmp_path / "x.apk"
    p.write_bytes(data)
    return sig.verify(p)


def test_a_changed_library_byte_is_refused(tmp_path, release_apk):
    at = release_apk.index(b"\x7fELF") + 10
    with pytest.raises(sig.ApkSignatureError) as e:
        _verify_bytes(tmp_path, _flip(release_apk, at))
    assert "changed after signing" in str(e.value)


def test_a_changed_central_directory_or_end_record_is_refused(tmp_path, release_apk):
    lay = _layout(release_apk)
    # A file name inside the central directory; then the end record's comment length area.
    name_at = release_apk.index(b"lib/arm64-v8a/libfixture.so", lay["cd"]) + 5
    for at in (name_at, lay["eocd"] + 8):
        with pytest.raises(sig.ApkSignatureError):
            _verify_bytes(tmp_path, _flip(release_apk, at))


def test_a_changed_signature_or_certificate_is_refused(tmp_path, release_apk):
    lay = _layout(release_apk)
    block = release_apk[lay["block"]:lay["cd"]]
    v2 = block.index(struct.pack("<I", sig.V2_BLOCK_ID))
    # Somewhere inside the v2 signer's certificate (DER starts 30 82), and
    # in its signature (the last 512 bytes before its public key are RSA-4096's).
    cert_at = lay["block"] + block.index(b"\x30\x82", v2 + 40) + 40
    with pytest.raises(sig.ApkSignatureError):
        _verify_bytes(tmp_path, _flip(release_apk, cert_at))
    spki = sig.certificate_public_key(_first_cert(block[v2 + 4:]))
    key_at = lay["block"] + block.index(spki, v2)
    sig_at = key_at - 4 - 100
    with pytest.raises(sig.ApkSignatureError) as e:
        _verify_bytes(tmp_path, _flip(release_apk, sig_at))
    assert "does not verify" in str(e.value)


def _first_cert(v2_value: bytes) -> bytes:
    r = sig._Reader(v2_value)
    signer = sig._Reader(r.sequence()[0])
    signed = sig._Reader(signer.prefixed())
    signed.sequence()                      # digests
    return signed.sequence()[0]


def test_an_apk_without_its_v2_block_is_refused_even_with_v3(tmp_path, release_apk):
    lay = _layout(release_apk)
    at = release_apk.index(struct.pack("<I", sig.V2_BLOCK_ID), lay["block"], lay["cd"])
    renamed = release_apk[:at] + struct.pack("<I", 0x42424242) + release_apk[at + 4:]
    with pytest.raises(sig.ApkSignatureError) as e:
        _verify_bytes(tmp_path, renamed)
    assert "v2" in str(e.value) and "Android 8" in str(e.value)


def test_damage_to_the_signing_block_is_a_refusal_never_a_crash(tmp_path, release_apk):
    """Any one byte of the signing block flipped: either a refusal, or (in
    the padding apksigner adds, which nothing signs) the same certificate."""
    lay = _layout(release_apk)
    want = sig.verify(FIX / "release-arm64-v8a.apk").cert_sha256
    rng = random.Random(20261015)
    spots = rng.sample(range(lay["block"], lay["cd"]), 300) + list(range(lay["block"], lay["block"] + 40)) \
        + list(range(lay["cd"] - 24, lay["cd"]))
    for at in spots:
        try:
            got = _verify_bytes(tmp_path, _flip(release_apk, at))
        except sig.ApkSignatureError:
            continue
        assert got.cert_sha256 == want, f"byte {at - lay['block']} of the block changed the signer"


def test_truncated_and_garbage_files_are_refusals(tmp_path, release_apk):
    for data in (b"", b"PK\x05\x06", release_apk[:1000], release_apk[:-30], b"\x00" * 5000,
                 release_apk + b"trailing"):
        with pytest.raises(sig.ApkSignatureError):
            _verify_bytes(tmp_path, data)


def test_rsa_verification_rebuilds_the_whole_encoding():
    # A tiny textbook key is refused outright (under 2048 bits) ...
    spki_small = bytes.fromhex(
        "305c300d06092a864886f70d0101010500034b003048024100"
        "c3" + "11" * 63 + "0203010001")
    with pytest.raises(sig.ApkSignatureError):
        sig.rsa_public_key(spki_small)
    # ... and a signature of the wrong length never verifies.
    n = (1 << 2047) + 12345
    assert sig.rsa_pkcs1_v15_verify(n, 65537, b"\x01" * 10, b"m", "sha256") is False
    assert sig.rsa_pkcs1_v15_verify(n, 65537, n.to_bytes(256, "big"), b"m", "sha256") is False


def test_the_pinned_certificate_signed_by_another_key_is_refused():
    """forged-cert: the release key's certificate in a v2 signer whose
    signature (valid) and key field are another key's, over the right
    content digest. Only "the certificate carries the key that signed"
    stands between this file and the release key's name."""
    with pytest.raises(sig.ApkSignatureError) as e:
        sig.verify(FIX / "forged-cert-arm64-v8a.apk")
    assert "does not carry the key that signed" in str(e.value)
    assert META["forged-cert-arm64-v8a.apk"]["apksigner_verifies"] is False, "apksigner refuses it too"


@pytest.mark.parametrize("name,words", [
    ("digest-list-arm64-v8a.apk", "name different algorithms"),
    ("mixed-v3-arm64-v8a.apk", "v2 and v3 signers differ"),
])
def test_signers_android_would_refuse_are_refused(name, words):
    """Validly signed, but not as Android accepts: a digest with no
    signature beside it; the release key's v2 block next to another key's
    v3 block (a phone on Android 9+ would take the v3 signer)."""
    with pytest.raises(sig.ApkSignatureError) as e:
        sig.verify(FIX / name)
    assert words in str(e.value)
    assert META[name]["apksigner_verifies"] is False


# ─── the security review's forgeries (2026-10-15) ─────────────────────────

@pytest.mark.parametrize("name,words", [
    ("v31-other-arm64-v8a.apk", "v3.1 block"),
    ("v3-stripped-arm64-v8a.apk", "v3 signature was stripped"),
    ("v2-two-signers-arm64-v8a.apk", "v2 block has 2 signers"),
    ("v3-two-ranges-arm64-v8a.apk", "v3 block has 2 signers"),
], ids=lambda v: v if v.endswith(".apk") else None)
def test_what_the_review_forged_is_refused(name, words):
    """Each sits beside the release key's genuine blocks and is signed,
    validly, by another key. v31-other: apksigner at min SDK 26 accepts it,
    but an Android 13+ phone believes the v3.1 signer (apksigner at 33
    names the other key): so any v3.1 block is refused. v3-stripped: v2's
    stripping-protection attribute names v3, so its absence is refused,
    as Android does. Two signers in v2 or v3: exactly one is supported."""
    with pytest.raises(sig.ApkSignatureError) as e:
        sig.verify(FIX / name)
    assert words in str(e.value)


def test_the_release_v2_names_v3_and_a_v2_only_signing_does_not():
    """The stripping-protection attribute is what apksigner writes when it
    signs v2 and v3: the release fixtures carry it, the v2-only one not."""
    marker = struct.pack("<I", sig.STRIPPING_PROTECTION_ATTR_ID)
    assert marker in (FIX / "release-arm64-v8a.apk").read_bytes()
    assert marker not in (FIX / "release-v2only-arm64-v8a.apk").read_bytes()
    assert sig.verify(FIX / "release-v2only-arm64-v8a.apk").schemes == (2,)


def _der(tag: int, body: bytes) -> bytes:
    n = len(body)
    length = bytes([n]) if n < 0x80 else bytes([0x80 | ((n.bit_length() + 7) // 8)]) + n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([tag]) + length + body


def _spki(n: int, e: int) -> bytes:
    def integer(v: int) -> bytes:
        b = v.to_bytes((v.bit_length() + 8) // 8, "big")
        return _der(0x02, b)
    key = _der(0x30, integer(n) + integer(e))
    alg = _der(0x30, _der(0x06, bytes.fromhex("2a864886f70d010101")) + b"\x05\x00")
    return _der(0x30, alg + _der(0x03, b"\x00" + key))


def test_rsa_keys_are_capped_like_java_does():
    ok = (1 << 4095) | 1
    assert sig.rsa_public_key(_spki(ok, 65537)) == (ok, 65537)
    for n, e, words in (((1 << 8192) | 1, 65537, "at most 8192"), (ok, 2 ** 32 + 1, "exponent"),
                        ((1 << 2046) | 1, 65537, "at least 2048"), (ok, 65536, "exponent")):
        with pytest.raises(sig.ApkSignatureError) as err:
            sig.rsa_public_key(_spki(n, e))
        assert words in str(err.value)
