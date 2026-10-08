"""Know which server is ours, and refuse the rest.

A satellite used to decide what "the server" meant by asking the network
and believing the first answer. This module is the other half of
:mod:`domovoi.server_identity`: it holds the **fingerprint** of the core
that prepared this device's image, and the checks that turn that string
into a decision.

Three checks, all of them "verify if known":

* :func:`verify_server` — ask ``/v1/health`` to sign a nonce we just made
  up AND the address we dialed, and confirm the signature against the
  pinned fingerprint. Used by discovery before an address is ever written
  down, and again on every reconnect. Binding the address is what tells
  the core from a host that merely forwards our question to it: the core
  refuses to sign for an address that is not its own, and an answer
  signed for the core's own address is not the one we dialed.
* :func:`accept_manifest_envelope` — confirm a file list (code, plugin
  payloads, sounds, wake models) before a single byte of it is
  downloaded, and refuse one older than the last this device accepted.
* :func:`pinned_fingerprint` — what we are comparing against, and where it
  came from. The root-owned pin first boot installed wins over anything
  the satellite account can edit.

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
import urllib.parse
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
# The manifest signature that also covers the list's issue time and serial.
MANIFEST_CONTEXT_V2 = b"domovoi-manifest-v2"

CODE_CHANNEL = "satellite-code"
PLUGIN_CHANNEL = "satellite-plugins"
SOUNDS_CHANNEL = "satellite-sounds"
WAKE_MODELS_CHANNEL = "satellite-wake-models"

CONFIG_DIR = Path("~/.domovoi").expanduser()
# The last manifest this device accepted, per channel: ``{channel:
# {serial, issued_at, digest}}``. What makes "older than what I already
# have" a question with an answer. Owned by the satellite account, like
# the code tree it protects; the root helper keeps its own record for the
# payload channel.
FRESHNESS_SIDECAR = CONFIG_DIR / "manifest-serials.json"
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


def health_message(challenge: str, addr: str | None = None) -> bytes:
    """What the core signs for a health challenge. With ``addr`` — the
    ``host:port`` we dialed — the address is part of the message."""
    message = HEALTH_CONTEXT + b"\n" + challenge.encode("utf-8")
    if addr is not None:
        message += b"\n" + addr.encode("utf-8")
    return message


def manifest_message(channel: str, manifest: Any) -> bytes:
    return (
        MANIFEST_CONTEXT + b"\n" + channel.encode("utf-8") + b"\n"
        + canonical_json(manifest)
    )


def manifest_message_v2(
    channel: str, manifest: Any, issued_at: int, serial: int
) -> bytes:
    """The bytes under ``signature_v2``: one canonical document carrying the
    channel, the list, its issue time and its serial. Byte-identical to
    what the core and the root helper build."""
    signed = {
        "channel": channel,
        "issued_at": int(issued_at),
        "manifest": manifest,
        "serial": int(serial),
    }
    return MANIFEST_CONTEXT_V2 + b"\n" + canonical_json(signed)


def manifest_digest(manifest: Any) -> str:
    return hashlib.sha256(canonical_json(manifest)).hexdigest()


def new_challenge() -> str:
    return secrets.token_hex(_CHALLENGE_BYTES)


def dialed_address(http_base: str) -> str:
    """The ``host:port`` a URL dials, spelled the way we ask the core to
    sign it. The port is always present (the scheme's default when the URL
    has none) so the string is unambiguous; an IPv6 literal keeps its
    brackets."""
    text = http_base.strip()
    if "://" not in text:
        text = "http://" + text
    parts = urllib.parse.urlsplit(text)
    host = parts.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        port = parts.port
    except ValueError:
        port = None
    if port is None:
        port = 443 if parts.scheme in ("https", "wss") else 80
    return f"{host}:{port}"


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
    from: ``"image"`` (the root-owned copy first boot installed),
    ``"config"`` (written into config.toml at adoption), ``"recorded"``
    (learned on first contact) or ``"none"``.

    The root-owned pin wins. config.toml belongs to the satellite account,
    and every trust decision on the device — which core to connect to,
    whose code to install, whose plugin ``post_install`` root runs — takes
    this value; a pin that account could rewrite would let a shell as that
    account choose what root runs, by choosing the server. The root copy is
    what first boot installed from the card and nothing on the device can
    edit, so when it exists it is the answer, and a config value that
    disagrees with it is said out loud as the tampering or the mistake it
    is."""
    from_config = normalize_fingerprint(configured)
    from_image = normalize_fingerprint(_read_json(ROOT_PIN).get("fingerprint"))
    if from_image:
        if from_config and from_config != from_image:
            log.error(
                "config.toml names server %s but this device was prepared "
                "for %s (%s, root-owned); the root-owned pin is the one this "
                "device trusts. Something edited config.toml — check who.",
                from_config, from_image, ROOT_PIN,
            )
        return from_image, "image"
    if from_config:
        return from_config, "config"
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
    doc: Any, *, challenge: str, expected_fingerprint: str | None,
    addr: str | None = None,
) -> str:
    """The fingerprint the answer proved, or raise :class:`IdentityError`.

    "Proved" means: the document carries a public key and a signature over
    the nonce WE chose in this call, the signature checks out against that
    key, and — when a fingerprint is pinned — the key hashes to it. Without
    a pin this still rules out a host that cannot sign at all, and gives
    the caller a fingerprint worth recording.

    ``addr`` is the ``host:port`` we dialed. When this device is pinned the
    answer must name that same address and the signature must cover it:
    an answer that names no address came from a core running older code
    (or from something relaying one, after stripping our question), and an
    answer naming a different address was signed for somebody else's
    connection. Either way it is not proof that THIS host is the server.
    Unpinned, an unbound answer is still accepted and recorded — a device
    with nothing to compare against cannot tell a relay from a core by any
    means, and refusing would only strand it against an older core."""
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
        if addr is not None and identity.get("addr_refused") == addr:
            raise IdentityError(
                f"the server would not sign for {addr}: the signed address "
                "is not the one dialed (that address is not the server's "
                "own, so something is relaying its answers — or the server "
                "needs it listed in TRUSTED_HOSTS)"
            )
        raise IdentityError("the server's identity is malformed")
    if identity.get("challenge") != challenge:
        raise IdentityError("the server answered a different challenge")
    signed_addr = identity.get("addr")
    bound = addr is not None and (expected_fingerprint or signed_addr is not None)
    if bound:
        if signed_addr is None:
            raise IdentityError(
                f"the signed address is missing: the proof is not bound to "
                f"the address dialed ({addr}) — an older Domovoi server, or "
                "something relaying one"
            )
        if signed_addr != addr:
            raise IdentityError(
                f"the signed address {signed_addr} is not the one dialed "
                f"({addr}): this answer was signed for a different connection"
            )
        message = health_message(challenge, addr)
    else:
        message = health_message(challenge)
    if not verify(public, message, signature):
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
    opener=None, addr: str | None = None,
) -> Any:
    """GET ``/v1/health?challenge=…[&addr=…]``. Both ride in the query so a
    core that predates them simply ignores what it does not know and
    answers as it always did."""
    opener = opener or urllib.request.urlopen
    url = f"{http_base.rstrip('/')}/v1/health?challenge={challenge}"
    if addr is not None:
        url += "&addr=" + urllib.parse.quote(addr, safe="")
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

    The proof is bound to the address in ``http_base`` — the one we are
    about to connect to — so a host that relays the question to the real
    core gets an answer the core would not sign for it.

    With nothing pinned the host still has to hold a key and sign with it;
    the fingerprint comes back so the caller can record it."""
    challenge = new_challenge()
    addr = dialed_address(http_base)
    try:
        doc = fetch_health(
            http_base, challenge=challenge, timeout=timeout, opener=opener,
            addr=addr,
        )
    except IdentityError:
        raise
    except Exception as e:      # noqa: BLE001 — every transport failure is "not provable"
        raise IdentityError(f"could not reach {http_base}: {e}") from e
    return verify_health_document(
        doc, challenge=challenge, expected_fingerprint=expected_fingerprint,
        addr=addr,
    )


def _freshness_fields(doc: dict[str, Any]) -> tuple[int, int]:
    """``(issued_at, serial)`` out of an envelope, or raise. Integers only
    — a float or a bool here would canonicalize differently from what was
    signed, and a negative serial is nobody's."""
    issued_at, serial = doc.get("issued_at"), doc.get("serial")
    ok = (
        isinstance(issued_at, int) and not isinstance(issued_at, bool)
        and isinstance(serial, int) and not isinstance(serial, bool)
        and issued_at >= 0 and serial >= 0
    )
    if not ok:
        raise IdentityError(
            "the signed manifest carries no issue time or serial: the server "
            "signed it the old way; upgrade the Domovoi server first"
        )
    return issued_at, serial


