"""The one door to signature arithmetic on the server.

Everything the core and the dashboard sign or check goes through here:

* **Ed25519** — the server identity (domovoi/server_identity.py): its key
  pair, the signed ``/v1/health`` challenge, the signed file-channel
  manifests. Used by both server processes.
* **RSA PKCS#1 v1.5, SHA-256 or SHA-512** — the APK signatures the core
  checks before it keeps or serves the Android app
  (domovoi/apk_signature.py; it parses the file, this does the RSA).

Both are done by ``cryptography`` (Apache-2.0 OR BSD-3-Clause, OpenSSL
inside), a core dependency pinned in both server locks. Before 1.3.0 the
core signed with its own pure-Python Ed25519 (domovoi/_ed25519.py, not
constant-time) unless an optional extra was installed, and checked RSA in
pure Python always.

**Signing never falls back.** Without ``cryptography`` the server is a
broken install: :func:`ed25519_public_key` and :func:`ed25519_sign` raise
:class:`CryptoUnavailable`, so no key is made and nothing is signed with
home-made arithmetic. A signature *check* touches only public data, so it
falls back to the pure-Python code (the same that 32-bit Pi satellites
run), which keeps an APK check or a test answering rather than crashing.
The backend is resolved once per process and logged once: INFO naming the
library and OpenSSL versions, or ERROR when it is missing. :func:`info` is
what ``GET /v1/admin/version`` reports as ``crypto``.

**Same answers on both backends.** The pure checks refuse some inputs
OpenSSL would take (a non-canonical Ed25519 key encoding; an RSA key
OpenSSL won't load). Those refusals run here, in front of either backend
(:func:`domovoi._ed25519.precheck`, :func:`_rsa_precheck`), so the accept
and reject results are identical whichever does the arithmetic:
domovoi/tests/test_crypto_backend.py holds them to it on the RFC 8032
vectors, the Windows Setup's edge vectors, the APK fixtures and random
keys.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import threading
from types import SimpleNamespace
from typing import Any

from domovoi import _ed25519

log = logging.getLogger(__name__)

CRYPTOGRAPHY = "cryptography"
PURE = "pure"

# DigestInfo prefixes for the pure RSA check, RFC 8017 section 9.2 note 1.
_DIGEST_INFO = {
    "sha256": bytes.fromhex("3031300d060960864801650304020105000420"),
    "sha512": bytes.fromhex("3051300d060960864801650304020305000440"),
}


class CryptoUnavailable(RuntimeError):
    """``cryptography`` is not installed, and the server does not sign
    without it. Not a ValueError on purpose: the identity loader reads a
    ValueError as "this key file is unusable" and would mint a new key."""


_LOCK = threading.Lock()
_STATE: dict[str, Any] | None = None


def _import_cryptography() -> SimpleNamespace | None:
    """The parts of ``cryptography`` used here, or None when it can't be
    imported. Its own function so a test can stand in for an install
    without it."""
    try:
        import cryptography
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa
    except Exception:      # noqa: BLE001 — any import failure means "not available"
        return None
    openssl = None
    try:
        from cryptography.hazmat.backends.openssl import backend as _openssl

        openssl = _openssl.openssl_version_text()
    except Exception:      # noqa: BLE001 — the version is a label, never a requirement
        pass
    return SimpleNamespace(
        version=getattr(cryptography, "__version__", None),
        openssl=openssl,
        InvalidSignature=InvalidSignature,
        hashes=hashes,
        serialization=serialization,
        ed25519=ed25519,
        padding=padding,
        rsa=rsa,
    )


def _state() -> dict[str, Any]:
    global _STATE
    with _LOCK:
        if _STATE is None:
            lib = _import_cryptography()
            if lib is None:
                _STATE = {"backend": PURE, "lib": None}
                log.error(
                    "cryptography is not installed: this server will not sign "
                    "(no server identity, no signed health answers or "
                    "manifests) and checks signatures with the pure-Python "
                    "fallback. Reinstall the dependencies from the lock "
                    "(pip install --require-hashes -r <the lock>)."
                )
            else:
                _STATE = {"backend": CRYPTOGRAPHY, "lib": lib}
                log.info(
                    "crypto backend: cryptography %s (%s) signs and checks "
                    "Ed25519 and checks RSA signatures",
                    lib.version, lib.openssl or "OpenSSL version unknown",
                )
        return _STATE


def _lib() -> SimpleNamespace | None:
    return _state()["lib"]


def reset() -> None:
    """Resolve the backend again on next use. For tests."""
    global _STATE
    with _LOCK:
        _STATE = None


def backend() -> str:
    """``"cryptography"``, or ``"pure"`` on an install without it."""
    return _state()["backend"]


def info() -> dict[str, Any]:
    """What ``GET /v1/admin/version`` reports as ``crypto``: the backend,
    the library and OpenSSL versions, and whether this server can sign."""
    lib = _lib()
    return {
        "backend": backend(),
        "cryptography": lib.version if lib else None,
        "openssl": lib.openssl if lib else None,
        "signing": lib is not None,
    }


def _require_lib(what: str) -> SimpleNamespace:
    lib = _lib()
    if lib is None:
        raise CryptoUnavailable(
            f"{what} needs the cryptography package, which is not installed "
            "on this server; the server never signs with the pure-Python "
            "fallback. Reinstall the dependencies from the lock."
        )
    return lib


# ─── Ed25519 ──────────────────────────────────────────────────────────────

def ed25519_public_key(seed: bytes) -> bytes:
    """The 32-byte public key for a 32-byte private seed. ValueError for a
    seed of the wrong size; :class:`CryptoUnavailable` without the
    library."""
    lib = _require_lib("making the server's Ed25519 key")
    if len(seed) != _ed25519.KEY_SIZE:
        raise ValueError("an ed25519 private seed is 32 bytes")
    key = lib.ed25519.Ed25519PrivateKey.from_private_bytes(seed)
    return key.public_key().public_bytes(
        encoding=lib.serialization.Encoding.Raw,
        format=lib.serialization.PublicFormat.Raw,
    )


def ed25519_sign(seed: bytes, message: bytes) -> bytes:
    """A 64-byte RFC 8032 signature over ``message``. ValueError for a seed
    of the wrong size; :class:`CryptoUnavailable` without the library."""
    lib = _require_lib("signing with the server's Ed25519 key")
    if len(seed) != _ed25519.KEY_SIZE:
        raise ValueError("an ed25519 private seed is 32 bytes")
    return lib.ed25519.Ed25519PrivateKey.from_private_bytes(seed).sign(message)


def ed25519_verify(public: bytes, message: bytes, signature: bytes) -> bool:
    """Whether ``signature`` is ``public``'s signature over ``message``.
    Never raises: a malformed key, a malformed signature, a key or R of
    small order and a wrong signature are one answer."""
    if not _ed25519.precheck(public, signature):
        return False
    lib = _lib()
    if lib is None:
        return _ed25519.verify(public, message, signature)
    try:
        lib.ed25519.Ed25519PublicKey.from_public_bytes(public).verify(signature, message)
    except Exception:      # noqa: BLE001 — InvalidSignature and malformed input alike
        return False
    return True


# ─── RSA PKCS#1 v1.5 ──────────────────────────────────────────────────────

def _rsa_precheck(n: int, e: int, signature: bytes) -> bool:
    """What both backends require before the arithmetic: a signature
    exactly the modulus's length and below it, and a key OpenSSL will load
    (odd modulus; odd exponent, 3 or more, below the modulus). A real RSA
    key always passes the key half."""
    k = (n.bit_length() + 7) // 8
    if len(signature) != k:
        return False
    if int.from_bytes(signature, "big") >= n:
        return False
    return n % 2 == 1 and 3 <= e < n and e % 2 == 1


def _pure_rsa_pkcs1v15_verify(n: int, e: int, signature: bytes, message: bytes, digest: str) -> bool:
    """RSASSA-PKCS1-v1_5 verification (RFC 8017 section 8.2.2) in pure
    Python: the whole encoded message is rebuilt and compared, never
    parsed. The fallback, after :func:`_rsa_precheck`."""
    k = (n.bit_length() + 7) // 8
    em = pow(int.from_bytes(signature, "big"), e, n).to_bytes(k, "big")
    t = _DIGEST_INFO[digest] + hashlib.new(digest, message).digest()
    if k < len(t) + 11:
        return False
    expected = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    return hmac.compare_digest(em, expected)


def rsa_pkcs1v15_verify(n: int, e: int, signature: bytes, message: bytes, digest: str) -> bool:
    """Whether ``signature`` is an RSASSA-PKCS1-v1_5 signature over
    ``message`` with ``digest`` (``"sha256"`` or ``"sha512"``) by the key
    (``n``, ``e``). Never raises for a bad signature or key; ValueError for
    a digest it doesn't know. The caller bounds the key size."""
    if digest not in _DIGEST_INFO:
        raise ValueError(f"unsupported digest {digest!r}")
    if not _rsa_precheck(n, e, signature):
        return False
    lib = _lib()
    if lib is None:
        return _pure_rsa_pkcs1v15_verify(n, e, signature, message, digest)
    algorithm = lib.hashes.SHA256() if digest == "sha256" else lib.hashes.SHA512()
    try:
        key = lib.rsa.RSAPublicNumbers(e, n).public_key()
        key.verify(signature, message, lib.padding.PKCS1v15(), algorithm)
    except Exception:      # noqa: BLE001 — InvalidSignature and a key OpenSSL refuses alike
        return False
    return True
