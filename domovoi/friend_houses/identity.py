"""A house's Friend Houses keys, its signatures and its address statements.

Friend Houses ("serverless messaging and friend system between households")
identifies a house by its **house key** (Ed25519). Friends know the house by
that key; everything a house says about itself is signed with it.

Beside it sits the **session key** (X25519), the house's static key in the
Noise handshakes (:mod:`domovoi.friend_houses.noise`). It is a separate key,
never derived from the house key, and it is bound to the house key by the
signed **address statement**: "my session key is S and my onion address is
O, as of time T".

Where the keys live
-------------------
One JSON file, ``<config dir>/friend-houses/house-keys.json``, mode 0600,
written the same way as the server identity (domovoi/server_identity.py):
never world-readable, not even between create and chmod. The keys are
deliberately **not** in Postgres, so the database dump the update unit takes
before every update never holds them. The file is created only when an
admin turns Friend Houses on; a house that never does has no keys.

Losing this file means a new identity: every friend has to be friended
again. The dashboard says so plainly.

Signatures
----------
Every signature covers a **context string** first (threat-model finding 17),
so a signature made for one purpose can never be replayed as another. The
signed bytes are ``len(context) || context || body``.

Address statements count in unix seconds
----------------------------------------
Friends keep the newest statement they have seen and refuse older ones
(finding 4). The counter is the issue time in seconds, not a stored number,
so a house whose database was rolled back by a failed update still issues
statements newer than anything its friends hold (finding 16).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import struct
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from domovoi import crypto_backend
from domovoi.friend_houses.noise import public_bytes

log = logging.getLogger(__name__)

KEYS_SCHEMA = 1

# ─── signing contexts ─────────────────────────────────────────────────────
# One per use. Never reuse one for a second purpose; add a new string.
CTX_ADDRESS = b"domovoi-fh-address-v1"
CTX_RETIRE = b"domovoi-fh-retire-v1"
CTX_FRIEND_FILE = b"domovoi-fh-friend-file-v1"
CTX_MESSAGE = b"domovoi-fh-message-v1"
_CONTEXTS = frozenset({CTX_ADDRESS, CTX_RETIRE, CTX_FRIEND_FILE, CTX_MESSAGE})


def signed_bytes(context: bytes, body: bytes) -> bytes:
    """What a signature actually covers: the context, length-prefixed, then
    the body."""
    if context not in _CONTEXTS:
        raise ValueError(f"unknown signing context {context!r}")
    return bytes([len(context)]) + context + body


def verify(house_key: bytes, context: bytes, body: bytes, signature: bytes) -> bool:
    """Check a friend's signature. Never raises on hostile input."""
    if len(house_key) != 32 or len(signature) != 64:
        return False
    try:
        return crypto_backend.ed25519_verify(house_key, signed_bytes(context, body), signature)
    except Exception:  # noqa: BLE001 — any malformed input is just "no"
        return False


def fingerprint(house_key: bytes) -> str:
    """``SHA256:<base64>``: the same style as the server identity's."""
    digest = hashlib.sha256(house_key).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


# ─── onion addresses ──────────────────────────────────────────────────────

_ONION_VERSION = b"\x03"


def onion_address(onion_key: bytes) -> str:
    """The v3 ``.onion`` address for a 32-byte onion service public key."""
    if len(onion_key) != 32:
        raise ValueError("an onion key is 32 bytes")
    checksum = hashlib.sha3_256(b".onion checksum" + onion_key + _ONION_VERSION).digest()[:2]
    raw = onion_key + checksum + _ONION_VERSION
    return base64.b32encode(raw).decode("ascii").lower() + ".onion"


def parse_onion(address: str) -> bytes | None:
    """The onion key inside a v3 address, or None if it isn't a valid one."""
    if not isinstance(address, str):
        return None
    text = address.strip().lower()
    if text.endswith(".onion"):
        text = text[: -len(".onion")]
    if len(text) != 56:
        return None
    try:
        raw = base64.b32decode(text.upper())
    except (ValueError, TypeError):
        return None
    key, checksum, version = raw[:32], raw[32:34], raw[34:]
    if version != _ONION_VERSION:
        return None
    expected = hashlib.sha3_256(b".onion checksum" + key + version).digest()[:2]
    return key if secrets.compare_digest(checksum, expected) else None


# ─── address statements ───────────────────────────────────────────────────

_STATEMENT = struct.Struct(">B32s32s32sQ")  # version, house, session, onion, issued_at
STATEMENT_VERSION = 1
STATEMENT_SIZE = _STATEMENT.size + 64


