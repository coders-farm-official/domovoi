"""The core's own cryptographic identity, and the things it signs.

A satellite has to be able to tell *this* household's server from anything
else that answers on port 6370. Until this module there was nothing to tell
them apart with: a device swept its subnet, kept the first host whose
``/v1/health`` looked like Domovoi, wrote that address down and never
questioned it again, and every later code download was checked against a
manifest the same host had served — integrity, not authenticity.

So the install gets a long-lived Ed25519 key pair, generated the first time
it is needed and kept at ``~/.domovoi/server-identity.json`` (0600) — the
private half, which is why a satellite running on the same box keeps its
public record of which server it belongs to under a different name. Its
**fingerprint** — ``SHA256:<base64 of sha256(public key)>``, the shape ssh
prints — is the short string a person can read off the dashboard and
compare, and the exact string that is baked into a prepared satellite image.

What the key signs:

* a **challenge** on ``/v1/health``. The caller sends a nonce it just made
  up; the answer carries the public key and a signature over that nonce, so
  a recording of an earlier answer proves nothing.
* the **code** and **plugin-payload manifests**. Each channel offers a
  ``manifest.sig`` endpoint whose body is a signed envelope carrying the
  manifest it signed, so a satellite can check the authenticity of the file
  list before it downloads a single byte — and the plain ``manifest``
  endpoint keeps serving the old unsigned shape for devices that predate
  this.

Backend: ``cryptography`` when it is installed, else the vendored pure
implementation in :mod:`domovoi._ed25519`. Same keys, same signatures.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

ALGORITHM = "ed25519"
FINGERPRINT_PREFIX = "SHA256:"
IDENTITY_SCHEMA = 1

# Domain separators. Every signature this key makes is over a string that
# starts with one of these, so a signature minted for one purpose can never
# be replayed as a signature for another.
HEALTH_CONTEXT = b"domovoi-health-v1"
MANIFEST_CONTEXT = b"domovoi-manifest-v1"

CODE_CHANNEL = "satellite-code"
PLUGIN_CHANNEL = "satellite-plugins"

_CHALLENGE_BYTES = 16
CHALLENGE_MAX_LEN = 128


# ─── backend ──────────────────────────────────────────────────────────────

def _backend() -> str:
    """Which implementation signs and verifies here: ``"cryptography"`` or
    ``"pure"``. Resolved per call so a test can see both."""
    try:
        import cryptography.hazmat.primitives.asymmetric.ed25519  # noqa: F401
    except Exception:      # noqa: BLE001 — any import failure means "not available"
        return "pure"
    return "cryptography"


def public_key_for(seed: bytes) -> bytes:
    if _backend() == "cryptography":
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519

        key = ed25519.Ed25519PrivateKey.from_private_bytes(seed)
        return key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    from domovoi import _ed25519

    return _ed25519.public_key(seed)


def sign(seed: bytes, message: bytes) -> bytes:
    if _backend() == "cryptography":
        from cryptography.hazmat.primitives.asymmetric import ed25519

        return ed25519.Ed25519PrivateKey.from_private_bytes(seed).sign(message)
    from domovoi import _ed25519

    return _ed25519.sign(seed, message)


def verify(public: bytes, message: bytes, signature: bytes) -> bool:
    """Whether ``signature`` is ``public``'s signature over ``message``.
    Never raises — a bad key, a bad signature and a wrong signature are one
    answer."""
    if _backend() == "cryptography":
        from cryptography.hazmat.primitives.asymmetric import ed25519

        try:
            ed25519.Ed25519PublicKey.from_public_bytes(public).verify(
                signature, message
            )
            return True
        except Exception:      # noqa: BLE001 — InvalidSignature and malformed input alike
            return False
    from domovoi import _ed25519

    return _ed25519.verify(public, message, signature)


# ─── encoding ─────────────────────────────────────────────────────────────

def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def unb64(value: str) -> bytes | None:
    """Decode a base64 field from an untrusted document, or None."""
    if not isinstance(value, str) or len(value) > 256:
        return None
    try:
        return base64.b64decode(value, validate=True)
    except Exception:      # noqa: BLE001 — any malformed input is just "no"
        return None


def fingerprint_for(public: bytes) -> str:
    """``SHA256:<base64 of sha256(public key)>`` — the ssh-style string a
    person compares by eye and an image bakes in."""
    digest = hashlib.sha256(public).digest()
    return FINGERPRINT_PREFIX + base64.b64encode(digest).decode("ascii").rstrip("=")


def canonical_json(doc: Any) -> bytes:
    """One byte string per document, whatever produced it: sorted keys, no
    incidental whitespace, UTF-8. Both ends of a signature compute this, so
    neither depends on how the other's JSON encoder felt about spacing."""
    return json.dumps(
        doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def health_message(challenge: str) -> bytes:
    """The bytes signed in a ``/v1/health`` challenge answer. The satellite
    builds this string too; they must agree byte for byte."""
    return HEALTH_CONTEXT + b"\n" + challenge.encode("utf-8")


def manifest_message(channel: str, manifest: Any) -> bytes:
    """The bytes signed for a file-channel manifest."""
    return (
        MANIFEST_CONTEXT + b"\n" + channel.encode("utf-8") + b"\n"
        + canonical_json(manifest)
    )


def new_challenge() -> str:
    return secrets.token_hex(_CHALLENGE_BYTES)


# ─── the identity itself ──────────────────────────────────────────────────

@dataclass(frozen=True)
class ServerIdentity:
    seed: bytes
    public: bytes

    @property
    def fingerprint(self) -> str:
        return fingerprint_for(self.public)

    @property
    def public_key_b64(self) -> str:
        return b64(self.public)

    def sign(self, message: bytes) -> bytes:
        return sign(self.seed, message)

    def public_document(self) -> dict[str, str]:
        """What is safe to hand anyone who asks — and exactly what an image
        bakes in."""
        return {
            "algorithm": ALGORITHM,
            "fingerprint": self.fingerprint,
            "public_key": self.public_key_b64,
        }

    def health_answer(self, challenge: str) -> dict[str, str]:
        doc = self.public_document()
        doc["challenge"] = challenge
        doc["signature"] = b64(self.sign(health_message(challenge)))
        return doc

    def signed_manifest(self, channel: str, manifest: Any) -> dict[str, Any]:
        """The ``manifest.sig`` envelope: the manifest, and a signature over
        it. Carrying the manifest inside the envelope is deliberate — a
        satellite that fetched the list and the signature as two requests
        could be handed a list that changed in between, and would then
        refuse a perfectly good upgrade."""
        doc: dict[str, Any] = self.public_document()
        doc["channel"] = channel
        doc["manifest"] = manifest
        doc["signature"] = b64(self.sign(manifest_message(channel, manifest)))
        return doc


def identity_path() -> Path:
    """Where the key lives. ``CONFIG_DIR``-relative like the setup code and
    the device token, so a throwaway harness run keeps its own.

    A satellite running on the same box keeps its record of which server it
    belongs to in ``server-fingerprint.json`` beside this — a different
    file, because that one is public and this one is a private key."""
    from domovoi.admin_auth import CONFIG_DIR

    return CONFIG_DIR / "server-identity.json"


class IdentityFileConflict(OSError):
    """The identity file holds a document that is not this core's key.

    An :class:`OSError` on purpose: every caller already degrades when the
    config dir cannot give up an identity (``/v1/health`` drops its
    identity block, the signed-manifest routes fail), and "there is a file
    here that is not mine" needs the same fail-closed handling as "I cannot
    read the directory". What it must never do is fall through to
    generating a new key over the top."""


# A satellite's recorded-fingerprint sidecar used to share this filename.
_SATELLITE_RECORD_KIND = "satellite-server-fingerprint"


def _refuse_a_foreign_document(path: Path) -> None:
    """Stop rather than generate a key over somebody else's document.

    The one document that could plausibly be sitting here is a satellite's
    recorded server fingerprint: public-only, and written to this exact
    path by every satellite built before it was given a name of its own.
    Overwriting it would both destroy that device's pin and — far worse —
    mint a new server identity, which orphans every card ever prepared
    from this install. A corrupt or truncated key file of our own is a
    different thing and still regenerates, exactly as before."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(doc, dict) or doc.get("private_key"):
        return
    if doc.get("kind") == _SATELLITE_RECORD_KIND or doc.get("fingerprint"):
        raise IdentityFileConflict(
            f"{path} holds a public fingerprint and no private key — that is "
            "a satellite's record of its server, not this core's identity. "
            "Refusing to overwrite it, because generating a new key here "
            "would orphan every satellite image prepared from this install. "
            "Move it aside (a satellite's record now lives in "
            "server-fingerprint.json) or restore this core's key file."
        )


def _read_identity(path: Path) -> ServerIdentity | None:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("algorithm") != ALGORITHM:
        return None
    seed = unb64(doc.get("private_key") or "")
    if seed is None or len(seed) != 32:
        return None
    try:
        public = public_key_for(seed)
    except ValueError:
        return None
    return ServerIdentity(seed=seed, public=public)


def _write_identity(path: Path, identity: ServerIdentity) -> None:
    """0600 before anything is written into it — the private half must never
    exist on disk world-readable, not even for the microsecond between
    create and chmod."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(
        {
            "schema": IDENTITY_SCHEMA,
            "algorithm": ALGORITHM,
            "private_key": b64(identity.seed),
            "public_key": identity.public_key_b64,
            "fingerprint": identity.fingerprint,
        },
        indent=2,
    ) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, body.encode("utf-8"))
    finally:
        os.close(fd)
    try:
        os.chmod(path, 0o600)
    except OSError:      # pragma: no cover — Windows dev boxes
        pass


