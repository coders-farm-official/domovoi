"""The house's own away keys, made once and kept in ``<config dir>/away/``.

Away chat (1.5.0) has keys of its own, never the Friend Houses ones, so a
friend's keys and a phone's never meet:

``noise.key``
    the house's away Noise static key (X25519, 32 raw bytes). Its public
    half is ``house_noise_pub`` in an enrolment's answer; the private half
    is mounted read-only into the listener's container and nowhere else.
``onion.key``
    the seed of the away onion's ed25519 key (32 raw bytes). The house makes
    the onion key itself, so the first phone's enrolment can be answered
    with the address before Tor has ever started (:func:`write_tor_keys`
    gives Tor the same key in its own format).
``bridge.secret``
    a random secret the core makes at each start: the web process's away
    bridge route takes only a caller that presents it (the core, relaying
    a phone's request), on top of the household token.

Every file is created atomically, exclusively and 0600 (two processes
may race to make one: the loser reads the winner's).
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

NOISE_FILE = "noise.key"
ONION_FILE = "onion.key"
BRIDGE_FILE = "bridge.secret"

_SECRET_HEADER = b"== ed25519v1-secret: type0 ==\x00\x00\x00"
_PUBLIC_HEADER = b"== ed25519v1-public: type0 ==\x00\x00\x00"


def folder() -> Path:
    from domovoi import admin_auth

    return Path(admin_auth.CONFIG_DIR) / "away"


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def unb64url(text: str, size: int) -> bytes | None:
    """``size`` bytes from unpadded base64url, or None for any other shape."""
    if not isinstance(text, str) or len(text) != (size * 4 + 2) // 3:
        return None
    try:
        raw = base64.b64decode(text + "=" * (-len(text) % 4), altchars=b"-_", validate=True)
    except (ValueError, TypeError):
        return None
    if len(raw) != size or b64url(raw) != text:
        return None
    return raw


def _ensure_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:  # pragma: no cover — Windows dev boxes
        pass


def _create_once(path: Path, data: bytes) -> bytes:
    """Write ``data`` to ``path`` if no file is there yet (exclusive, 0600,
    via a temp name and a hard link so a reader never sees half a file);
    return what the file holds either way."""
    _ensure_folder(path.parent)
    if not path.exists():
        tmp = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        fd = os.open(tmp, flags, 0o600)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            os.link(tmp, path)  # fails if another process made it first
        except FileExistsError:
            pass
        except OSError:
            # A filesystem without hard links: an exclusive rename stand-in.
            if not path.exists():
                os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
    return path.read_bytes()


def _read_exact(path: Path, size: int) -> bytes | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    return raw if len(raw) == size else None


# ─── the Noise key ────────────────────────────────────────────────────────


def noise_path() -> Path:
    return folder() / NOISE_FILE


def noise_key(*, create: bool = False) -> X25519PrivateKey | None:
    """The house's away Noise key, made on first use with ``create``."""
    path = noise_path()
    raw = _read_exact(path, 32)
    if raw is None and create:
        raw = _create_once(path, X25519PrivateKey.generate().private_bytes_raw())
        if len(raw) != 32:
            raise RuntimeError("the away Noise key file is damaged")
    if raw is None:
        return None
    return X25519PrivateKey.from_private_bytes(raw)


def noise_public(key: X25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def load_noise_file(path: Path) -> X25519PrivateKey | None:
    """For the listener: the key as mounted, or None."""
    raw = _read_exact(path, 32)
    return X25519PrivateKey.from_private_bytes(raw) if raw is not None else None


# ─── the onion key ────────────────────────────────────────────────────────


def onion_seed(*, create: bool = False) -> bytes | None:
    path = folder() / ONION_FILE
    raw = _read_exact(path, 32)
    if raw is None and create:
        raw = _create_once(path, secrets.token_bytes(32))
        if len(raw) != 32:
            raise RuntimeError("the away onion key file is damaged")
    return raw


def onion_public(seed: bytes) -> bytes:
    return Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes(
        Encoding.Raw, PublicFormat.Raw)


def onion_address(seed: bytes) -> str:
    from domovoi.friend_houses import identity as fid

    return fid.onion_address(onion_public(seed))


def tor_secret_key(seed: bytes) -> bytes:
    """Tor's ``hs_ed25519_secret_key``: its header, then the expanded
    ed25519 secret (SHA-512 of the seed, the scalar half clamped)."""
    h = bytearray(hashlib.sha512(seed).digest())
    h[0] &= 248
    h[31] &= 63
    h[31] |= 64
    return _SECRET_HEADER + bytes(h)


def write_tor_keys(service_dir: Path, seed: bytes) -> None:
    """Give Tor the away onion's key (``hs-away``), unless the files
    already say exactly this."""
    from domovoi.friend_houses import tor_config as tc

    secret = tor_secret_key(seed)
    public = _PUBLIC_HEADER + onion_public(seed)
    hostname = (onion_address(seed) + "\n").encode("ascii")
    for name, data in (("hs_ed25519_secret_key", secret), ("hs_ed25519_public_key", public),
                       ("hostname", hostname)):
        path = service_dir / name
        if _read_exact(path, len(data)) != data:
            tc._write_private(path, data)


# ─── the bridge secret ────────────────────────────────────────────────────

_BRIDGE: dict[str, str | None] = {"secret": None}


def bridge_secret() -> str:
    """The core's secret for this start, written for the web process to
    compare against. Made once per process."""
    secret = _BRIDGE["secret"]
    if secret is None:
        from domovoi.friend_houses import tor_config as tc

        secret = secrets.token_hex(32)
        _ensure_folder(folder())
        tc._write_private(folder() / BRIDGE_FILE, secret + "\n")
        _BRIDGE["secret"] = secret
    return secret


def bridge_matches(presented: str | None) -> bool:
    """For the web process: whether ``presented`` is the core's current
    secret (constant time; False when there is none yet)."""
    if not presented:
        return False
    try:
        stored = (folder() / BRIDGE_FILE).read_text(encoding="ascii").strip()
    except OSError:
        return False
    if len(stored) != 64:
        return False
    return secrets.compare_digest(stored.encode("ascii"),
                                  presented.strip().encode("utf-8", "replace"))


def reset_bridge_secret() -> None:
    """For tests."""
    _BRIDGE["secret"] = None
