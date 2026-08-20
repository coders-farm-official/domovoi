"""The E2E cryptography for ``ltl-remote/v1`` — see ``ltl/docs/PROTOCOL.md``.

Everything here is a pure function over bytes: no sockets, no database,
no settings. That is deliberate. This module is the half of the protocol
that has to agree, byte for byte, with ``ltl-frontend/js/e2e.js``, and
the only way to keep two implementations honest is to be able to run
both against the same vectors without standing anything up.

The primitives are the four WebCrypto gives a browser natively, so the
client side needs no library and no build step:

    ECDH P-256  ·  HKDF-SHA256  ·  AES-256-GCM  ·  ECDSA P-256

Nothing in here is novel. The handshake is a Noise ``KK`` pattern with an
added ephemeral-ephemeral term, written out longhand — the reasoning, and
the honest caveat about hand-assembled protocols, are in
``ltl/docs/SECURITY.md``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

PROTOCOL_VERSION = 1
PROTOCOL_LABEL = b"ltl-remote/v1"

# HKDF info labels. Changing any of these is a protocol break.
INFO_C2H = b"ltl-remote/v1 c2h"
INFO_H2C = b"ltl-remote/v1 h2c"
INFO_CONFIRM = b"ltl-remote/v1 confirm"

# AES-GCM nonce prefixes. Four bytes + an 8-byte counter = 12.
PREFIX_C2H = b"C2H_"
PREFIX_H2C = b"H2C_"

FINGERPRINT_LABEL = b"ltl-remote/v1 fp"
FINGERPRINT_BYTES = 16

CURVE = ec.SECP256R1()
_POINT_LEN = 65                       # uncompressed SEC1 for P-256
_NONCE_LEN = 16                       # handshake nonces
_MAX_COUNTER = (1 << 64) - 1


class CryptoError(Exception):
    """Any failure that must close the link. Deliberately opaque: callers
    log it, but never hand the message to a peer — a detailed decryption
    error is an oracle."""


# ─── base64url (unpadded, per the protocol doc) ─────────────────────────────


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def unb64u(text: str) -> bytes:
    if not isinstance(text, str):
        raise CryptoError("expected a base64url string")
    pad = "=" * (-len(text) % 4)
    try:
        return base64.urlsafe_b64decode(text + pad)
    except Exception as e:  # noqa: BLE001 — any decode failure is the same failure
        raise CryptoError("malformed base64url") from e


# ─── key material ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class KeyPair:
    """A P-256 keypair plus its cached uncompressed public point."""

    private: ec.EllipticCurvePrivateKey
    public_raw: bytes

    @property
    def public_b64(self) -> str:
        return b64u(self.public_raw)


def generate_keypair() -> KeyPair:
    private = ec.generate_private_key(CURVE)
    return KeyPair(private=private, public_raw=public_raw_of(private))


def public_raw_of(private: ec.EllipticCurvePrivateKey) -> bytes:
    return private.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )


def load_public(raw: bytes) -> ec.EllipticCurvePublicKey:
    """Parse an uncompressed SEC1 point. ``from_encoded_point`` rejects
    points that are not on the curve, which is exactly the invalid-curve
    check this needs — so a hostile peer cannot walk our private scalar
    out of us one ECDH at a time."""
    if not isinstance(raw, (bytes, bytearray)) or len(raw) != _POINT_LEN:
        raise CryptoError(f"public key must be {_POINT_LEN} uncompressed bytes")
    if raw[0] != 0x04:
        raise CryptoError("public key must be an uncompressed SEC1 point")
    try:
        return ec.EllipticCurvePublicKey.from_encoded_point(CURVE, bytes(raw))
    except Exception as e:  # noqa: BLE001
        raise CryptoError("public key is not a valid P-256 point") from e


def serialize_private(private: ec.EllipticCurvePrivateKey) -> bytes:
    return private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def deserialize_private(pem: bytes) -> ec.EllipticCurvePrivateKey:
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise CryptoError("stored key is not an EC private key")
    return key


# ─── the household identity (two keypairs, one fingerprint) ────────────────


@dataclass(frozen=True)
class Identity:
    """The household's long-term identity.

    Two keypairs rather than one: ``dh`` does the E2E key agreement with
    clients, ``sig`` proves to the relay that this agent is the
    household. Reusing a single key for both ECDH and ECDSA is
    discouraged, and generating a second keypair costs nothing.

    One fingerprint covers both, so a user comparing eight hex groups is
    verifying the whole identity rather than half of it.
    """

    dh: KeyPair
    sig: KeyPair

    @property
    def fingerprint(self) -> str:
        return fingerprint_text(self.dh.public_raw, self.sig.public_raw)


def fingerprint_bytes(dh_pub: bytes, sig_pub: bytes) -> bytes:
    digest = hashlib.sha256(FINGERPRINT_LABEL + dh_pub + sig_pub).digest()
    return digest[:FINGERPRINT_BYTES]


def fingerprint_text(dh_pub: bytes, sig_pub: bytes) -> str:
    """Uppercase hex in eight groups of four — the string a human
    compares between the dashboard and their phone."""
    hexed = fingerprint_bytes(dh_pub, sig_pub).hex().upper()
    return " ".join(hexed[i:i + 4] for i in range(0, len(hexed), 4))


def load_or_create_identity(key_dir: Path) -> Identity:
    """Load the household identity, generating it on first call.

    Private keys are written mode 0600 into the plugin's data dir and
    never leave the machine. Losing them un-pairs every client, which is
    why the dashboard's regenerate button asks twice.
    """
    key_dir.mkdir(parents=True, exist_ok=True)
    pairs: dict[str, KeyPair] = {}
    for role in ("dh", "sig"):
        path = key_dir / f"household_{role}.pem"
        if path.exists():
            private = deserialize_private(path.read_bytes())
        else:
            private = ec.generate_private_key(CURVE)
            # Create with restrictive permissions BEFORE writing bytes —
            # writing then chmod'ing leaves a window where the key is
            # world-readable.
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(serialize_private(private))
        pairs[role] = KeyPair(private=private, public_raw=public_raw_of(private))
    return Identity(dh=pairs["dh"], sig=pairs["sig"])


# ─── relay agent authentication (PROTOCOL.md §8) ───────────────────────────


def sign_challenge(sig_key: KeyPair, challenge: bytes, household_id: str) -> str:
    """DER ECDSA-P256-SHA256 over ``challenge || household_id``, base64url.

    The household id is inside the signed blob so a signature captured
    from one household can't be replayed to claim another's agent slot.
    """
    payload = challenge + household_id.encode("utf-8")
    return b64u(sig_key.private.sign(payload, ec.ECDSA(hashes.SHA256())))


def verify_challenge(
    sig_pub_raw: bytes, challenge: bytes, household_id: str, signature_b64: str
) -> bool:
    """The relay's side of the same check. Lives here so both halves of
    the check are written once and tested against each other."""
    from cryptography.exceptions import InvalidSignature

    payload = challenge + household_id.encode("utf-8")
    try:
        load_public(sig_pub_raw).verify(
            unb64u(signature_b64), payload, ec.ECDSA(hashes.SHA256())
        )
        return True
    except (InvalidSignature, CryptoError, ValueError):
        return False


# ─── the key schedule (PROTOCOL.md §3.3) ───────────────────────────────────


def transcript_hash(
    *,
    device_pub: bytes,
    eph_c_pub: bytes,
    nonce_c: bytes,
    static_s_pub: bytes,
    eph_s_pub: bytes,
    nonce_s: bytes,
) -> bytes:
    """Bind every public value of the handshake into the HKDF salt.

    Because the salt covers both ephemerals and both nonces, a relay that
    edits any handshake field produces different keys on the two sides
    and the confirmation tags fail — tampering shows up as a closed link,
    not as a session someone else can read.
    """
    return hashlib.sha256(
        PROTOCOL_LABEL
        + device_pub
        + eph_c_pub
        + nonce_c
        + static_s_pub
        + eph_s_pub
        + nonce_s
    ).digest()


def _hkdf(prk_salt: bytes, ikm: bytes, info: bytes, length: int = 32) -> bytes:
    """RFC 5869 extract-then-expand, matching WebCrypto's single-shot
    HKDF ``deriveBits`` exactly (same salt, same info, same output)."""
    return HKDF(
        algorithm=hashes.SHA256(), length=length, salt=prk_salt, info=info
    ).derive(ikm)


@dataclass(frozen=True)
class SessionSecrets:
    k_c2h: bytes
    k_h2c: bytes
    k_confirm: bytes

    def confirm_tag(self, who: bytes) -> bytes:
        return hmac.new(self.k_confirm, who, hashlib.sha256).digest()


def derive_secrets(
    *,
    dh1: bytes,
    dh2: bytes,
    dh3: bytes,
    transcript: bytes,
) -> SessionSecrets:
    ikm = dh1 + dh2 + dh3
    return SessionSecrets(
        k_c2h=_hkdf(transcript, ikm, INFO_C2H),
        k_h2c=_hkdf(transcript, ikm, INFO_H2C),
        k_confirm=_hkdf(transcript, ikm, INFO_CONFIRM),
    )


# ─── the sealed layer (PROTOCOL.md §4) ─────────────────────────────────────


class SealedLink:
    """AES-256-GCM in both directions with strictly increasing counters.

    Two counters, never shared: one for what we send, one for what we
    accept. The receive side refuses any counter that is not greater than
    the last accepted one, so replay and reordering fail closed rather
    than being silently tolerated.
    """

    def __init__(
        self,
        *,
        send_key: bytes,
        recv_key: bytes,
        send_prefix: bytes,
        recv_prefix: bytes,
    ) -> None:
        self._send = AESGCM(send_key)
        self._recv = AESGCM(recv_key)
        self._send_prefix = send_prefix
        self._recv_prefix = recv_prefix
        self._send_counter = 0
        self._last_recv_counter = -1

    @staticmethod
    def _nonce(prefix: bytes, counter: int) -> bytes:
        return prefix + counter.to_bytes(8, "big")

    @staticmethod
    def _aad(prefix: bytes, counter: int) -> bytes:
        return bytes([PROTOCOL_VERSION]) + prefix + counter.to_bytes(8, "big")

    def seal(self, plaintext: bytes) -> bytes:
        counter = self._send_counter
        if counter > _MAX_COUNTER:
            raise CryptoError("send counter exhausted; reconnect to rekey")
        self._send_counter += 1
        ciphertext = self._send.encrypt(
            self._nonce(self._send_prefix, counter),
            plaintext,
            self._aad(self._send_prefix, counter),
        )
        return counter.to_bytes(8, "big") + ciphertext

    def open(self, frame: bytes) -> bytes:
        if len(frame) < 8 + 16:
            raise CryptoError("sealed frame is too short to hold a tag")
        counter = int.from_bytes(frame[:8], "big")
        if counter <= self._last_recv_counter:
            raise CryptoError("replayed or reordered frame")
        try:
            plaintext = self._recv.decrypt(
                self._nonce(self._recv_prefix, counter),
                frame[8:],
                self._aad(self._recv_prefix, counter),
            )
        except Exception as e:  # noqa: BLE001 — never leak which check failed
            raise CryptoError("frame failed authentication") from e
        # Only advance after a successful open, so a forged frame with a
        # huge counter can't burn the window for legitimate traffic.
        self._last_recv_counter = counter
        return plaintext


# ─── the two handshake roles ───────────────────────────────────────────────


class HomeHandshake:
    """The home server's side. Two calls: :meth:`respond`, then
    :meth:`finish`.

    The caller supplies ``approved_device_pub`` — this class does not
    look devices up, because whether a device is approved is a database
    question and this module does no I/O. If the caller has no approved
    key, it must refuse before ever getting here.
    """

    def __init__(self, identity_dh: KeyPair) -> None:
        self._static = identity_dh
        self._eph = generate_keypair()
        self._nonce_s = os.urandom(_NONCE_LEN)
        self._secrets: SessionSecrets | None = None

    def respond(
        self, client_hello: dict[str, Any], approved_device_pub: bytes
    ) -> dict[str, Any]:
        if client_hello.get("t") != "client_hello":
            raise CryptoError("expected client_hello")
        if client_hello.get("v") != PROTOCOL_VERSION:
            raise CryptoError("unsupported protocol version")

        device_pub = unb64u(client_hello.get("device_pub", ""))
        eph_c_pub = unb64u(client_hello.get("eph_pub", ""))
        nonce_c = unb64u(client_hello.get("nonce_c", ""))
        if len(nonce_c) != _NONCE_LEN:
            raise CryptoError("bad client nonce length")
        # The device key we agree with is the APPROVED one from our own
        # database, not the one the peer sent. Comparing them is how a
        # peer claiming an approved device id but holding a different key
        # is caught before any secret is derived.
        if not hmac.compare_digest(device_pub, approved_device_pub):
            raise CryptoError("device key does not match the approved key")

        eph_c = load_public(eph_c_pub)
        dh1 = self._eph.private.exchange(ec.ECDH(), eph_c)
        dh2 = self._static.private.exchange(ec.ECDH(), eph_c)
        dh3 = self._eph.private.exchange(ec.ECDH(), load_public(device_pub))

        transcript = transcript_hash(
            device_pub=device_pub,
            eph_c_pub=eph_c_pub,
            nonce_c=nonce_c,
            static_s_pub=self._static.public_raw,
            eph_s_pub=self._eph.public_raw,
            nonce_s=self._nonce_s,
        )
        self._secrets = derive_secrets(
            dh1=dh1, dh2=dh2, dh3=dh3, transcript=transcript
        )
        return {
            "t": "home_hello",
            "v": PROTOCOL_VERSION,
            "eph_pub": self._eph.public_b64,
            "nonce_s": b64u(self._nonce_s),
            "confirm": b64u(self._secrets.confirm_tag(b"home")),
        }

    def finish(self, client_confirm: dict[str, Any]) -> SealedLink:
        if self._secrets is None:
            raise CryptoError("finish() before respond()")
        if client_confirm.get("t") != "client_confirm":
            raise CryptoError("expected client_confirm")
        expected = self._secrets.confirm_tag(b"client")
        if not hmac.compare_digest(
            unb64u(client_confirm.get("confirm", "")), expected
        ):
            raise CryptoError("client confirmation tag mismatch")
        return SealedLink(
            send_key=self._secrets.k_h2c,
            recv_key=self._secrets.k_c2h,
            send_prefix=PREFIX_H2C,
            recv_prefix=PREFIX_C2H,
        )


class ClientHandshake:
    """The client's side.

    The browser implements this in ``ltl-frontend/js/e2e.js``; this
    Python copy exists so the test suite can drive a full handshake
    against :class:`HomeHandshake` and so the JS can be checked against
    generated vectors. Keep the two in step.
    """

    def __init__(self, device: KeyPair, household_dh_pub: bytes, device_id: str) -> None:
        self._device = device
        self._household_pub_raw = household_dh_pub
        self._household_pub = load_public(household_dh_pub)
        self._device_id = device_id
        self._eph = generate_keypair()
        self._nonce_c = os.urandom(_NONCE_LEN)
        self._secrets: SessionSecrets | None = None

    def hello(self) -> dict[str, Any]:
        return {
            "t": "client_hello",
            "v": PROTOCOL_VERSION,
            "device_id": self._device_id,
            "device_pub": self._device.public_b64,
            "eph_pub": self._eph.public_b64,
            "nonce_c": b64u(self._nonce_c),
        }

    def finish(self, home_hello: dict[str, Any]) -> tuple[dict[str, Any], SealedLink]:
        if home_hello.get("t") != "home_hello":
            raise CryptoError("expected home_hello")
        if home_hello.get("v") != PROTOCOL_VERSION:
            raise CryptoError("unsupported protocol version")

        eph_s_pub = unb64u(home_hello.get("eph_pub", ""))
        nonce_s = unb64u(home_hello.get("nonce_s", ""))
        if len(nonce_s) != _NONCE_LEN:
            raise CryptoError("bad server nonce length")
        eph_s = load_public(eph_s_pub)

        dh1 = self._eph.private.exchange(ec.ECDH(), eph_s)
        dh2 = self._eph.private.exchange(ec.ECDH(), self._household_pub)
        dh3 = self._device.private.exchange(ec.ECDH(), eph_s)

        transcript = transcript_hash(
            device_pub=self._device.public_raw,
            eph_c_pub=self._eph.public_raw,
            nonce_c=self._nonce_c,
            static_s_pub=self._household_pub_raw,
            eph_s_pub=eph_s_pub,
            nonce_s=nonce_s,
        )
        self._secrets = derive_secrets(
            dh1=dh1, dh2=dh2, dh3=dh3, transcript=transcript
        )
        if not hmac.compare_digest(
            unb64u(home_hello.get("confirm", "")),
            self._secrets.confirm_tag(b"home"),
        ):
            # Either the household key we pinned is wrong or someone sat
            # in the middle. Both mean: do not proceed.
            raise CryptoError("home confirmation tag mismatch")

        confirm = {
            "t": "client_confirm",
            "v": PROTOCOL_VERSION,
            "confirm": b64u(self._secrets.confirm_tag(b"client")),
        }
        link = SealedLink(
            send_key=self._secrets.k_c2h,
            recv_key=self._secrets.k_h2c,
            send_prefix=PREFIX_C2H,
            recv_prefix=PREFIX_H2C,
        )
        return confirm, link