_CACHE: dict[str, ServerIdentity] = {}


def load_or_create(path: Path | None = None) -> ServerIdentity:
    """This install's identity, generating it the first time. Idempotent:
    the file is the source of truth and is never regenerated over a usable
    one, because regenerating it would orphan every image ever prepared
    from this server."""
    target = identity_path() if path is None else path
    key = str(target)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    identity = _read_identity(target)
    if identity is None:
        _refuse_a_foreign_document(target)
        seed = secrets.token_bytes(32)
        identity = ServerIdentity(seed=seed, public=public_key_for(seed))
        _write_identity(target, identity)
        log.info(
            "generated this server's identity %s (%s backend) at %s",
            identity.fingerprint, _backend(), target,
        )
    _CACHE[key] = identity
    return identity


def reset_cache() -> None:
    """Forget the in-process cache. For tests, and for a rotation that
    rewrites the file underneath us."""
    _CACHE.clear()


def public_document() -> dict[str, str]:
    """The identity block ``/v1/health`` and the dashboard show. Degrades to
    an empty dict rather than failing a health check: an install whose
    config dir is read-only must still report healthy."""
    try:
        return load_or_create().public_document()
    except OSError as e:      # pragma: no cover — read-only config dir
        log.warning("could not load the server identity: %s", e)
        return {}