def verify_manifest_envelope(
    doc: Any, *, channel: str, expected_fingerprint: str | None
) -> Any:
    """The manifest inside a signed ``manifest.sig`` envelope, or raise.

    The envelope carries the manifest it signed, so what is verified and
    what is used are the same object — there is no window in which the file
    list could change between the signature and the download.

    What is checked is ``signature_v2``: the signature over the list AND
    its ``issued_at`` and ``serial``. An envelope with only the original
    ``signature`` is refused — the core serves both, and a core that serves
    only the old one predates this code. Whether the serial is NEW ENOUGH
    is :func:`check_manifest_freshness`'s question; this function answers
    only "did our server sign exactly this".
    """
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
    if public is None:
        raise IdentityError("the signed manifest is malformed")
    actual = fingerprint_for(public)
    if expected_fingerprint and actual != expected_fingerprint:
        raise IdentityError(
            f"the manifest was signed by {actual}, not the "
            f"{expected_fingerprint} this device was prepared for"
        )
    issued_at, serial = _freshness_fields(doc)
    signature = unb64(doc.get("signature_v2"))
    if signature is None:
        raise IdentityError(
            "the signed manifest carries no signature over its serial; "
            "upgrade the Domovoi server first"
        )
    manifest = doc["manifest"]
    if not verify(public, manifest_message_v2(channel, manifest, issued_at, serial),
                  signature):
        raise IdentityError("the manifest signature did not verify")
    return manifest


