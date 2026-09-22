"""USB-adoption wire format — the contract between the Domovoi server and an
unprovisioned satellite presenting itself as a USB mass-storage volume.

Deliberately stdlib-only and importable from BOTH sides: the web backend's
adoption scanner (`web/backend/satellite_adoption.py`) and the device's
provisioning mode (`satellite/provisioning_mode.py`) share this single
source of truth for file names, schemas, and checksums.

The exchange (see docs/ARCHITECTURE.md and satellite/PROVISIONING.md):

  1. An unprovisioned device boots, builds a small FAT image labeled
     ``DOMOVOI-SET`` (FAT labels cap at 11 chars — the label is only a
     cheap pre-filter; the authoritative marker is a parseable
     ``device-info.json`` with ``domovoi_setup == 1`` at the volume root),
     and exposes it over the USB gadget as a flash drive.
  2. The server's scanner spots the volume, reads ``device-info.json``,
     and the dashboard shows an adopt card.
  3. Adopt writes ``provision.json`` back: Wi-Fi credentials, the core WS
     URL, the room id, and a pre-generated pairing token whose sha256 the
     core stored at adopt time (so the first WS connect matches as an
     already-paired device — no TOFU race, strict-mode compatible).
  4. The device validates (nonce echo + payload checksum + two stable
     reads — FAT write caching means a torn read is a WHEN, not an if),
     applies, wipes ``provision.json`` from the image, and reboots onto
     Wi-Fi. The raw token transits exactly once, on this volume.

``device-info.json`` (device → server), rebuilt with a FRESH nonce every
time the gadget (re)binds so a stale ``provision.json`` from a previous
session can never apply:

    {"domovoi_setup": 1, "nonce": "<16 hex>", "mac": "aa:bb:..",
     "board": "raspberry_pi_zero_2_w", "model": "Raspberry Pi Zero 2 W",
     "client_version": null, "sat_type": "voice",
     "status": "awaiting_provision", "step": null, "error": null,
     "profiles_supported": ["respeaker_2mic_hat", ...]}

``status`` is the unified device lifecycle, shared with the first-boot
bootstrap (which writes the same file on the boot partition):
``bootstrapping`` → ``awaiting_provision`` → (``wifi_failed`` on a bad
PSK, re-presented so the dashboard can show the error) → ``active``.

``provision.json`` (server → device):

    {"domovoi_provision": 1, "nonce": "<echo of device nonce>",
     "payload": {"room_id", "domovoi_url", "sat_type", "device_profile",
                 "pairing_token", "wifi": {"ssid", "psk", "country",
                 "hidden"}, "tz", "initial_volume"},
     "payload_sha256": "<sha256 of canonical payload JSON>"}
"""

from __future__ import annotations

import ipaddress
import re

import hashlib
import json
from typing import Any
from urllib.parse import urlsplit

SETUP_VERSION = 1
VOLUME_LABEL = "DOMOVOI-SET"          # FAT volume labels max out at 11 chars
DEVICE_INFO_NAME = "device-info.json"
PROVISION_NAME = "provision.json"

DEVICE_STATUSES = (
    "bootstrapping",
    "awaiting_provision",
    "wifi_failed",
    # The portal transport can't ask the core whether a room is free —
    # it isn't on the house network yet. A collision therefore surfaces
    # only after joining, and re-presents setup with this status.
    "room_taken",
    "active",
)

# room_id is identity everywhere (WS path, MPD room, intercom address,
# every intents_log row), so a customer-typed name is normalised before
# it ever becomes one — never trusted as typed.
# A blank server address means "discover it at first start". An explicit
# sentinel keeps validate_provision strict about non-empty strings instead
# of teaching it to accept blanks, and gives client.py something
# unambiguous to branch on.
AUTO_DISCOVER_URL = "auto"

ROOM_ID_MAX_LEN = 32
_ROOM_DISALLOWED = re.compile(r"[^a-z0-9-]")

# The core's WebSocket port, defaulted onto a server address typed
# without one.
CORE_PORT = 6370
SERVER_URL_MAX_LEN = 256
# Where a satellite may be pointed from the setup portal: the house LAN.
# RFC 1918, exactly - not loopback (the satellite would dial itself), not
# link-local, not the CGNAT range.
_LAN_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)
_HOSTNAME_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
SERVER_URL_HELP = (
    "The server address must start with ws:// and point at your own "
    "network, e.g. ws://192.168.1.20:6370 or ws://domovoi.local:6370."
)


