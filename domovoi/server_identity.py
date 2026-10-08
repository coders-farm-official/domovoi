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
  a recording of an earlier answer proves nothing. A caller that also says
  which address it dialed (``?addr=host:port``) gets a signature over the
  nonce AND that address — and only if the address is one of this box's
  own, so a host that merely relays the question to the real core cannot
  pass the answer off as its own. The bare-nonce form is still answered,
  for satellites running code from before the binding existed.
* the **file-channel manifests** — code, plugin payloads, sounds and wake
  models. Each channel offers a ``manifest.sig`` endpoint whose body is a
  signed envelope carrying the manifest it signed, so a satellite can check
  the authenticity of the file list before it downloads a single byte. The
  envelope carries two signatures: the original one over the channel and
  the list (``signature``, what satellites in the field verify today) and
  one over the list PLUS its ``issued_at`` and a per-channel monotonic
  ``serial`` (``signature_v2``), which is what lets a device refuse an
  older, genuinely signed list served to it again. The plain ``manifest``
  endpoint keeps serving the old unsigned shape for devices that predate
  all of this.

Backend: ``cryptography`` when it is installed, else the vendored pure
implementation in :mod:`domovoi._ed25519`. Same keys, same signatures.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import logging
import os
import re
import secrets
import socket
import threading
import time
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
# The freshness-carrying manifest signature. A different separator from the
# v1 one, so neither signature can stand in for the other.
MANIFEST_CONTEXT_V2 = b"domovoi-manifest-v2"

CODE_CHANNEL = "satellite-code"
PLUGIN_CHANNEL = "satellite-plugins"
SOUNDS_CHANNEL = "satellite-sounds"
WAKE_MODELS_CHANNEL = "satellite-wake-models"

_CHALLENGE_BYTES = 16
CHALLENGE_MAX_LEN = 128
# The address a satellite says it dialed: ``host:port``, an IPv6 literal in
# brackets. Longer than any name plus a port can be.
ADDR_MAX_LEN = 300

# What a challenge and a dialed address may be made of. LOAD-BEARING, not
# tidiness: the bound health message is ``<context>\n<nonce>\n<addr>`` and
# the unbound one is ``<context>\n<nonce>``. If a nonce could carry a
# newline, a caller could ask for an UNBOUND signature over ``C\nA`` and
# present it as a BOUND answer for nonce ``C`` and address ``A`` — which is
# exactly the relay the binding exists to stop. Neither field may contain
# whitespace or anything outside a URL-safe alphabet. Genuine satellites
# send hex nonces and ``host:port``; anything else is refused unsigned.
_CHALLENGE_RE = re.compile(rf"^[A-Za-z0-9._~-]{{1,{CHALLENGE_MAX_LEN}}}$")
_ADDR_RE = re.compile(rf"^[A-Za-z0-9.:\[\]_~%-]{{1,{ADDR_MAX_LEN}}}$")

# Where the per-channel manifest serials live: beside the key, so a test
# that points the key at a tmp dir gets its serials there too.
MANIFEST_SERIALS_NAME = "manifest-serials.json"


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


def health_message(challenge: str, addr: str | None = None) -> bytes:
    """The bytes signed in a ``/v1/health`` challenge answer. The satellite
    builds this string too; they must agree byte for byte.

    With ``addr`` — the ``host:port`` the caller dialed — the address is
    bound into the message, so the signature proves not only "the key
    holder saw this nonce" but "the key holder agreed it is reachable at
    that address". See :data:`_CHALLENGE_RE` for why neither part may
    carry a newline."""
    message = HEALTH_CONTEXT + b"\n" + challenge.encode("utf-8")
    if addr is not None:
        message += b"\n" + addr.encode("utf-8")
    return message


def manifest_message(channel: str, manifest: Any) -> bytes:
    """The bytes signed for a file-channel manifest (the v1 signature)."""
    return (
        MANIFEST_CONTEXT + b"\n" + channel.encode("utf-8") + b"\n"
        + canonical_json(manifest)
    )


def manifest_message_v2(
    channel: str, manifest: Any, issued_at: int, serial: int
) -> bytes:
    """The bytes signed for a file-channel manifest WITH its freshness.

    One canonical document carries the channel, the list, when it was
    issued and its serial, so none of the four can be swapped under the
    signature. The satellite tree and the root helper on a satellite build
    the identical bytes."""
    signed = {
        "channel": channel,
        "issued_at": int(issued_at),
        "manifest": manifest,
        "serial": int(serial),
    }
    return MANIFEST_CONTEXT_V2 + b"\n" + canonical_json(signed)


