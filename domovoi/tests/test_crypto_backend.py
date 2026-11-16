"""The server's crypto backend (domovoi/crypto_backend.py): ``cryptography``
does the arithmetic, the pure-Python code is only a checking fallback, and
the two give the same answer to every input.

What must hold:

* the same keys and the same signatures from both (Ed25519 is
  deterministic), on the RFC 8032 vectors and on random keys and messages;
  a signature made by either verifies on the other;
* the same accept/reject verdict from both on the Windows Setup's edge
  vectors (small-order keys, non-canonical encodings, S out of range), on
  tampered inputs, and on every APK fixture (same certificate, same
  refusal text);
* without ``cryptography`` the server signs nothing (it raises, never
  falls back), still answers a check, logs that at ERROR once, and says so
  in ``GET /v1/admin/version``.

DB-free and network-free; ``cryptography`` is a core dependency, so its
absence here is a broken environment, not a skip.
"""

from __future__ import annotations

import json
import logging
import random
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa

from domovoi import _ed25519, apk_signature, crypto_backend, server_identity
from satellite import _ed25519 as sat_ed25519
from satellite import server_identity as sat_identity

FIXTURES = Path(__file__).resolve().parent / "fixtures"
VECTORS = json.loads((FIXTURES / "ed25519-setup-vectors.json").read_text(encoding="utf-8"))["vectors"]
APKS = FIXTURES / "android_app"
APK_META = json.loads((APKS / "fixtures.json").read_text(encoding="utf-8"))["files"]

# RFC 8032 section 7.1, TEST 1, 2 and 3: (seed, public key, message, signature).
RFC_8032 = [
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
     "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
     "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555f"
     "b8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
     "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
     "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da08"
     "5ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
    ("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
     "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025",
     "af82",
     "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18"
     "ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"),
]


@pytest.fixture(autouse=True)
def _fresh_backend():
    """Every test resolves the backend itself, and leaves it resolved
    afresh for the next one."""
    crypto_backend.reset()
    yield
    crypto_backend.reset()


@pytest.fixture
def without_cryptography(monkeypatch):
    """An install without the package: the import comes back empty."""
    monkeypatch.setattr(crypto_backend, "_import_cryptography", lambda: None)
    crypto_backend.reset()
    yield
    crypto_backend.reset()


@pytest.fixture(params=["cryptography", "pure"])
def backend(request, monkeypatch):
    """Run the test once on each backend, through the module's own API."""
    if request.param == "pure":
        monkeypatch.setattr(crypto_backend, "_import_cryptography", lambda: None)
    crypto_backend.reset()
    assert crypto_backend.backend() == request.param
    yield request.param
    crypto_backend.reset()


def _random_cases(n: int, seed: int) -> list[tuple[bytes, bytes]]:
    rng = random.Random(seed)
    return [
        (rng.randbytes(32), rng.randbytes(rng.choice((0, 1, 31, 64, 200, 1500))))
        for _ in range(n)
    ]


# ─── which backend, and saying so ─────────────────────────────────────────

def test_the_server_uses_cryptography_and_says_so_once(caplog):
    with caplog.at_level(logging.INFO, logger="domovoi.crypto_backend"):
        assert crypto_backend.backend() == "cryptography"
        crypto_backend.backend()
        crypto_backend.info()
    lines = [r for r in caplog.records if r.name == "domovoi.crypto_backend"]
    assert len(lines) == 1 and lines[0].levelno == logging.INFO
    assert "cryptography" in lines[0].getMessage()
    info = crypto_backend.info()
    assert info["backend"] == "cryptography" and info["signing"] is True
    assert info["cryptography"] and info["openssl"]
    assert server_identity._backend() == "cryptography"


def test_without_cryptography_it_says_so_at_error_once(without_cryptography, caplog):
    with caplog.at_level(logging.INFO, logger="domovoi.crypto_backend"):
        assert crypto_backend.backend() == "pure"
        crypto_backend.backend()
    lines = [r for r in caplog.records if r.name == "domovoi.crypto_backend"]
    assert len(lines) == 1 and lines[0].levelno == logging.ERROR
    assert crypto_backend.info() == {
        "backend": "pure", "cryptography": None, "openssl": None, "signing": False,
    }


# ─── signing never falls back ─────────────────────────────────────────────

def test_without_cryptography_the_server_signs_nothing(without_cryptography):
    seed = bytes(range(32))
    with pytest.raises(crypto_backend.CryptoUnavailable):
        crypto_backend.ed25519_sign(seed, b"m")
    with pytest.raises(crypto_backend.CryptoUnavailable):
        crypto_backend.ed25519_public_key(seed)
    with pytest.raises(crypto_backend.CryptoUnavailable):
        server_identity.sign(seed, b"m")
    # Not a ValueError: the identity loader reads that as "unusable key
    # file" and would mint a new identity over the household's.
    assert not issubclass(crypto_backend.CryptoUnavailable, ValueError)


