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

import hashlib
import json
from typing import Any

SETUP_VERSION = 1
VOLUME_LABEL = "DOMOVOI-SET"          # FAT volume labels max out at 11 chars
DEVICE_INFO_NAME = "device-info.json"
PROVISION_NAME = "provision.json"

DEVICE_STATUSES = ("bootstrapping", "awaiting_provision", "wifi_failed", "active")


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
    wifi = payload.get("wifi")
    if not isinstance(wifi, dict) or not wifi.get("ssid") or not wifi.get("psk"):
        raise ProvisionInvalid("payload missing wifi credentials")
    if payload.get("sat_type") not in ("voice", "video"):
        raise ProvisionInvalid("payload sat_type invalid")
    return payload