def manifest_digest(manifest: Any) -> str:
    """sha256 of the canonical manifest — the key the serial store is kept
    by, so an unchanged list keeps its serial."""
    return hashlib.sha256(canonical_json(manifest)).hexdigest()


def new_challenge() -> str:
    return secrets.token_hex(_CHALLENGE_BYTES)


def valid_challenge(challenge: object) -> bool:
    """Whether a challenge may be signed at all: non-empty, bounded, and
    made only of URL-safe characters (no whitespace, no newline)."""
    return isinstance(challenge, str) and bool(_CHALLENGE_RE.match(challenge))


def valid_addr(addr: object) -> bool:
    """Whether a dialed-address string may be bound into a signature."""
    return isinstance(addr, str) and bool(_ADDR_RE.match(addr))


# ─── is a dialed address one of ours? ─────────────────────────────────────

def split_host_port(addr: str) -> tuple[str, int | None]:
    """``host:port`` / ``[v6]:port`` / bare host → (host lower-cased, port).
    An IPv6 literal comes back without its brackets."""
    text = addr.strip().lower()
    port: int | None = None
    if text.startswith("["):
        end = text.find("]")
        if end == -1:
            return "", None
        host, rest = text[1:end], text[end + 1:]
        if rest.startswith(":") and rest[1:].isdigit():
            port = int(rest[1:])
        elif rest:
            return "", None
        return host, port
    if text.count(":") == 1:
        host, _, tail = text.partition(":")
        if tail.isdigit():
            return host, int(tail)
        return "", None
    return text, None      # a bare name, or an unbracketed IPv6 literal