def test_without_cryptography_no_key_is_made(without_cryptography, tmp_path):
    fresh = tmp_path / "fresh" / "server-identity.json"
    server_identity.reset_cache()
    with pytest.raises(crypto_backend.CryptoUnavailable):
        server_identity.load_or_create(fresh)
    assert not fresh.exists()


def test_an_existing_key_file_is_left_alone_without_cryptography(tmp_path, monkeypatch):
    path = tmp_path / "server-identity.json"
    server_identity.reset_cache()
    made = server_identity.load_or_create(path)
    before = path.read_bytes()
    server_identity.reset_cache()
    monkeypatch.setattr(crypto_backend, "_import_cryptography", lambda: None)
    crypto_backend.reset()
    with pytest.raises(crypto_backend.CryptoUnavailable):
        server_identity.load_or_create(path)
    assert path.read_bytes() == before
    assert made.fingerprint in before.decode("utf-8")
    server_identity.reset_cache()


def test_without_cryptography_a_check_still_answers(without_cryptography):
    seed, public, message, signature = (bytes.fromhex(v) for v in RFC_8032[1])
    assert crypto_backend.ed25519_verify(public, message, signature) is True
    assert crypto_backend.ed25519_verify(public, message + b"!", signature) is False


# ─── Ed25519: same keys, same signatures ──────────────────────────────────

@pytest.mark.parametrize("seed_hex,public_hex,message_hex,signature_hex", RFC_8032)
def test_rfc_8032_on_both_backends(seed_hex, public_hex, message_hex, signature_hex, backend):
    seed, message = bytes.fromhex(seed_hex), bytes.fromhex(message_hex)
    public, signature = bytes.fromhex(public_hex), bytes.fromhex(signature_hex)
    # The pure-Python reference (the satellites' fallback) and cryptography
    # (the server's signer) both match the RFC.
    assert _ed25519.public_key(seed) == public
    assert _ed25519.sign(seed, message) == signature
    if backend == "cryptography":
        assert crypto_backend.ed25519_public_key(seed) == public
        assert crypto_backend.ed25519_sign(seed, message) == signature
    assert crypto_backend.ed25519_verify(public, message, signature) is True
    assert crypto_backend.ed25519_verify(public, message + b"x", signature) is False


def test_random_keys_sign_identically_and_cross_verify():
    for seed, message in _random_cases(40, 20261115):
        ours = crypto_backend.ed25519_sign(seed, message)
        reference = _ed25519.sign(seed, message)
        public = crypto_backend.ed25519_public_key(seed)
        assert public == _ed25519.public_key(seed)
        assert ours == reference, "Ed25519 is deterministic: one key, one message, one signature"
        # cryptography's signature checked by the pure code, and the pure
        # code's by cryptography (raw, and through the backend).
        assert _ed25519.verify(public, message, ours)
        assert sat_ed25519.verify(public, message, ours)
        ed25519.Ed25519PublicKey.from_public_bytes(public).verify(reference, message)
        assert crypto_backend.ed25519_verify(public, message, reference)