# 802.11 allows a network name of 1-32 bytes. Beyond that, three characters
# are refused outright because each means something inside a wpa_supplicant
# network block, and a name is data, never syntax.
WIFI_SSID_MAX_BYTES = 32
_SSID_FORBIDDEN = frozenset('"{}')
WIFI_PSK_MIN_LEN = 8
WIFI_PSK_MAX_LEN = 63
_HEX = frozenset("0123456789abcdefABCDEF")


def validate_wifi_ssid(ssid: Any) -> str:
    """A network name this code can carry safely: 1-32 bytes of UTF-8 with
    no control characters and none of ``"``, ``{`` or ``}``. Returns the
    name unchanged; raises ProvisionInvalid with a message fit for a form
    (the name itself is never echoed)."""
    if not isinstance(ssid, str) or not ssid:
        raise ProvisionInvalid("Choose your Wi-Fi network.")
    if len(ssid.encode("utf-8")) > WIFI_SSID_MAX_BYTES:
        raise ProvisionInvalid("That network name is too long.")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F or ch in _SSID_FORBIDDEN for ch in ssid):
        raise ProvisionInvalid(
            "That network name has characters that cannot be used here."
        )
    return ssid


def validate_wifi_psk(psk: Any) -> str:
    """What WPA2-PSK accepts: 8 to 63 printable ASCII characters, or the
    64-hex-digit key itself. Never echoed."""
    if not isinstance(psk, str) or not psk:
        raise ProvisionInvalid("Enter your Wi-Fi password.")
    if len(psk) == 64 and all(ch in _HEX for ch in psk):
        return psk
    if not (WIFI_PSK_MIN_LEN <= len(psk) <= WIFI_PSK_MAX_LEN):
        raise ProvisionInvalid("Wi-Fi passwords are 8 to 63 characters long.")
    if any(ord(ch) < 0x20 or ord(ch) > 0x7E for ch in psk):
        raise ProvisionInvalid(
            "That Wi-Fi password has characters that cannot be used here."
        )
    return psk


def wpa_psk_hex(ssid: str, psk: str) -> str:
    """The 256-bit pairwise master key wpa_supplicant derives from a
    passphrase (PBKDF2-HMAC-SHA1, the SSID as salt, 4096 rounds): what
    ``psk=`` carries so the passphrase itself never sits in the file. A
    64-hex-digit passphrase IS the key."""
    if len(psk) == 64 and all(ch in _HEX for ch in psk):
        return psk.lower()
    return hashlib.pbkdf2_hmac(
        "sha1", psk.encode("utf-8"), ssid.encode("utf-8"), 4096, 32
    ).hex()


def wpa_supplicant_network_block(ssid: str, psk: str, *, hidden: bool = False) -> str:
    """The ``network={...}`` block the wpa_supplicant fallback appends to
    its configuration, built here rather than taken from ``wpa_passphrase``:
    ``ssid=`` as hex, so no byte of the name is ever read as syntax;
    ``psk=`` as the derived key, and no ``#psk="..."`` comment carrying the
    passphrase; ``scan_ssid=1`` for a hidden network. Validates both
    inputs first."""
    validate_wifi_ssid(ssid)
    validate_wifi_psk(psk)
    lines = [
        "network={",
        f"\tssid={ssid.encode('utf-8').hex()}",
        f"\tpsk={wpa_psk_hex(ssid, psk)}",
    ]
    if hidden:
        lines.append("\tscan_ssid=1")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _lan_host(host: str) -> bool:
    """An RFC 1918 IPv4 address, or a name under .local (mDNS never
    resolves off the link)."""
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        labels = host.split(".")
        if len(labels) < 2 or labels[-1] != "local":
            return False
        return all(_HOSTNAME_LABEL.match(label) for label in labels)
    return addr.version == 4 and any(addr in net for net in _LAN_NETWORKS)