# ─── older than what we already have? ─────────────────────────────────────

def last_accepted_manifest(channel: str, path: Path | None = None) -> dict[str, Any] | None:
    """What this device last accepted on ``channel``: ``{serial, issued_at,
    digest}``, or None when it never has."""
    path = FRESHNESS_SIDECAR if path is None else path
    entry = _read_json(path).get(channel)
    if not isinstance(entry, dict):
        return None
    serial = entry.get("serial")
    if not isinstance(serial, int) or isinstance(serial, bool):
        return None
    return entry


def check_manifest_freshness(
    doc: dict[str, Any], *, channel: str, path: Path | None = None,
    floor: str | None = None,
) -> None:
    """Raise :class:`IdentityError` when ``doc`` is older than the last
    envelope this device accepted on ``channel``.

    Older means a smaller serial. The same serial is fine when it is the
    same list — that is every connect on which nothing changed — and
    refused when it is a different list, because our server never issues
    two lists under one serial. A newer serial is always taken, whatever
    its list, so rolling the core's satellite tree back to an earlier
    commit still reaches the device: that is a new publication, not a
    replay.

    ``channel`` here is the RECORD the serial is kept under. ``floor`` is an
    older record to judge against while this one has never been written:
    the sounds channel moved from one record per channel to one per voice,
    and a device's first list under a new record must still be newer than
    the last it took under the old one."""
    issued_at, serial = _freshness_fields(doc)
    del issued_at
    last = last_accepted_manifest(channel, path)
    if last is None and floor is not None:
        last = last_accepted_manifest(floor, path)
    if last is None:
        return
    if serial < last["serial"]:
        raise IdentityError(
            f"the signed manifest (serial {serial}) is older than the one "
            f"this device already accepted (serial {last['serial']}); "
            "a recording of an earlier list is being served"
        )
    if serial == last["serial"] and last.get("digest") not in (None, manifest_digest(doc["manifest"])):
        raise IdentityError(
            f"the signed manifest carries serial {serial}, which this device "
            "already accepted for a different list"
        )


def remember_manifest(
    doc: dict[str, Any], *, channel: str, path: Path | None = None
) -> bool:
    """Record ``doc`` as the last accepted on ``channel``. Best-effort: a
    read-only config dir costs the record, not the sync."""
    path = FRESHNESS_SIDECAR if path is None else path
    issued_at, serial = _freshness_fields(doc)
    state = _read_json(path)
    state[channel] = {
        "serial": serial,
        "issued_at": issued_at,
        "digest": manifest_digest(doc["manifest"]),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        log.debug("could not record the accepted manifest serial: %s", e)
        return False
    return True


def accept_manifest_envelope(
    doc: Any, *, channel: str, expected_fingerprint: str | None,
    path: Path | None = None, record: str | None = None,
) -> Any:
    """Verify, check freshness, remember — the one call a sync channel
    makes. Returns the manifest; raises :class:`IdentityError` when the
    envelope is not our server's or is older than the last accepted.

    ``record`` keeps the serial under its own name when one channel serves
    several lists side by side — the sounds channel, one list per voice
    (:func:`sounds_record`). The server keys its serials the same way, so a
    device that switches voice and back judges each voice's list against
    that voice's last one, not against another voice's newer serial. Until
    a record has been written, the channel's own record is the floor."""
    manifest = verify_manifest_envelope(
        doc, channel=channel, expected_fingerprint=expected_fingerprint
    )
    key = record or channel
    floor = channel if key != channel else None
    check_manifest_freshness(doc, channel=key, path=path, floor=floor)
    remember_manifest(doc, channel=key, path=path)
    return manifest


def sounds_record(voice: str | None) -> str:
    """Where this device keeps the sounds serial for the voice it asks for
    (``None`` = the server's default voice)."""
    return f"{SOUNDS_CHANNEL}@{voice or ''}"


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