def _tamperings(public: bytes, message: bytes, signature: bytes, rng: random.Random):
    def flip(data: bytes, bit: int) -> bytes:
        b = bytearray(data)
        b[bit // 8] ^= 1 << (bit % 8)
        return bytes(b)

    yield "message bit", public, flip(message + b"\x00", rng.randrange(8 * (len(message) + 1))), signature
    yield "message extended", public, message + b"\x00", signature
    yield "R bit", public, message, flip(signature, rng.randrange(256))
    yield "S bit", public, message, flip(signature, 256 + rng.randrange(256))
    yield "key bit", flip(public, rng.randrange(256)), message, signature
    yield "signature truncated", public, message, signature[:-1]
    yield "S + L", public, message, signature[:32] + (
        int.from_bytes(signature[32:], "little") + _ed25519.L).to_bytes(33, "little")[:32]


def test_tampered_inputs_fail_on_both_backends(backend):
    rng = random.Random(8032)
    for seed, message in _random_cases(15, 4242):
        public, signature = _ed25519.public_key(seed), _ed25519.sign(seed, message)
        assert crypto_backend.ed25519_verify(public, message, signature)
        for name, p, m, s in _tamperings(public, message, signature, rng):
            assert crypto_backend.ed25519_verify(p, m, s) is False, (backend, name)
            assert _ed25519.verify(p, m, s) is False, name


@pytest.mark.parametrize("case", VECTORS, ids=lambda c: c["name"])
def test_the_edge_vectors_get_one_verdict_everywhere(case, backend):
    """The Windows Setup's vector suite: the server's verifier on either
    backend, the satellite's verifier and both pure twins all agree with
    its expectation, small-order and non-canonical keys included."""
    args = (bytes.fromhex(case["pub"]), bytes.fromhex(case["msg"]), bytes.fromhex(case["sig"]))
    assert crypto_backend.ed25519_verify(*args) is case["expect"]
    assert server_identity.verify(*args) is case["expect"]
    assert sat_identity.verify(*args) is case["expect"]
    assert _ed25519.verify(*args) is case["expect"]
    assert sat_ed25519.verify(*args) is case["expect"]


def _identity_spelled_otherwise() -> list[tuple[str, bytes]]:
    return [
        ("y = p + 1", (_ed25519.P + 1).to_bytes(32, "little")),
        ("y = 1 with the sign bit", (1 | (1 << 255)).to_bytes(32, "little")),
    ]


@pytest.mark.parametrize("name,public", _identity_spelled_otherwise(), ids=lambda v: v if isinstance(v, str) else "")
def test_a_non_canonical_identity_key_verifies_nothing(name, public, backend):
    """OpenSSL decodes a non-canonical key as the point it stands for, so
    the identity written that way takes R = [S]B as a signature over any
    message; the small-order check alone missed it (it never decodes a
    non-canonical encoding). The precheck refuses it before OpenSSL sees
    it, as the pure code always did."""
    s = 12345
    forged = _ed25519._encode_point(_ed25519._scalar_mult(_ed25519.BASE, s)) + s.to_bytes(32, "little")
    raw_openssl = True
    try:
        ed25519.Ed25519PublicKey.from_public_bytes(public).verify(forged, b"any message at all")
    except Exception:  # noqa: BLE001
        raw_openssl = False
    assert raw_openssl, "OpenSSL takes this encoding: the reason the precheck exists"
    assert _ed25519.precheck(public, forged) is False
    assert crypto_backend.ed25519_verify(public, b"any message at all", forged) is False
    assert sat_identity.verify(public, b"any message at all", forged) is False


def test_precheck_passes_every_well_formed_signature():
    for seed, message in _random_cases(10, 99):
        public, signature = _ed25519.public_key(seed), _ed25519.sign(seed, message)
        assert _ed25519.precheck(public, signature)
        assert sat_ed25519.precheck(public, signature)


def test_the_twins_are_still_byte_identical():
    repo = Path(__file__).resolve().parents[2]
    assert (repo / "domovoi" / "_ed25519.py").read_bytes() == (repo / "satellite" / "_ed25519.py").read_bytes()


# ─── RSA PKCS#1 v1.5: the APK check ───────────────────────────────────────

@pytest.fixture(scope="module")
def rsa_keys():
    return [rsa.generate_private_key(public_exponent=65537, key_size=bits) for bits in (2048, 3072)]


def test_rsa_signatures_verify_on_both_backends_and_tampering_fails(rsa_keys, monkeypatch):
    rng = random.Random(8017)
    cases = []
    for key in rsa_keys:
        nums = key.public_key().public_numbers()
        for digest, algorithm in (("sha256", hashes.SHA256()), ("sha512", hashes.SHA512())):
            message = rng.randbytes(rng.choice((0, 17, 4096)))
            signature = key.sign(message, padding.PKCS1v15(), algorithm)
            cases.append((nums.n, nums.e, signature, message, digest))

    def verdicts() -> list[tuple[bool, ...]]:
        out = []
        for n, e, signature, message, digest in cases:
            bad_sig = bytearray(signature)
            bad_sig[rng.randrange(len(bad_sig))] ^= 1 << rng.randrange(8)
            other = "sha512" if digest == "sha256" else "sha256"
            out.append((
                crypto_backend.rsa_pkcs1v15_verify(n, e, signature, message, digest),
                crypto_backend.rsa_pkcs1v15_verify(n, e, signature, message + b"!", digest),
                crypto_backend.rsa_pkcs1v15_verify(n, e, bytes(bad_sig), message, digest),
                crypto_backend.rsa_pkcs1v15_verify(n, e, signature, message, other),
                crypto_backend.rsa_pkcs1v15_verify(n, e, signature[:-1], message, digest),
                crypto_backend.rsa_pkcs1v15_verify(n, e, n.to_bytes(len(signature), "big"), message, digest),
                crypto_backend.rsa_pkcs1v15_verify(n, 3, signature, message, digest),
                crypto_backend.rsa_pkcs1v15_verify(n + 1, e, signature, message, digest),
            ))
        return out

    rng.seed(1)
    with_lib = verdicts()
    monkeypatch.setattr(crypto_backend, "_import_cryptography", lambda: None)
    crypto_backend.reset()
    assert crypto_backend.backend() == "pure"
    rng.seed(1)
    pure = verdicts()
    assert with_lib == pure
    assert all(v == (True, False, False, False, False, False, False, False) for v in with_lib)


def test_an_unknown_digest_is_a_programming_error(backend):
    with pytest.raises(ValueError):
        crypto_backend.rsa_pkcs1v15_verify((1 << 2047) | 1, 65537, b"\x00" * 256, b"m", "md5")


def _apk_verdict(name: str) -> tuple:
    try:
        v = apk_signature.verify(APKS / name)
    except apk_signature.ApkSignatureError as e:
        return ("refused", str(e))
    return ("ok", v.cert_sha256, v.schemes, v.key_bits)


def test_every_apk_fixture_gets_the_same_verdict_on_both_backends(monkeypatch):
    names = sorted(APK_META)
    assert len(names) >= 10
    with_lib = {name: _apk_verdict(name) for name in names}
    monkeypatch.setattr(crypto_backend, "_import_cryptography", lambda: None)
    crypto_backend.reset()
    assert crypto_backend.backend() == "pure"
    pure = {name: _apk_verdict(name) for name in names}
    assert with_lib == pure
    # And what they are: the signed ones name the certificate apksigner printed.
    signed = [n for n, m in APK_META.items()
              if m["key"] in ("release", "other") and m["apksigner_v2"] and "refused" not in m["about"]]
    assert signed
    for name in signed:
        assert with_lib[name][0] == "ok" and with_lib[name][1] == APK_META[name]["apksigner_cert_sha256"]


def _signature_offsets(data: bytes) -> list[int]:
    """A byte inside each signer's RSA signature (v2's, then v3's): the
    algorithm id's second and fourth appearance in the signing block (the
    first of each pair is in the signed data's digest list), past the id
    and the signature's length."""
    block_start = data.index(b"APK Sig Block 42") - 200_000
    algo = (0x0104).to_bytes(4, "little")
    found, pos = [], max(block_start, 0)
    while len(found) < 4:
        pos = data.index(algo, pos + 1)
        found.append(pos)
    return [found[1] + 8 + 17, found[3] + 8 + 17]


def test_a_tampered_apk_signature_is_refused_on_both_backends(tmp_path, monkeypatch):
    data = (APKS / "release-arm64-v8a.apk").read_bytes()
    target = tmp_path / "t.apk"
    results = {}
    for use_lib in (True, False):
        if not use_lib:
            monkeypatch.setattr(crypto_backend, "_import_cryptography", lambda: None)
        crypto_backend.reset()
        assert crypto_backend.backend() == ("cryptography" if use_lib else "pure")
        out = []
        for offset in _signature_offsets(data):
            bad = bytearray(data)
            bad[offset] ^= 0x01
            target.write_bytes(bytes(bad))
            with pytest.raises(apk_signature.ApkSignatureError) as e:
                apk_signature.verify(target)
            out.append(str(e.value))
        results[use_lib] = out
    assert results[True] == results[False]
    assert results[True] == ["the v2 signature does not verify", "the v3 signature does not verify"]


# ─── reported on the version read ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_version_read_reports_the_backend(monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from domovoi import git_version
    from domovoi.main import app as core_app
    from domovoi.tests.auth_testkit import HEADER, install_fake_db

    async def fake_state():
        return {"sha": "abc1234", "running_sha": "abc1234", "checkout_sha": "abc1234",
                "restart_required": False}

    monkeypatch.setattr(git_version, "version_state", fake_state)
    install_fake_db(monkeypatch, admin=True, sessions={"admin"}, device_token="device")
    async with AsyncClient(transport=ASGITransport(app=core_app, raise_app_exceptions=False),
                           base_url="http://test") as c:
        r = await c.get("/v1/admin/version", headers={HEADER: "device"})
    assert r.status_code == 200, r.text
    crypto = r.json()["crypto"]
    assert crypto["backend"] == "cryptography" and crypto["signing"] is True
    assert crypto["cryptography"] == crypto_backend.info()["cryptography"]


def test_fingerprints_do_not_depend_on_the_backend(tmp_path):
    """The same key file gives the same public key and fingerprint as the
    pure reference: an install that signed with the pure code before 1.3.0
    keeps its identity, and every card prepared from it keeps trusting it."""
    path = tmp_path / "server-identity.json"
    server_identity.reset_cache()
    ident = server_identity.load_or_create(path)
    assert ident.public == _ed25519.public_key(ident.seed)
    assert ident.fingerprint == server_identity.fingerprint_for(_ed25519.public_key(ident.seed))
    message = server_identity.health_message("abc", "192.168.1.20:6370")
    assert ident.sign(message) == _ed25519.sign(ident.seed, message)
    server_identity.reset_cache()