@dataclass(frozen=True)
class AddressStatement:
    house_key: bytes
    session_key: bytes
    onion_key: bytes
    issued_at: int
    signature: bytes

    @property
    def onion(self) -> str:
        return onion_address(self.onion_key)

    def body(self) -> bytes:
        return _STATEMENT.pack(
            STATEMENT_VERSION, self.house_key, self.session_key, self.onion_key, self.issued_at
        )

    def to_bytes(self) -> bytes:
        return self.body() + self.signature

    @classmethod
    def parse(cls, data: bytes) -> AddressStatement | None:
        """A verified statement, or None. Checks size, version and signature
        before anything is trusted."""
        if not isinstance(data, (bytes, bytearray)) or len(data) != STATEMENT_SIZE:
            return None
        body, signature = bytes(data[: _STATEMENT.size]), bytes(data[_STATEMENT.size:])
        version, house, session, onion, issued_at = _STATEMENT.unpack(body)
        if version != STATEMENT_VERSION:
            return None
        if not verify(house, CTX_ADDRESS, body, signature):
            return None
        return cls(house, session, onion, issued_at, signature)

    def supersedes(self, other: AddressStatement | None) -> bool:
        """Whether a friend holding ``other`` should take this one instead."""
        if other is None:
            return True
        return self.house_key == other.house_key and self.issued_at > other.issued_at


# ─── the key file ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class HouseKeys:
    house_seed: bytes
    session_private: bytes
    path: Path | None = None

    @property
    def house_key(self) -> bytes:
        return crypto_backend.ed25519_public_key(self.house_seed)

    @property
    def session(self) -> X25519PrivateKey:
        return X25519PrivateKey.from_private_bytes(self.session_private)

    @property
    def session_key(self) -> bytes:
        return public_bytes(self.session)

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.house_key)

    def sign(self, context: bytes, body: bytes) -> bytes:
        return crypto_backend.ed25519_sign(self.house_seed, signed_bytes(context, body))

    def address_statement(self, onion_key: bytes, issued_at: int) -> AddressStatement:
        draft = AddressStatement(self.house_key, self.session_key, onion_key, issued_at, b"")
        return AddressStatement(
            draft.house_key, draft.session_key, onion_key, issued_at,
            self.sign(CTX_ADDRESS, draft.body()),
        )


def keys_path() -> Path:
    from domovoi.admin_auth import CONFIG_DIR

    return CONFIG_DIR / "friend-houses" / "house-keys.json"


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(value: object, size: int) -> bytes | None:
    if not isinstance(value, str):
        return None
    try:
        raw = base64.b64decode(value, validate=True)
    except ValueError:
        return None
    return raw if len(raw) == size else None


def load(path: Path | None = None) -> HouseKeys | None:
    """This house's keys, or None when Friend Houses was never turned on (or
    the file is unreadable: a broken file is reported, never replaced)."""
    target = keys_path() if path is None else path
    try:
        doc = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        log.error("Friend Houses keys at %s can't be read (%s); not replacing them", target, e)
        return None
    if not isinstance(doc, dict) or doc.get("schema") != KEYS_SCHEMA:
        log.error("Friend Houses keys at %s are not a schema-%d file", target, KEYS_SCHEMA)
        return None
    seed = _unb64(doc.get("house_seed"), 32)
    session = _unb64(doc.get("session_private"), 32)
    if seed is None or session is None:
        log.error("Friend Houses keys at %s are damaged", target)
        return None
    return HouseKeys(seed, session, target)


def create(path: Path | None = None) -> HouseKeys:
    """Make this house's keys. Refuses if a file is already there, readable
    or not: replacing it would silently end every friendship."""
    target = keys_path() if path is None else path
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(target.parent, 0o700)
    except OSError:  # pragma: no cover — Windows dev boxes
        pass
    keys = HouseKeys(secrets.token_bytes(32), X25519PrivateKey.generate().private_bytes_raw(), target)
    body = json.dumps(
        {
            "schema": KEYS_SCHEMA,
            "house_seed": _b64(keys.house_seed),
            "session_private": _b64(keys.session_private),
            "house_key": _b64(keys.house_key),
            "fingerprint": keys.fingerprint,
        },
        indent=2,
    ) + "\n"
    # O_EXCL: never overwrite. 0600 from the first byte.
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, body.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    log.info("made this house's Friend Houses keys %s at %s", keys.fingerprint, target)
    return keys


# ─── restore epoch (finding 16) ───────────────────────────────────────────

EPOCH_BYTES = 8


def resolve_epoch(
    db_epoch: bytes | None, db_marker: str | None, file_marker: str | None
) -> tuple[bytes, str, bool]:
    """Decide this run's sending epoch.

    The database and a small file beside the keys each hold the same random
    marker. When they differ, the database was rolled back (a failed update
    restores its dump without anyone asking) or restored from elsewhere, so
    it may have forgotten messages this house already sent. Then a new epoch
    starts and sequence numbers restart at 1: friends treat a new epoch as a
    fresh run instead of refusing everything as replays.

    Returns ``(epoch, marker, changed)``. When ``changed`` is true the caller
    writes the new epoch and marker to the database and the marker file, in
    that order.
    """
    if db_epoch is not None and len(db_epoch) == EPOCH_BYTES and db_marker and db_marker == file_marker:
        return db_epoch, db_marker, False
    return secrets.token_bytes(EPOCH_BYTES), secrets.token_hex(16), True
