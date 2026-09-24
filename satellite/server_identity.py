"""Know which server is ours, and refuse the rest.

A satellite used to decide what "the server" meant by asking the network
and believing the first answer. This module is the other half of
:mod:`domovoi.server_identity`: it holds the **fingerprint** of the core
that prepared this device's image, and the checks that turn that string
into a decision.

Three checks, all of them "verify if known":

* :func:`verify_server` — ask ``/v1/health`` to sign a nonce we just made
  up and confirm the signature against the pinned fingerprint. Used by
  discovery before an address is ever written down, and again on every
  reconnect.
* :func:`verify_manifest_envelope` — confirm a code or plugin-payload file
  list before a single byte of it is downloaded.
* :func:`pinned_fingerprint` — what we are comparing against, and where it
  came from.

**Verify if known, not verify always.** A device prepared before server
identities existed has no fingerprint, and must keep working: with nothing
pinned, the checks report "unpinned" and the caller proceeds exactly as it
did before, recording the first identity it sees
(:func:`record_fingerprint`) so a *different* one is refused afterwards.
Baking a fingerprint at prepare time is what turns that into real
authentication from the very first boot.

What this module writes is only ever a **public** fingerprint, and it
writes it to a file of its own — ``~/.domovoi/server-fingerprint.json``.
The core's private key lives one filename away at
``~/.domovoi/server-identity.json``, and on any box that runs both (a
developer machine, an all-in-one install, a container satellite beside a
core) the two used to be the same path.

Stdlib only (plus the optional ``cryptography`` speed-up), because
:mod:`satellite.provisioning_mode` runs before the venv has anything else.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import urllib.request
from pathlib import Path
from typing import Any

log = logging.getLogger("satellite.server_identity")

ALGORITHM = "ed25519"
FINGERPRINT_PREFIX = "SHA256:"

# Twins of the core's domain separators — a signature is only ever accepted
# for the job it was minted for.
HEALTH_CONTEXT = b"domovoi-health-v1"
MANIFEST_CONTEXT = b"domovoi-manifest-v1"

CODE_CHANNEL = "satellite-code"
PLUGIN_CHANNEL = "satellite-plugins"

CONFIG_DIR = Path("~/.domovoi").expanduser()
# What this device learned on its own (trust on first use), for images
# prepared before fingerprints were baked in.
#
# The name matters. This file holds a PUBLIC fingerprint and nothing else;
# a Domovoi core keeps its PRIVATE key at ``~/.domovoi/server-identity.json``
# in the very same directory. On a Pi there is no core and the two could
# never meet, but a developer box, an all-in-one install and a container
# satellite sitting beside a core all have both — and two different
# documents, one of them a private key, must not share a path on the
# strength of nobody having written to it yet.
RECORD_SIDECAR = CONFIG_DIR / "server-fingerprint.json"
# Where the record lived before it was given a name of its own. Read for
# migration, never written: on a box that also runs a core, this path is
# the core's private key.
LEGACY_RECORD_SIDECAR = CONFIG_DIR / "server-identity.json"
# What the image was prepared with, installed by first boot. Root-owned:
# the satellite user can read it and cannot rewrite it. Shares the core's
# filename but never the core's directory — /etc/domovoi is root-owned and
# installed from the card, and no core keeps its key there.
ROOT_PIN = Path("/etc/domovoi/server-identity.json")
# A discovered address waiting for the dashboard to approve this device.
# Deliberately NOT config.toml: an address that no human has agreed to is
# not configuration yet.
PENDING_SERVER_SIDECAR = CONFIG_DIR / "pending-server.json"

HTTP_TIMEOUT_SEC = 5.0
_CHALLENGE_BYTES = 16


# ─── primitives ───────────────────────────────────────────────────────────

def _backend() -> str:
    try:
        import cryptography.hazmat.primitives.asymmetric.ed25519  # noqa: F401
    except Exception:      # noqa: BLE001 — any import failure means "not available"
        return "pure"
    return "cryptography"


def verify(public: bytes, message: bytes, signature: bytes) -> bool:
    """Whether ``signature`` is ``public``'s signature over ``message``.
    Never raises: everything wrong is the same answer."""
    if _backend() == "cryptography":
        from cryptography.hazmat.primitives.asymmetric import ed25519

        try:
            ed25519.Ed25519PublicKey.from_public_bytes(public).verify(
                signature, message
            )
            return True
        except Exception:      # noqa: BLE001
            return False
    from satellite import _ed25519

    return _ed25519.verify(public, message, signature)


def unb64(value: object) -> bytes | None:
    if not isinstance(value, str) or not value or len(value) > 256:
        return None
    try:
        return base64.b64decode(value, validate=True)
    except Exception:      # noqa: BLE001
        return None


def fingerprint_for(public: bytes) -> str:
    digest = hashlib.sha256(public).digest()
    return FINGERPRINT_PREFIX + base64.b64encode(digest).decode("ascii").rstrip("=")


def canonical_json(doc: Any) -> bytes:
    return json.dumps(
        doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def health_message(challenge: str) -> bytes:
    return HEALTH_CONTEXT + b"\n" + challenge.encode("utf-8")


def manifest_message(channel: str, manifest: Any) -> bytes:
    return (
        MANIFEST_CONTEXT + b"\n" + channel.encode("utf-8") + b"\n"
        + canonical_json(manifest)
    )


def new_challenge() -> str:
    return secrets.token_hex(_CHALLENGE_BYTES)


# ─── what we are comparing against ────────────────────────────────────────

def _read_json(path: Path) -> dict[str, Any]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def normalize_fingerprint(value: object) -> str | None:
    """A fingerprint from config, a pin file or the wire, or None. Only the
    shape this codebase writes is accepted — anything else is a typo or a
    different scheme, and guessing at either would be worse than saying so."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or not text.startswith(FINGERPRINT_PREFIX) or len(text) > 128:
        return None
    return text