def normalize_server_url(raw: str, *, lan_only: bool = False) -> str:
    """The server address a satellite may be pointed at, in the one shape
    the client dials: ``ws://host:port`` (or ``wss://``), no credentials,
    no path. A bare host gets ``ws://`` and the core port; blank or
    ``auto`` is the discovery sentinel. Raises ProvisionInvalid with a
    message fit for the portal form.

    ``lan_only`` (the setup portal) additionally requires the host to be an
    RFC 1918 address or a ``.local`` name: that field is typed by whoever
    joined the setup network, and the satellite hands its pairing token to
    whatever it dials.
    """
    value = (raw or "").strip()
    if not value or value.lower() == AUTO_DISCOVER_URL:
        return AUTO_DISCOVER_URL
    if len(value) > SERVER_URL_MAX_LEN:
        raise ProvisionInvalid("The server address is too long.")
    # urlsplit silently drops tabs and newlines; an address that needs
    # that treatment is not one anybody typed.
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        raise ProvisionInvalid(SERVER_URL_HELP)
    if "://" not in value:
        value = "ws://" + value
    parts = urlsplit(value)
    if parts.scheme not in ("ws", "wss"):
        raise ProvisionInvalid(SERVER_URL_HELP)
    if parts.username is not None or parts.password is not None:
        raise ProvisionInvalid(SERVER_URL_HELP)
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ProvisionInvalid(SERVER_URL_HELP)
    try:
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        raise ProvisionInvalid(SERVER_URL_HELP) from None
    if not host:
        raise ProvisionInvalid(SERVER_URL_HELP)
    if lan_only and not _lan_host(host):
        raise ProvisionInvalid(SERVER_URL_HELP)
    if port is None:
        port = CORE_PORT
    if not (1 <= port <= 65535):
        raise ProvisionInvalid(SERVER_URL_HELP)
    if ":" in host:
        host = f"[{host}]"
    return f"{parts.scheme}://{host}:{port}"


def slugify_room(value: str) -> str:
    """Normalise a room name to a room_id: lowercase, spaces to dashes,
    every other symbol dropped. Raises ProvisionInvalid when nothing
    usable survives or the result is too long — callers show the result
    back to the user before committing, so a silent mangling can't pass
    unnoticed.

    ``"Kids' Room!"`` -> ``"kids-room"``; ``"!!!"`` -> raises.
    """
    slug = (value or "").strip().lower()
    slug = re.sub(r"\s+", "-", slug)      # runs of whitespace -> ONE dash
    slug = _ROOM_DISALLOWED.sub("", slug)  # drop everything else
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    if not slug:
        raise ProvisionInvalid("room name has no usable characters")
    if len(slug) > ROOM_ID_MAX_LEN:
        raise ProvisionInvalid(
            f"room name too long ({len(slug)} > {ROOM_ID_MAX_LEN})"
        )
    return slug


class ProvisionInvalid(ValueError):
    """A provision/device-info document failed validation. The message is
    safe to log — it never contains credentials."""


def payload_checksum(payload: dict[str, Any]) -> str:
    """sha256 over the canonical (sorted-keys, no-whitespace) payload JSON.
    Both sides MUST build the digest this way — it's what lets the device
    reject a torn FAT write byte-for-byte."""
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_device_info(
    *,
    nonce: str,
    mac: str | None,
    board: str | None,
    model: str | None,
    sat_type: str = "voice",
    status: str = "awaiting_provision",
    step: str | None = None,
    error: str | None = None,
    client_version: str | None = None,
    profiles_supported: list[str] | None = None,
) -> dict[str, Any]:
    if status not in DEVICE_STATUSES:
        raise ValueError(f"unknown device status {status!r}")
    return {
        "domovoi_setup": SETUP_VERSION,
        "nonce": nonce,
        "mac": mac.lower() if mac else None,
        "board": board,
        "model": model,
        "client_version": client_version,
        "sat_type": sat_type,
        "status": status,
        "step": step,
        "error": error,
        "profiles_supported": list(profiles_supported or []),
    }