def _can_bind(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Whether this host owns ``ip``: a datagram socket can only be bound to
    an address of one of its own interfaces. Nothing is sent. This covers
    every interface — Wi-Fi, Ethernet, a Docker bridge — without having to
    enumerate them, and it is a kernel answer rather than a resolver's."""
    family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
    try:
        s = socket.socket(family, socket.SOCK_DGRAM)
    except OSError:
        return False
    try:
        s.bind((str(ip), 0))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _configured_host_entries() -> list[tuple[str, int | None]]:
    """``TRUSTED_HOSTS`` as (host, port) pairs. Resolved at call time so a
    test can set the setting; imported lazily so this module stays
    importable without the settings machinery."""
    try:
        from domovoi.transport_guard import configured_hosts
    except Exception:      # noqa: BLE001 — no settings here: nothing configured
        return []
    out: list[tuple[str, int | None]] = []
    for entry in configured_hosts():
        host, port = split_host_port(entry)
        if host:
            out.append((host, port))
    return out


def dialed_address_is_ours(addr: str, *, can_bind=None) -> bool:
    """Whether ``addr`` — the ``host:port`` a satellite says it dialed — is
    an address this server is actually reachable at, and so one it may
    sign for.

    Yes when the host is a loopback or an IP literal of one of this box's
    own interfaces, or a name (or address, with or without a port) the
    operator listed in ``TRUSTED_HOSTS``. **A name is never resolved**: on
    the LAN a name can be answered by anyone (mDNS, LLMNR, a router's
    DNS), and resolving it here would hand the decision back to whoever
    answers — which is the relay this check exists to stop. A household
    whose satellites dial the core by a name, or through a NAT or
    port-forward whose outside address the core does not own, lists that
    address in ``TRUSTED_HOSTS``; the refusal is logged with that advice.
    """
    can_bind = _can_bind if can_bind is None else can_bind
    host, port = split_host_port(addr)
    if not host:
        return False
    for ehost, eport in _configured_host_entries():
        if eport is not None and eport != port:
            continue
        if ehost.startswith("*."):
            if host == ehost[2:] or host.endswith(ehost[1:]):
                return True
        elif host == ehost:
            return True
    if host in ("localhost", "localhost.localdomain"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False      # a name: only TRUSTED_HOSTS can vouch for it
    if ip.is_loopback:
        return True
    if ip.is_unspecified or ip.is_multicast:
        return False
    return can_bind(ip)


# ─── the identity itself ──────────────────────────────────────────────────

@dataclass(frozen=True)
class ServerIdentity:
    seed: bytes
    public: bytes
    # Where the key was read from; the manifest serial store sits beside
    # it. None means "the install's own" (:func:`identity_path`).
    path: Path | None = None

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

    def health_answer(self, challenge: str, addr: str | None = None) -> dict[str, str]:
        """The identity block for ``/v1/health?challenge=…``.

        With ``addr`` the answer carries it back and the signature covers
        it: the caller checks that the address signed is the one it dialed.
        The caller (``main.health``) has already decided the address is
        one of ours — this method only signs what it is handed."""
        doc = self.public_document()
        doc["challenge"] = challenge
        if addr is not None:
            doc["addr"] = addr
        doc["signature"] = b64(self.sign(health_message(challenge, addr)))
        return doc

    def serials_path(self) -> Path:
        base = self.path if self.path is not None else identity_path()
        return base.parent / MANIFEST_SERIALS_NAME

    def signed_manifest(
        self, channel: str, manifest: Any, *, now: float | None = None
    ) -> dict[str, Any]:
        """The ``manifest.sig`` envelope: the manifest, and signatures over
        it. Carrying the manifest inside the envelope is deliberate — a
        satellite that fetched the list and the signature as two requests
        could be handed a list that changed in between, and would then
        refuse a perfectly good upgrade.

        Two signatures, for the transition:

        * ``signature`` — over the channel and the list, exactly as before.
          Satellites running earlier code verify this one; it is what lets
          them take the code that teaches them the next one.
        * ``signature_v2`` — over the channel, the list, ``issued_at`` and
          a ``serial`` that only ever grows per channel. A satellite on
          current code verifies this one and remembers the serial, so a
          genuine envelope recorded earlier and served again is refused as
          older rather than installed as new.
        """
        issued_at, serial = _freshness_for(self.serials_path(), channel, manifest, now=now)
        doc: dict[str, Any] = self.public_document()
        doc["channel"] = channel
        doc["manifest"] = manifest
        doc["issued_at"] = issued_at
        doc["serial"] = serial
        doc["signature"] = b64(self.sign(manifest_message(channel, manifest)))
        doc["signature_v2"] = b64(
            self.sign(manifest_message_v2(channel, manifest, issued_at, serial))
        )
        return doc


# ─── manifest serials ─────────────────────────────────────────────────────
#
# One file beside the key: ``{channel: {serial, issued_at, digest}}``. The
# serial is minted when a channel's list CHANGES (its digest differs from
# the one recorded) and reused while it does not, so an unchanged list keeps
# a stable envelope. A new serial is ``max(previous + 1, now)``: strictly
# greater than anything issued before, and — should this file ever be lost
# — still greater than any serial a satellite remembers, because every
# earlier serial was at most the time it was minted. ``issued_at`` is when
# the serial was minted. Satellites do not judge it against their own clock
# (a Pi has none worth trusting before its first time sync); the serial is
# the anti-replay mechanism, the time is for people reading the envelope.

_SERIALS_LOCK = threading.Lock()
_SERIALS_WARNED: set[str] = set()


def _read_serials(path: Path) -> dict[str, Any]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _freshness_for(
    path: Path, channel: str, manifest: Any, *, now: float | None = None
) -> tuple[int, int]:
    """``(issued_at, serial)`` for this channel's current list."""
    digest = manifest_digest(manifest)
    stamp = int(time.time() if now is None else now)
    with _SERIALS_LOCK:
        state = _read_serials(path)
        entry = state.get(channel)
        entry = entry if isinstance(entry, dict) else {}
        previous = entry.get("serial")
        previous = previous if isinstance(previous, int) and not isinstance(previous, bool) else 0
        if entry.get("digest") == digest and previous > 0:
            issued = entry.get("issued_at")
            issued = issued if isinstance(issued, int) and not isinstance(issued, bool) else stamp
            return issued, previous
        serial = max(previous + 1, stamp)
        state[channel] = {"serial": serial, "issued_at": stamp, "digest": digest}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, path)
        except OSError as e:
            # Still a valid, monotonic answer for this request; the next one
            # mints again from the clock. Said once per path, not per call.
            if str(path) not in _SERIALS_WARNED:
                _SERIALS_WARNED.add(str(path))
                log.warning(
                    "could not record the manifest serial at %s (%s); serials "
                    "will follow the clock until it is writable", path, e,
                )
    return stamp, serial


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
    return ServerIdentity(seed=seed, public=public, path=path)


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
        identity = ServerIdentity(seed=seed, public=public_key_for(seed), path=target)
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


# ─── rotation ─────────────────────────────────────────────────────────────
#
# There is no in-band successor path yet: a satellite holds exactly one
# fingerprint, baked root-owned into its card, and nothing the core can say
# over the wire changes it. So rotating the key today means every card is
# re-prepared and re-flashed (or, for a hand-built unit, its pin files
# replaced by hand). This is the honest, explicit version of that — it
# retires the old key file rather than deleting it, so a later release that
# teaches satellites to accept a successor statement signed by the OLD key
# has something to sign with. docs/SECURITY_PRIVACY.md § Server identity
# says when to do this (the key file was copied off the box) and what it
# costs.

RETIRED_SUFFIX = ".retired-"


def rotate_identity(
    path: Path | None = None, *, confirm: bool = False, now: float | None = None
) -> dict[str, Any]:
    """Retire the current key and mint a new one — only with ``confirm``.

    Returns what happened: ``{rotated, old_fingerprint, new_fingerprint,
    retired_path, consequences}``. Without ``confirm`` nothing is written
    and ``rotated`` is False; the consequences are what a person must
    accept before saying yes."""
    target = identity_path() if path is None else path
    reset_cache()
    current = load_or_create(target)
    consequences = [
        "every satellite card prepared from this install trusts the OLD "
        "fingerprint and will refuse this server until it is re-prepared "
        "from the dashboard and re-flashed (a hand-built unit: replace "
        "/etc/domovoi/server-identity.json and ~/.domovoi/server-fingerprint.json "
        "by hand)",
        "the running core keeps the old key in memory until it is restarted",
        "the old key file is kept beside the new one, mode 0600, so a later "
        "release can publish a successor statement signed by it; delete it "
        "once every card has been re-prepared if the reason for rotating was "
        "that it had been copied",
    ]
    result: dict[str, Any] = {
        "rotated": False,
        "old_fingerprint": current.fingerprint,
        "new_fingerprint": None,
        "retired_path": None,
        "consequences": consequences,
    }
    if not confirm:
        return result
    stamp = time.strftime(
        "%Y%m%dT%H%M%SZ", time.gmtime(time.time() if now is None else now)
    )
    retired = target.with_name(target.name + RETIRED_SUFFIX + stamp)
    os.replace(target, retired)
    try:
        os.chmod(retired, 0o600)
    except OSError:      # pragma: no cover — Windows dev boxes
        pass
    reset_cache()
    fresh = load_or_create(target)
    log.warning(
        "rotated this server's identity: %s retired to %s, now %s — every "
        "prepared card must be re-prepared",
        current.fingerprint, retired, fresh.fingerprint,
    )
    result.update(
        rotated=True, new_fingerprint=fresh.fingerprint, retired_path=str(retired)
    )
    return result


def main(argv: list[str] | None = None) -> int:
    """``python -m domovoi.server_identity --rotate-identity
    [--confirm-reprovision]`` — see :func:`rotate_identity`."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="python -m domovoi.server_identity",
        description="Show or rotate this install's server identity.",
    )
    parser.add_argument(
        "--rotate-identity", action="store_true",
        help="retire the current Ed25519 key and mint a new one",
    )
    parser.add_argument(
        "--confirm-reprovision", action="store_true",
        help="actually rotate: you accept that every prepared satellite card "
             "must be re-prepared and re-flashed",
    )
    parser.add_argument(
        "--path", type=Path, default=None,
        help="the key file (default: the install's ~/.domovoi/server-identity.json)",
    )
    args = parser.parse_args(argv)
    if not args.rotate_identity:
        identity = load_or_create(args.path)
        print(f"fingerprint {identity.fingerprint}")
        print(f"key file    {args.path or identity_path()}")
        return 0
    result = rotate_identity(args.path, confirm=args.confirm_reprovision)
    out = sys.stdout
    if not result["rotated"]:
        print(f"current fingerprint: {result['old_fingerprint']}", file=out)
        print("Rotating the server identity means:", file=out)
        for line in result["consequences"]:
            print(f"  - {line}", file=out)
        print(
            "Nothing was changed. Re-run with --confirm-reprovision to rotate.",
            file=out,
        )
        return 2
    print(f"retired  {result['old_fingerprint']} -> {result['retired_path']}", file=out)
    print(f"new      {result['new_fingerprint']}", file=out)
    print(
        "Restart the core, then re-prepare and re-flash every satellite card.",
        file=out,
    )
    return 0


if __name__ == "__main__":      # pragma: no cover — exercised through main()
    raise SystemExit(main())