RECORD_SCHEMA = 1
# Stamped into every record this module writes, so the document says what
# it is rather than being identified by where it happens to sit.
RECORD_KIND = "satellite-server-fingerprint"
# Fields that only ever appear in a CORE's identity document. A file
# carrying one of them is somebody's private key, whatever its name.
_PRIVATE_FIELDS = ("private_key", "seed", "secret_key")


def _holds_a_private_key(doc: dict[str, Any]) -> bool:
    return any(doc.get(field) for field in _PRIVATE_FIELDS)


def _read_record(path: Path, *, what: str) -> dict[str, Any]:
    """A recorded-fingerprint document, or nothing.

    A document with a private key in it is a core's identity, not this
    device's note of which server it met. Trusting its ``fingerprint``
    field would pin the satellite to whatever core shares its filesystem —
    so it is refused, and said out loud rather than swallowed."""
    doc = _read_json(path)
    if not doc:
        return {}
    if _holds_a_private_key(doc):
        log.error(
            "%s at %s contains a private key: that is a Domovoi core's own "
            "identity, not this device's record of its server. Ignoring it. "
            "This satellite's record belongs in %s.",
            what, path, RECORD_SIDECAR.name,
        )
        return {}
    return doc


def _write_record(doc: dict[str, Any]) -> bool:
    """Create the record file, and only create it.

    ``O_EXCL``: a file already at this path was written by something else —
    a core's key that ended up here, another process, a person — and this
    module does not get to decide what happens to it. Best-effort
    otherwise: a read-only config dir costs the record, not the
    connection."""
    try:
        RECORD_SIDECAR.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(
            RECORD_SIDECAR, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
    except FileExistsError:
        log.warning(
            "not overwriting %s — this module only ever creates it",
            RECORD_SIDECAR,
        )
        return False
    except OSError as e:
        log.debug("could not record the server identity: %s", e)
        return False
    try:
        os.write(fd, (json.dumps(doc, indent=2) + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    try:
        os.chmod(RECORD_SIDECAR, 0o600)
    except OSError:      # pragma: no cover — Windows dev boxes
        pass
    return True


def recorded_document() -> dict[str, Any]:
    """This device's own record of the server it met, migrating one written
    at the old shared path across the first time it is read.

    A satellite already in the field recorded its pin at
    :data:`LEGACY_RECORD_SIDECAR`. Leaving it there would silently unpin
    the device the moment this code lands — which is a security regression,
    not a rename — so the PUBLIC fields are copied to the new file. The old
    one is left exactly where it is: on a box that also runs a core, it is
    that core's private key, and this module never writes to it."""
    doc = _read_record(RECORD_SIDECAR, what="the recorded server fingerprint")
    if normalize_fingerprint(doc.get("fingerprint")):
        return doc
    legacy = _read_record(
        LEGACY_RECORD_SIDECAR, what="the pre-rename recorded server fingerprint"
    )
    fingerprint = normalize_fingerprint(legacy.get("fingerprint"))
    if not fingerprint:
        return {}
    migrated: dict[str, Any] = {
        "schema": RECORD_SCHEMA,
        "kind": RECORD_KIND,
        "algorithm": legacy.get("algorithm") or ALGORITHM,
        "fingerprint": fingerprint,
    }
    public_key = legacy.get("public_key")
    if isinstance(public_key, str) and public_key:
        migrated["public_key"] = public_key
    if _write_record(migrated):
        log.warning(
            "moved this device's recorded server fingerprint %s from %s to "
            "%s — the old path is a core's private-key file and is left "
            "untouched", fingerprint, LEGACY_RECORD_SIDECAR, RECORD_SIDECAR,
        )
    return migrated


def pinned_fingerprint(configured: object = None) -> tuple[str | None, str]:
    """The fingerprint this device holds the server to, and where it came
    from: ``"config"`` (baked into config.toml at adoption), ``"image"``
    (the root-owned copy first boot installed), ``"recorded"`` (learned on
    first contact) or ``"none"``."""
    from_config = normalize_fingerprint(configured)
    if from_config:
        return from_config, "config"
    from_image = normalize_fingerprint(_read_json(ROOT_PIN).get("fingerprint"))
    if from_image:
        return from_image, "image"
    recorded = normalize_fingerprint(recorded_document().get("fingerprint"))
    if recorded:
        return recorded, "recorded"
    return None, "none"


def record_fingerprint(fingerprint: str, public_key: str | None = None) -> bool:
    """Write down the identity we just met, so a different one is refused
    from here on. Only ever writes the FIRST one: overwriting the record
    would turn trust-on-first-use into trust-on-every-use, which is not
    trust at all. Best-effort — a read-only config dir costs the record,
    not the connection."""
    if not normalize_fingerprint(fingerprint):
        return False
    existing = normalize_fingerprint(recorded_document().get("fingerprint"))
    if existing:
        return existing == fingerprint
    doc: dict[str, Any] = {
        "schema": RECORD_SCHEMA,
        "kind": RECORD_KIND,
        "algorithm": ALGORITHM,
        "fingerprint": fingerprint,
    }
    if public_key:
        doc["public_key"] = public_key
    if not _write_record(doc):
        return False
    log.info("recorded the Domovoi server identity %s", fingerprint)
    return True


# ─── the checks ───────────────────────────────────────────────────────────

class IdentityError(Exception):
    """A server that could not prove it is ours."""


class IdentityUnavailable(IdentityError):
    """The server answered, and has no identity to offer at all.

    Told apart from every other refusal because it is the one that says
    something permanent about the host rather than about this attempt: an
    older core will not grow an identity between now and the next
    reconnect, so a device with nothing pinned can stop asking instead of
    spending a round trip before every connect for the rest of the
    outage."""


def verify_health_document(
    doc: Any, *, challenge: str, expected_fingerprint: str | None
) -> str:
    """The fingerprint the answer proved, or raise :class:`IdentityError`.

    "Proved" means: the document carries a public key and a signature over
    the nonce WE chose in this call, the signature checks out against that
    key, and — when a fingerprint is pinned — the key hashes to it. Without
    a pin this still rules out a host that cannot sign at all, and gives
    the caller a fingerprint worth recording."""
    if not isinstance(doc, dict):
        raise IdentityError("the server's answer was not a document")
    identity = doc.get("identity")
    if not isinstance(identity, dict):
        raise IdentityUnavailable("the server offered no identity to check")
    if identity.get("algorithm") != ALGORITHM:
        raise IdentityUnavailable(
            f"unsupported identity algorithm {identity.get('algorithm')!r}"
        )
    public = unb64(identity.get("public_key"))
    signature = unb64(identity.get("signature"))
    if public is None or signature is None:
        raise IdentityError("the server's identity is malformed")
    if identity.get("challenge") != challenge:
        raise IdentityError("the server answered a different challenge")
    if not verify(public, health_message(challenge), signature):
        raise IdentityError("the server's signature did not verify")
    actual = fingerprint_for(public)
    claimed = normalize_fingerprint(identity.get("fingerprint"))
    if claimed and claimed != actual:
        raise IdentityError("the server's fingerprint does not match its key")
    if expected_fingerprint and actual != expected_fingerprint:
        raise IdentityError(
            f"this is a different server: {actual} is not the "
            f"{expected_fingerprint} this device was prepared for"
        )
    return actual


def fetch_health(
    http_base: str, *, challenge: str, timeout: float = HTTP_TIMEOUT_SEC,
    opener=None,
) -> Any:
    """GET ``/v1/health?challenge=…``. The nonce rides in the query so a
    core that predates challenges simply ignores it and answers as it
    always did."""
    opener = opener or urllib.request.urlopen
    url = f"{http_base.rstrip('/')}/v1/health?challenge={challenge}"
    with opener(url, timeout=timeout) as r:
        if getattr(r, "status", 200) != 200:
            raise IdentityError("the server did not answer /v1/health")
        return json.loads(r.read().decode("utf-8", "replace"))


def verify_server(
    http_base: str, *, expected_fingerprint: str | None,
    timeout: float = HTTP_TIMEOUT_SEC, opener=None,
) -> str:
    """Prove the host at ``http_base`` is the server this device belongs to,
    and return its fingerprint. Raises :class:`IdentityError` otherwise.

    With nothing pinned the host still has to hold a key and sign with it;
    the fingerprint comes back so the caller can record it."""
    challenge = new_challenge()
    try:
        doc = fetch_health(
            http_base, challenge=challenge, timeout=timeout, opener=opener
        )
    except IdentityError:
        raise
    except Exception as e:      # noqa: BLE001 — every transport failure is "not provable"
        raise IdentityError(f"could not reach {http_base}: {e}") from e
    return verify_health_document(
        doc, challenge=challenge, expected_fingerprint=expected_fingerprint
    )


def verify_manifest_envelope(
    doc: Any, *, channel: str, expected_fingerprint: str | None
) -> Any:
    """The manifest inside a signed ``manifest.sig`` envelope, or raise.

    The envelope carries the manifest it signed, so what is verified and
    what is used are the same object — there is no window in which the file
    list could change between the signature and the download."""
    if not isinstance(doc, dict):
        raise IdentityError("the signed manifest was not a document")
    if doc.get("algorithm") != ALGORITHM:
        raise IdentityError(f"unsupported signature algorithm {doc.get('algorithm')!r}")
    if doc.get("channel") != channel:
        raise IdentityError(
            f"this signature is for {doc.get('channel')!r}, not {channel!r}"
        )
    if "manifest" not in doc:
        raise IdentityError("the signed manifest carried no manifest")
    public = unb64(doc.get("public_key"))
    signature = unb64(doc.get("signature"))
    if public is None or signature is None:
        raise IdentityError("the signed manifest is malformed")
    actual = fingerprint_for(public)
    if expected_fingerprint and actual != expected_fingerprint:
        raise IdentityError(
            f"the manifest was signed by {actual}, not the "
            f"{expected_fingerprint} this device was prepared for"
        )
    manifest = doc["manifest"]
    if not verify(public, manifest_message(channel, manifest), signature):
        raise IdentityError("the manifest signature did not verify")
    return manifest


# ─── a discovered address, before anyone has agreed to it ─────────────────

def write_pending_server(url: str, fingerprint: str | None = None) -> bool:
    """Remember an address discovery turned up, WITHOUT making it
    configuration. It becomes configuration in config.toml only once the
    server says this device is paired — that is, once a person approved it
    on the dashboard."""
    doc: dict[str, Any] = {"domovoi_url": url}
    if fingerprint:
        doc["fingerprint"] = fingerprint
    try:
        PENDING_SERVER_SIDECAR.parent.mkdir(parents=True, exist_ok=True)
        PENDING_SERVER_SIDECAR.write_text(
            json.dumps(doc, indent=2) + "\n", encoding="utf-8"
        )
    except OSError as e:
        log.debug("could not save the pending server address: %s", e)
        return False
    return True


def read_pending_server() -> tuple[str | None, str | None]:
    doc = _read_json(PENDING_SERVER_SIDECAR)
    url = doc.get("domovoi_url")
    url = url.strip() if isinstance(url, str) and url.strip() else None
    return url, normalize_fingerprint(doc.get("fingerprint"))


def clear_pending_server() -> None:
    try:
        PENDING_SERVER_SIDECAR.unlink(missing_ok=True)
    except OSError:
        pass