def validate_device_info(doc: Any) -> dict[str, Any]:
    """Parse-side validation of a device-info document (the server runs
    this on every candidate volume). Raises ProvisionInvalid with a short,
    credential-free reason."""
    if not isinstance(doc, dict):
        raise ProvisionInvalid("device-info is not a JSON object")
    if doc.get("domovoi_setup") != SETUP_VERSION:
        raise ProvisionInvalid(
            f"unsupported domovoi_setup version {doc.get('domovoi_setup')!r}"
        )
    nonce = doc.get("nonce")
    if not isinstance(nonce, str) or not (8 <= len(nonce) <= 64):
        raise ProvisionInvalid("missing or malformed nonce")
    status = doc.get("status") or "awaiting_provision"
    if status not in DEVICE_STATUSES:
        raise ProvisionInvalid(f"unknown status {status!r}")
    out = dict(doc)
    out["status"] = status
    mac = doc.get("mac")
    out["mac"] = mac.lower() if isinstance(mac, str) and mac else None
    st = doc.get("sat_type")
    out["sat_type"] = st if st in ("voice", "video") else "voice"
    profiles = doc.get("profiles_supported")
    out["profiles_supported"] = [
        str(p) for p in profiles if isinstance(p, str)
    ] if isinstance(profiles, list) else []
    return out


def build_provision(
    *,
    nonce: str,
    room_id: str,
    domovoi_url: str,
    sat_type: str,
    device_profile: str,
    pairing_token: str,
    wifi_ssid: str,
    wifi_psk: str,
    wifi_country: str | None = None,
    wifi_hidden: bool = False,
    tz: str | None = None,
    initial_volume: int | None = None,
) -> dict[str, Any]:
    """The full provision document, checksum included (server side)."""
    payload: dict[str, Any] = {
        "room_id": room_id,
        "domovoi_url": domovoi_url,
        "sat_type": sat_type,
        "device_profile": device_profile,
        "pairing_token": pairing_token,
        "wifi": {
            "ssid": wifi_ssid,
            "psk": wifi_psk,
            "country": wifi_country,
            "hidden": bool(wifi_hidden),
        },
        "tz": tz,
        "initial_volume": initial_volume,
    }
    return {
        "domovoi_provision": SETUP_VERSION,
        "nonce": nonce,
        "payload": payload,
        "payload_sha256": payload_checksum(payload),
    }


def validate_provision(doc: Any, expected_nonce: str) -> dict[str, Any]:
    """Device-side validation of a provision document: version, nonce echo
    (a stale file from a previous gadget session is inert), and the payload
    checksum (a torn FAT write is rejected byte-for-byte). Returns the
    validated PAYLOAD. Raises ProvisionInvalid — with no credentials in the
    message — on any mismatch."""
    if not isinstance(doc, dict):
        raise ProvisionInvalid("provision is not a JSON object")
    if doc.get("domovoi_provision") != SETUP_VERSION:
        raise ProvisionInvalid(
            f"unsupported domovoi_provision version {doc.get('domovoi_provision')!r}"
        )
    if doc.get("nonce") != expected_nonce:
        raise ProvisionInvalid("nonce mismatch (stale provision file)")
    payload = doc.get("payload")
    if not isinstance(payload, dict):
        raise ProvisionInvalid("missing payload")
    if doc.get("payload_sha256") != payload_checksum(payload):
        raise ProvisionInvalid("payload checksum mismatch (torn write?)")
    for key in ("room_id", "domovoi_url", "device_profile", "pairing_token"):
        v = payload.get(key)
        if not isinstance(v, str) or not v:
            raise ProvisionInvalid(f"payload missing {key}")
    # The address the client will dial: ws:// or wss://, a host, nothing
    # else. The portal has already applied the LAN-only rule to what it
    # accepts; here the shape is what matters.
    normalize_server_url(payload["domovoi_url"])
    wifi = payload.get("wifi")
    if not isinstance(wifi, dict) or not wifi.get("ssid") or not wifi.get("psk"):
        raise ProvisionInvalid("payload missing wifi credentials")
    # The name goes into a root-owned network configuration on the device
    # (as argv to nmcli, or hex into wpa_supplicant.conf); one that cannot
    # be carried safely is refused here, on every transport.
    try:
        validate_wifi_ssid(wifi["ssid"])
    except ProvisionInvalid:
        raise ProvisionInvalid("payload wifi ssid invalid") from None
    if payload.get("sat_type") not in ("voice", "video"):
        raise ProvisionInvalid("payload sat_type invalid")
    return payload
