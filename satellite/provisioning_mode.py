"""USB-adoption provisioning mode — the device side.

An UNPROVISIONED satellite (no ``~/.domovoi/config.toml`` yet) runs this
instead of the client: it builds a small FAT image carrying
``device-info.json``, exposes it over the USB gadget as a flash drive
(``DOMOVOI-SET``), and polls for the ``provision.json`` the Domovoi
server's adopt flow writes back. On a valid provision it writes the real
config + pairing token, joins Wi-Fi, wipes the provision file, and
reboots — the normal client then connects and everything server-side
(MPD, pairing case-2 match) is already in place.

Run as ``python -m satellite.provisioning_mode`` from
``domovoi-provisioning.service`` (Before=domovoi-satellite.service). On a
provisioned boot it does one thing and exits 0: checks the network
settings in satellite/net_settings.py (no setup hotspot profile, Wi-Fi
power saving off, the room's host name) and applies whichever is missing,
saying what it changed. Root required: configfs, mkfs, nmcli, hostnamectl,
timedatectl, reboot.

Deliberately imports ONLY stdlib + ``provisioning_protocol`` + ``wifi_reasons`` +
``config_writer`` — it must run on a bare image with none of the audio
stack installed. ``mtools``/``dosfstools`` are apt prerequisites of the
unprovisioned image (the media-prep pipeline installs them).

Key hardware/durability rules:

* The backing image is NEVER loop-mounted while the gadget is bound
  (host + device mounting one FAT = corruption). All device-side reads
  go through mtools (``mtype``) against the raw image file; every
  device-initiated content change is unbind → rebuild → rebind so the
  host sees a clean re-plug.
* A provision applies only after nonce echo + payload checksum + two
  identical reads ``poll_sec`` apart (FAT write caching makes torn reads
  a WHEN, not an if — see provisioning_protocol).
* Wi-Fi failure doesn't dead-end: after ``wifi_attempts`` failed joins
  the volume is re-presented with ``status: wifi_failed`` + the error
  text and a FRESH nonce, so the dashboard shows exactly what happened
  and a re-adopt (force) retries with corrected credentials.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Protocol

from satellite import boot_write
from satellite import config_writer
from satellite import provisioning_protocol as proto
from satellite import join_diag
from satellite import wifi_reasons

log = logging.getLogger("provisioning")

CONFIG_DIR = Path("~/.domovoi").expanduser()
CONFIG_PATH = CONFIG_DIR / "config.toml"
PAIRING_TOKEN_PATH = CONFIG_DIR / "pairing_token"
# The approval gate's record — `satellite.client.APPROVED_MARKER`, with the
# same two words in its first line. Named here rather than imported because
# this module is deliberately stdlib-only: it runs before the client's
# dependencies (sounddevice, webrtcvad) are guaranteed to import at all.
APPROVED_MARKER_PATH = CONFIG_DIR / "approved"
APPROVAL_NOT_APPROVED = "not-approved"
STATE_FILE = CONFIG_DIR / "provisioning_state.json"
IMAGE_FILE = CONFIG_DIR / "setup_gadget.img"

IMAGE_BYTES = 16 * 1024 * 1024

# run()'s exit code when setup can never be exposed on this card (the setup
# network's packages are missing). domovoi-provisioning.service names it in
# RestartPreventExitStatus=, so the unit stays down — red ring, reason on
# the card — instead of restarting into the same failure every few seconds.
EXIT_CANNOT_SET_UP = 3
# A setup network that failed for any other reason is retried in-process,
# 15 s, 30 s, 60 s ... at most every 5 minutes.
_EXPOSE_RETRY_SEC = 15.0
_EXPOSE_RETRY_MAX_SEC = 300.0
GADGET_DIR = Path("/sys/kernel/config/usb_gadget/domovoi")
UDC_DIR = Path("/sys/class/udc")

EXAMPLE_CONFIG = Path(__file__).resolve().parent / "config.toml.example"
# Where the wpa_supplicant fallback appends its network block. Root-owned;
# this module runs as root.
WPA_SUPPLICANT_CONF = Path("/etc/wpa_supplicant/wpa_supplicant.conf")


# ─── Small host probes (overridable in tests) ─────────────────────────────


def read_board() -> tuple[str | None, str | None]:
    """(board_slug, model_string) from the device tree. Works on Pi and
    RK3566 boards alike."""
    try:
        model = (
            Path("/proc/device-tree/model")
            .read_bytes()
            .split(b"\x00", 1)[0]
            .decode("utf-8", "replace")
            .strip()
        )
    except OSError:
        return None, None
    slug = model.lower().replace(" ", "_").replace("-", "_")
    return slug or None, model or None


def read_wlan_mac() -> str | None:
    try:
        return (
            Path("/sys/class/net/wlan0/address").read_text().strip().lower()
            or None
        )
    except OSError:
        return None


def image_sat_type() -> str:
    """What kind of satellite this image was built as. The media-prep
    pipeline stamps ``~/.domovoi/image_sat_type`` on video builds; absent
    = voice."""
    try:
        v = (CONFIG_DIR / "image_sat_type").read_text().strip()
        return v if v in ("voice", "video") else "voice"
    except OSError:
        return "voice"


BOOT_DIRS = (Path("/boot/firmware"), Path("/boot"))


def image_device_profile() -> str:
    """The mic board this image was built for, stamped by stage 1. The
    portal only asks the customer when the image didn't decide."""
    try:
        v = (CONFIG_DIR / "image_device_profile").read_text().strip()
        return v or "respeaker_2mic_hat"
    except OSError:
        return "respeaker_2mic_hat"


def _read_ap_json(path: Path) -> dict[str, str] | None:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(doc, dict) and doc.get("ssid") and doc.get("psk"):
        creds = {"ssid": str(doc["ssid"]), "psk": str(doc["psk"])}
        # Optional radio settings (portal_transport.RADIO_OVERRIDES), which
        # validates them: the boot partition is the one file a household
        # can edit, so a setup network can be re-tuned there without
        # rebuilding the card.
        for key in ("channel", "pmf", "join_bssid", "join_band"):
            if doc.get(key) is not None:
                creds[key] = str(doc[key])
        return creds
    log.warning("%s present but unusable — ignoring", path)
    return None


def ap_credential_paths() -> list[Path]:
    """Where to look for setup-AP credentials, most authoritative first.

    The BOOT partition is checked before the home copy, and that order is
    load-bearing for mass production. ``firstrun.sh`` copies
    ``boot:domovoi/ap.json`` into ``~/.domovoi`` inside its ``code`` step —
    which is already marked done on a golden master. A card flashed from
    that master and personalized afterwards has fresh credentials on its
    boot partition that nothing would ever copy across, so reading only the
    home copy would leave every mass-flashed unit falling back to the USB
    gadget with no portal at all.

    Boot-first also gives the right precedence when both exist: the card's
    own identity beats whatever the master happened to carry.
    """
    return [boot / "domovoi" / "ap.json" for boot in BOOT_DIRS] + [
        CONFIG_DIR / "ap.json"
    ]


def portal_credentials() -> dict[str, str] | None:
    """The baked setup-AP credentials, or None for a USB-gadget unit.

    Presence of this sidecar is what selects the transport: media prep and
    the packaging bench write it only for units meant to onboard over the
    Wi-Fi setup portal."""
    for path in ap_credential_paths():
        creds = _read_ap_json(path)
        if creds is not None:
            log.info("setup-AP credentials from %s", path)
            return creds
    return None


# ─── Gadget backend (configfs + mtools; injectable for tests) ─────────────


class GadgetBackend:
    """Thin wrapper around the configfs mass-storage gadget + mtools image
    access. Every subprocess/sysfs touch lives here so the state machine in
    ``run()`` is fully testable with a fake."""

    def build_image(self, image: Path, device_info: dict[str, Any]) -> None:
        image.parent.mkdir(parents=True, exist_ok=True)
        with open(image, "wb") as f:
            f.truncate(IMAGE_BYTES)
        self._run(["mkfs.vfat", "-F", "16", "-n", proto.VOLUME_LABEL, str(image)])
        info_tmp = image.parent / "device-info.tmp.json"
        info_tmp.write_text(json.dumps(device_info, indent=2), encoding="utf-8")
        try:
            self._run([
                "mcopy", "-i", str(image), str(info_tmp),
                f"::/{proto.DEVICE_INFO_NAME}",
            ])
        finally:
            info_tmp.unlink(missing_ok=True)

    def bind(self, image: Path) -> None:
        """Compose + bind the mass-storage gadget. Identity choices:
        Linux Foundation composite VID/PID, a MAC-derived serial (stable
        Windows drive identity across replugs), ``stall=0`` (the Windows
        enumeration-compatibility knob), ``removable=1``."""
        g = GADGET_DIR
        (g / "functions/mass_storage.usb0/lun.0").mkdir(parents=True, exist_ok=True)
        (g / "configs/c.1").mkdir(parents=True, exist_ok=True)
        (g / "strings/0x409").mkdir(parents=True, exist_ok=True)
        (g / "idVendor").write_text("0x1d6b\n")
        (g / "idProduct").write_text("0x0104\n")
        serial = (read_wlan_mac() or "domovoi").replace(":", "")
        (g / "strings/0x409/serialnumber").write_text(serial + "\n")
        (g / "strings/0x409/manufacturer").write_text("Domovoi\n")
        (g / "strings/0x409/product").write_text("Domovoi satellite setup\n")
        ms = g / "functions/mass_storage.usb0"
        (ms / "stall").write_text("0\n")
        (ms / "lun.0/removable").write_text("1\n")
        (ms / "lun.0/file").write_text(str(image) + "\n")
        link = g / "configs/c.1/mass_storage.usb0"
        if not link.exists():
            link.symlink_to(ms)
        udcs = sorted(p.name for p in UDC_DIR.iterdir())
        if not udcs:
            raise RuntimeError(
                "no UDC found — is the USB controller in peripheral/OTG mode? "
                "(dwc2 overlay on Pi; dr_mode=peripheral on RK3566)"
            )
        (g / "UDC").write_text(udcs[0] + "\n")

    def unbind(self) -> None:
        try:
            (GADGET_DIR / "UDC").write_text("\n")
        except OSError:
            pass

    def read_file(self, image: Path, name: str) -> bytes | None:
        """Read one root file out of the raw backing image via mtools —
        never mounts. None when absent."""
        r = self._run(
            ["mtype", "-i", str(image), f"::/{name}"],
            check=False, capture=True,
        )
        return r.stdout if r.returncode == 0 else None

    def delete_file(self, image: Path, name: str) -> None:
        self._run(["mdel", "-i", str(image), f"::/{name}"], check=False)

    def reboot(self) -> None:
        self._run(["systemctl", "--no-block", "reboot"], check=False)

    @staticmethod
    def _run(cmd: list[str], check: bool = True, capture: bool = False):
        r = subprocess.run(
            cmd,
            capture_output=capture,
            timeout=60,
        )
        if check and r.returncode != 0:
            raise RuntimeError(f"{cmd[0]} failed with rc={r.returncode}")
        return r


# ─── Wi-Fi join (nmcli first, wpa_supplicant fallback) ────────────────────


# How long to keep rescanning for the customer's network before trying to
# join it anyway. A scan takes a few seconds; the interface has just come out
# of AP mode and may need one or two.
_WIFI_SCAN_WAIT_SEC = 12.0
_WIFI_SCAN_POLL_SEC = 1.0
# Between join attempts. Three back-to-back identical attempts fail
# identically; a pause lets NetworkManager's state settle.
_WIFI_RETRY_PAUSE_SEC = 2.0
# One radio, always wlan0 — the wpa_supplicant path below has said so since
# the beginning, and `nmcli connection add` wants the interface named.
_WIFI_IFACE = "wlan0"
# The house network's profile is named this rather than after the network
# (2026-10-12), so the client's recovery can bring it back BY NAME through
# one exact sudoers line (`nmcli connection up id domovoi-home`) instead of
# `nmcli device connect wlan0`, which lets NetworkManager pick any profile,
# the setup hotspot included. See satellite/wifi_profiles.py.
_HOME_CONNECTION = "domovoi-home"
# Wi-Fi power saving off, persisted in the profile (NetworkManager's
# 802-11-wireless.powersave: 2 = disable). See satellite/net_settings.py.
_POWERSAVE_OFF = "2"

# The `wifi-sec.key-mgmt` values a home network can want. `wpa-psk` covers
# WPA2, WPA (TKIP) and the WPA2 side of WPA2/WPA3 mixed; `sae` is WPA3;
# `none` (no security block at all) is an open network.
_KEY_MGMT_PSK = "wpa-psk"
_KEY_MGMT_SAE = "sae"
_KEY_MGMT_OPEN = "none"
# `802-11-wireless-security.pmf` values (Protected Management Frames).
#
# PMF OFF on the first wpa-psk attempt is the fix for 2026-10-22, and the
# same fix the setup hotspot got on 2026-10-17 (portal_transport.AP_PMF).
# For a key-mgmt=wpa-psk profile with PMF left at its default, NetworkManager
# hands wpa_supplicant `key_mgmt=WPA-PSK WPA-PSK-SHA256 SAE ...` whenever
# the supplicant and driver report SAE + PMF + BIP support
# (src/core/supplicant/nm-supplicant-config.c, client AND access-point
# mode). Against a WPA2/WPA3 mixed router, wpa_supplicant then prefers SAE,
# and the Pi Zero 2 W's brcmfmac radio cannot complete SAE: the join fails
# as "Secrets were required" with the right password
# (raspberrypi/linux#4976). With PMF disabled NM leaves SAE out, the
# supplicant joins the WPA2 side, and a WPA2 router is unaffected (PMF is
# optional in WPA2). A WPA2 router that REQUIRES PMF gets a second attempt
# with PMF at NM's default.
_PMF_DISABLE = "disable"
_PMF_DEFAULT = "default"
# PMF OPTIONAL is the second wpa-psk try on a WPA2 or WPA2/WPA3 router: an
# access point that REQUIRES PMF (some Wi-Fi 7 routers run transition mode
# that way) refuses a PMF-off client at association (802.11 status 31).
# With PMF optional NetworkManager also offers SAE again where the radio
# claims it - which is why it is never the FIRST try on a Pi Zero 2 W.
_PMF_OPTIONAL = "optional"
_PMF_REQUIRED = "required"      # WPA3 (SAE) mandates PMF
_PSK_PLACEHOLDER = "<psk>"
_NETWORK_PLACEHOLDER = "<network>"


def _split_terse(line: str) -> list[str]:
    """Split one ``nmcli -t`` row into its fields.

    nmcli escapes a colon inside a value as ``\\:``, so splitting on every
    colon turns ``Guest\\:5G:WPA2`` into three fields and the wrong answer.
    """
    fields: list[str] = []
    cur: list[str] = []
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line):
            cur.append(line[i + 1])
            i += 2
            continue
        if ch == ":":
            fields.append("".join(cur))
            cur = []
            i += 1
            continue
        cur.append(ch)
        i += 1
    fields.append("".join(cur))
    return fields


def _scrub(text: str, psk: str) -> str:
    """The passphrase never leaves this module — not even inside a message
    another tool handed back to us.

    nmcli normally says nothing about the value of ``wifi-sec.psk``, but
    "normally" is not a promise we can make to the customer: the error text
    goes into a log line and onto the setup page, and those are the two
    places a CORRECT password must never appear.
    """
    if psk and len(psk) >= 4 and psk in text:
        return text.replace(psk, _PSK_PLACEHOLDER)
    return text


def _nmcli_text(r) -> str:
    """Everything nmcli printed, stderr then stdout, as text."""
    parts = []
    for out in (r.stderr, r.stdout):
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        if out:
            parts.append(out)
    return "\n".join(parts)


def _nmcli_detail(r, psk: str) -> str:
    """nmcli's own reason for a failure, one line, scrubbed. Empty when it
    said nothing at all.

    The reason, not the last line: a real nmcli ends every activation
    failure with ``Hint: use 'journalctl -xe NM_CONNECTION=...`` and the
    reason is the ``Error:`` line ABOVE it. Taking the last line is what
    showed Kamron the hint instead of the reason (2026-10-22), and what made
    the sae fallback never run (the hint matched no failure marker)."""
    return wifi_reasons.reason_line(_scrub(_nmcli_text(r), psk))


class _Scan:
    """What one rescan said about the network being joined.

    ``security`` is nmcli's SECURITY field for every row carrying the name
    (an AP on both bands contributes both rows' flags): ``None`` when the
    name was never seen, ``""`` when it was seen with no security at all,
    i.e. an open network. ``freqs`` are the rows' frequencies in MHz, where
    nmcli gave them."""

    __slots__ = ("visible", "security", "freqs")

    def __init__(self, visible: bool, security: str | None, freqs: list[int]):
        self.visible = visible
        self.security = security
        self.freqs = freqs

    def __iter__(self):            # (visible, security), as it always was
        return iter((self.visible, self.security))


def _wait_for_ssid(
    nmcli: str, ssid: str, run, timeout: float, sleep=time.sleep
) -> _Scan:
    """Rescan until NetworkManager can see ``ssid``, or give up.

    Found on hardware, reliably: the first join ALWAYS failed and the second
    always worked, same password pasted both times. The join ran the instant
    the setup AP came down, while the interface was still leaving AP mode and
    NetworkManager's scan cache held nothing - it had been hosting, not
    scanning. `nmcli device wifi connect` refuses an SSID it cannot see, and
    the retries that followed were back-to-back with no rescan, so all three
    failed the same way. By the customer's second submission NM had scanned.

    The join no longer DEPENDS on this scan (see :func:`_nmcli_join`) — but
    the SECURITY field is the only way to tell WPA2 from WPA2/WPA3 mixed
    from WPA3-only from open from enterprise without guessing, so it is
    worth the same wait it always was.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            r = run(
                [nmcli, "-t", "-f", "SSID,SECURITY,FREQ", "device", "wifi",
                 "list", "--rescan", "yes"],
                capture_output=True, text=True, timeout=20,
            )
        except (OSError, subprocess.TimeoutExpired):
            r = None
        if r is not None and r.returncode == 0:
            flags: list[str] = []
            freqs: list[int] = []
            found = False
            for ln in (r.stdout or "").splitlines():
                row = _split_terse(ln.strip())
                if row and row[0].strip() == ssid:
                    found = True
                    if len(row) > 1:
                        flag = row[1].strip()
                        if flag and flag != "--":
                            flags.append(flag)
                    if len(row) > 2:
                        m = re.match(r"\s*(\d+)", row[2])
                        if m:
                            freqs.append(int(m.group(1)))
            if found:
                return _Scan(True, " ".join(flags), freqs)
        if time.monotonic() >= deadline:
            return _Scan(False, None, [])
        sleep(_WIFI_SCAN_POLL_SEC)


def _security_tokens(security: str | None) -> set[str]:
    return set((security or "").upper().replace(",", " ").split())


def _is_raw_key(psk: str) -> bool:
    return len(psk) == 64 and all(c in "0123456789abcdefABCDEF" for c in psk)


def _join_plan(
    security: str | None, psk: str
) -> tuple[list[tuple[str, str | None]], str | None, str | None]:
    """``(attempts, refusal, family)`` for a network whose scan said
    ``security`` (None: never seen).

    ``attempts`` is the ordered list of ``(key-mgmt, pmf)`` to try, each a
    fresh profile; ``refusal`` is a :mod:`satellite.wifi_reasons` kind when
    there is nothing worth trying (an enterprise network, WEP, a blank
    password for a secured network); ``family`` names what the scan said
    (``wpa2``, ``mixed``, ``wpa3``, ``open``, ``unknown``), which decides
    how a run of refusals is explained.

    Every order below starts with the one setting that works on the Pi Zero
    2 W for that kind of network, then the one a different radio or router
    may need:

    * WPA2 / WPA (TKIP): wpa-psk PMF off, then wpa-psk PMF optional (a
      WPA2 router that requires PMF).
    * WPA2/WPA3 mixed: wpa-psk PMF off (the WPA2 side; NM leaves SAE out),
      then wpa-psk PMF optional (a router that requires PMF, status 31),
      then sae PMF required (a radio that can do WPA3).
    * WPA3-only: sae PMF required, once. A 64-hex key cannot be used with
      SAE (it needs the password itself), so that is refused in words.
    * Open: no security block.
    * Not seen / hidden: wpa-psk PMF off, then sae PMF required — what
      nearly every home network is, cheapest first.
    """
    tokens = _security_tokens(security)
    raw = _is_raw_key(psk)
    if security is not None and ("802.1X" in tokens or "EAP" in tokens) \
            and not tokens & {"WPA-PSK", "PSK", "SAE"}:
        return [], wifi_reasons.ENTERPRISE, "enterprise"
    if "WEP" in tokens:
        return [], wifi_reasons.WEP, "wep"
    if tokens == {"OWE"}:
        # "Enhanced open": encrypted, no password.
        return [("owe", None)], None, "open"
    sae = bool(tokens & {"WPA3", "SAE"})
    wpa = bool(tokens & {"WPA", "WPA1", "WPA2", "PSK", "WPA-PSK"})
    if security is not None and not tokens:
        return [(_KEY_MGMT_OPEN, None)], None, "open"
    if not psk:
        if security is None:
            # Blank password and a network we could not see: an open one
            # is the only kind it could join.
            return [(_KEY_MGMT_OPEN, None)], None, "unknown"
        return [], wifi_reasons.NEEDS_PASSWORD, "secured"
    if sae and not wpa:
        if raw:
            return [], wifi_reasons.WPA3_RAW_KEY, "wpa3"
        return [(_KEY_MGMT_SAE, _PMF_REQUIRED)], None, "wpa3"
    if sae and wpa:
        plan = [(_KEY_MGMT_PSK, _PMF_DISABLE), (_KEY_MGMT_PSK, _PMF_OPTIONAL)]
        if not raw:
            plan.append((_KEY_MGMT_SAE, _PMF_REQUIRED))
        return plan, None, "mixed"
    if wpa:
        return [(_KEY_MGMT_PSK, _PMF_DISABLE), (_KEY_MGMT_PSK, _PMF_OPTIONAL)], \
            None, "wpa2"
    plan = [(_KEY_MGMT_PSK, _PMF_DISABLE)]
    if not raw:
        plan.append((_KEY_MGMT_SAE, _PMF_REQUIRED))
    return plan, None, "unknown"


def _key_mgmt_candidates(security: str | None) -> list[str]:
    """The key-mgmt values :func:`_join_plan` would try, in order (kept for
    readers of the older shape)."""
    plan, _refusal, _family = _join_plan(security, "x" * 8)
    return [km for km, _pmf in plan]


# The phy's band list, from `iw phy`. Band 1 is 2.4 GHz, band 2 is 5 GHz.
_IW_BAND = re.compile(r"^\s*Band (\d+):", re.MULTILINE)


def _radio_is_24ghz_only(run) -> bool | None:
    """True when this radio has no 5 GHz band (the Pi Zero 2 W, Zero W,
    Pi 3 B), False when it has one, None when `iw` would not say."""
    try:
        r = run(["iw", "phy"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = r.stdout if isinstance(r.stdout, str) else (r.stdout or b"").decode(
        "utf-8", "replace")
    if r.returncode != 0 or not out:
        return None
    bands = set(_IW_BAND.findall(out))
    if not bands:
        return None
    return "2" not in bands


# The last failed join's evidence from NetworkManager's own journal, kept
# for the card's report (setup_report). Scrubbed: no network name, no
# password, no hardware addresses.
LAST_JOIN_LOG: list[str] = []
# What every join attempt of the current provisioning attempt did: the scan
# of the chosen network's access points and one entry per attempt
# (satellite/join_diag.py). Reset by apply_provision; goes on the card.
JOIN_DIAG = join_diag.Diag()
_JOURNAL_KEYS = (
    "reason", "auth", "handshake", "sae", "assoc", "ctrl-event", "4-way",
    "disconnect", "secrets", "failed", "pmf", "key_mgmt", "key-mgmt",
)
_MAC = re.compile(r"\b[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}\b")


def _journal_tail(run, ssid: str, psk: str, lines: int = 8) -> list[str]:
    """NetworkManager's and wpa_supplicant's last words about the join,
    filtered to the lines that say why, scrubbed. Empty when the journal is
    not readable (not root, no systemd)."""
    try:
        r = run(
            ["journalctl", "-b", "--no-pager", "-o", "cat", "--since", "-3min",
             "-u", "NetworkManager", "-u", "wpa_supplicant"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    out = r.stdout if isinstance(r.stdout, str) else (r.stdout or b"").decode(
        "utf-8", "replace")
    if r.returncode != 0 or not out:
        return []
    keep = []
    for ln in out.splitlines():
        low = ln.lower()
        if not any(k in low for k in _JOURNAL_KEYS):
            continue
        ln = _scrub_ssid(_scrub(ln, psk), ssid)
        ln = _MAC.sub("xx:xx:xx:xx:xx:xx", ln)
        keep.append(ln.strip()[:240])
    return keep[-lines:]


_ADDED_UUID = re.compile(r"\(([0-9a-fA-F-]{36})\)")


def _uuid_from_add(proc) -> str | None:
    """The UUID ``nmcli connection add`` just printed, or ``None``.

    nmcli answers a successful add with
    ``Connection '<name>' (<uuid>) successfully added.`` — the only
    identifier that names the profile WE built rather than one that
    merely shares its SSID or its name. Everything else is a guess:
    activating by ``id <ssid>`` resolves by connection NAME, so if a
    stale profile survived the delete pass (root refused it, NM was mid
    reload, it was created by something else) nmcli can bring up the
    broken one and hand the customer back the exact
    ``key-mgmt: property is missing`` this whole change exists to remove.
    """
    # The add call is not text-mode (the PSK is on its argv and we keep
    # every nmcli invocation uniform), so stdout arrives as bytes here and
    # as str from a text-mode caller. Handle both rather than assuming.
    out = getattr(proc, "stdout", "") or ""
    if isinstance(out, bytes):
        out = out.decode("utf-8", "replace")
    m = _ADDED_UUID.search(out)
    return m.group(1) if m else None

def _wifi_profiles_for_ssid(nmcli: str, ssid: str, run) -> list[str]:
    """UUIDs of every saved Wi-Fi profile whose SSID is ``ssid``.

    Matched on the ssid PROPERTY rather than the profile's name, because
    that is what NetworkManager itself matches on when it decides to reuse a
    profile: ``Maple Wifi 2.0 1`` carries the same ssid as
    ``Maple Wifi 2.0`` and is just as able to break the next attempt.
    """
    try:
        r = run(
            [nmcli, "-t", "-f", "UUID,TYPE", "connection", "show"],
            capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if r.returncode != 0:
        return []
    wifi_uuids = []
    for ln in (r.stdout or "").splitlines():
        row = _split_terse(ln.strip())
        if len(row) >= 2 and row[1] in ("802-11-wireless", "wifi") and row[0]:
            wifi_uuids.append(row[0])
    matches = []
    for uuid in wifi_uuids:
        try:
            g = run(
                [nmcli, "-t", "-f", "802-11-wireless.ssid", "connection",
                 "show", "uuid", uuid],
                capture_output=True, text=True, timeout=20,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if g.returncode != 0:
            continue
        for ln in (g.stdout or "").splitlines():
            row = _split_terse(ln.strip())
            if len(row) >= 2 and row[0].strip() == "802-11-wireless.ssid":
                if row[1].strip() == ssid:
                    matches.append(uuid)
                break
    return matches


def _delete_other_home_profiles(nmcli: str, keep_uuid: str, run) -> None:
    """Delete every profile named the home name except ``keep_uuid``."""
    try:
        r = run(
            [nmcli, "-t", "-f", "NAME,UUID", "connection", "show"],
            capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return
    if r.returncode != 0:
        return
    out = r.stdout or ""
    if isinstance(out, bytes):
        out = out.decode("utf-8", "replace")
    others = []
    for ln in out.splitlines():
        row = _split_terse(ln.strip())
        if len(row) >= 2 and row[0] == _HOME_CONNECTION and row[1] and row[1] != keep_uuid:
            others.append(row[1])
    if others:
        log.info("removing %d earlier %s profile(s)", len(others), _HOME_CONNECTION)
        _delete_wifi_profiles(nmcli, others, run)


def _delete_wifi_profiles(nmcli: str, uuids: list[str], run) -> None:
    """Best-effort removal. A profile we could not delete is reported by the
    join that follows, in nmcli's words, not guessed at here."""
    for uuid in uuids:
        try:
            run(
                [nmcli, "connection", "delete", "uuid", uuid],
                capture_output=True, timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass


def _scrub_ssid(text: str, ssid: str) -> str:
    """``text`` without the network's name, so nmcli's words can go where
    the name may not (the card). The quoted forms nmcli uses always; the
    bare name only when it is long enough not to eat ordinary words."""
    if not ssid:
        return text
    for form in (f"'{ssid}'", f'"{ssid}"'):
        text = text.replace(form, f"'{_NETWORK_PLACEHOLDER}'")
    if len(ssid) >= 4:
        text = text.replace(ssid, _NETWORK_PLACEHOLDER)
    return text


# A DIAGNOSTIC pin for the house network's join, from ap.json on the boot
# partition (``"join_bssid": "aa:bb:cc:dd:ee:ff"``, ``"join_band": "bg"`` or
# ``"a"``): which of two access points broadcasting one name the setup join
# may use, to tell them apart on hardware. Never a fix: it is cleared from
# the profile once the join worked, so the satellite still roams later.
JOIN_PIN: dict[str, str] = {}
_BSSID = re.compile(r"^[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}$")


def set_join_pin(bssid: str | None, band: str | None) -> None:
    JOIN_PIN.clear()
    if bssid:
        if _BSSID.match(bssid.strip()):
            JOIN_PIN["bssid"] = bssid.strip().lower()
        else:
            log.warning("ignoring ap.json join_bssid (not a hardware address)")
    if band:
        if band.strip().lower() in ("bg", "a"):
            JOIN_PIN["band"] = band.strip().lower()
        else:
            log.warning("ignoring ap.json join_band=%r (allowed: bg, a)", band)
    if JOIN_PIN:
        log.warning("DIAGNOSTIC: the house network join is pinned (%s); remove "
                    "join_bssid/join_band from ap.json when done",
                    ", ".join(f"{k}={v}" for k, v in JOIN_PIN.items()))


def _clear_join_pin(nmcli: str, uuid: str, run) -> None:
    """After a pinned join worked: the profile may roam again."""
    if not JOIN_PIN:
        return
    args = [nmcli, "connection", "modify", "uuid", uuid]
    if "bssid" in JOIN_PIN:
        args += ["802-11-wireless.bssid", ""]
    if "band" in JOIN_PIN:
        args += ["802-11-wireless.band", ""]
    try:
        run(args, capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        log.warning("could not clear the diagnostic join pin from the profile")


def _profile_args(
    nmcli: str, ssid: str, psk: str, hidden: bool, km: str, pmf: str | None
) -> list[str]:
    """The `nmcli connection add` argv for one attempt. A list, never a
    shell line: a name or password with spaces, quotes or any other byte
    reaches NetworkManager exactly as typed."""
    add = [
        nmcli, "connection", "add", "type", "wifi",
        "con-name", _HOME_CONNECTION, "ifname", _WIFI_IFACE, "ssid", ssid,
        "connection.autoconnect", "yes",
        "802-11-wireless.powersave", _POWERSAVE_OFF,
    ]
    if km != _KEY_MGMT_OPEN:
        add += ["wifi-sec.key-mgmt", km]
        if pmf is not None:
            add += ["wifi-sec.pmf", pmf]
        if km in (_KEY_MGMT_PSK, _KEY_MGMT_SAE):
            add += ["wifi-sec.psk", psk]
    if hidden:
        # A hidden AP is in no scan by definition, so the profile has to
        # say so or NM never probes for it. Same inference hole as the
        # visible case, same explicit answer.
        add += ["802-11-wireless.hidden", "yes"]
    if "bssid" in JOIN_PIN:
        add += ["802-11-wireless.bssid", JOIN_PIN["bssid"]]
    if "band" in JOIN_PIN:
        add += ["802-11-wireless.band", JOIN_PIN["band"]]
    return add


def _note_attempt(run, started: float, km: str, pmf: str | None, ok: bool,
                  kind: str | None, detail: str, ssid: str, psk: str) -> None:
    """One attempt into JOIN_DIAG, and its gist into the journal. Never
    stops a join."""
    try:
        e = JOIN_DIAG.record(run, since=started, key_mgmt=km, pmf=pmf, ok=ok,
                             kind=kind, detail=detail, ssid=ssid, psk=psk)
        log.info("join attempt %s/%s: %s via %s %s%s%s", km, pmf or "-", e["outcome"],
                 e["ap"] or "?", e["freq_mhz"] or "",
                 (" reasons " + ", ".join(e["reason_codes"])) if e["reason_codes"] else "",
                 (" status " + ", ".join(e["status_codes"])) if e["status_codes"] else "")
    except Exception as ex:  # noqa: BLE001 — diagnostics never fail a join
        log.debug("join diagnostics failed: %s", ex)


def _retryable(kind: str, text: str) -> bool:
    """A failure another security setting might get past: the AP was
    reached and turned us away. A network that is not there, a radio that
    is not usable, or a timeout says something else and gets no second
    attempt."""
    if kind in wifi_reasons.AUTH_KINDS:
        return True
    return kind == wifi_reasons.OTHER and "activation failed" in text.lower()


def _nmcli_join(
    nmcli: str,
    ssid: str,
    psk: str,
    hidden: bool,
    timeout: float,
    run,
    security: str | None,
    *,
    only_24ghz: bool | None = None,
) -> tuple[bool, str | None]:
    """Build the connection profile ourselves, then bring it up.

    The old form — ``nmcli device wifi connect <ssid> password <psk>`` —
    asks NetworkManager to INFER the security type from a recent scan of
    that AP. Joining blind, which is exactly what happens when wlan0 has
    spent the last minute HOSTING the setup AP rather than scanning, leaves
    nothing to infer from: NM writes a profile carrying a PSK and no
    key-mgmt, and activation dies on ``802-11-wireless-security.key-mgmt:
    property is missing`` while the customer stares at a password they typed
    correctly. Naming key-mgmt (and PMF, see ``_PMF_DISABLE``) ourselves
    deletes the inference.

    Each attempt of :func:`_join_plan` starts from a profile WE made this
    second: see :func:`_wifi_profiles_for_ssid` for why inheriting one is
    what made every retry fail identically.

    A failure comes back tagged with its :mod:`satellite.wifi_reasons`
    kind, and nmcli's reason line with the network's name and the password
    taken out.
    """
    prefix = f"wifi join failed for {ssid!r}"
    plan, refusal, family = _join_plan(security, psk)
    if refusal is not None:
        log.warning("not joining %r: %s (scan said %r)", ssid, refusal, security)
        return False, wifi_reasons.tagged(prefix, refusal)

    def clean(text: str) -> str:
        return _scrub_ssid(text, ssid)

    first: tuple[str, str] | None = None     # (kind, detail) of attempt 1
    kinds: list[str] = []
    tried = False
    for km, pmf in plan:
        # Whatever an earlier attempt left behind goes first — an earlier
        # submission's half-made profile, or the previous candidate's.
        # `nmcli device wifi connect` REUSED a matching saved profile, so
        # one broken profile made every later attempt fail the same way no
        # matter what was re-typed; and each fresh one is how
        # `Maple Wifi 2.0 1`, `... 2` pile up.
        _delete_wifi_profiles(
            nmcli, _wifi_profiles_for_ssid(nmcli, ssid, run), run
        )
        add = _profile_args(nmcli, ssid, psk, hidden, km, pmf)
        tried = True
        try:
            r = run(add, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            # Never re-raise: the argv of that call holds the passphrase and
            # TimeoutExpired stringifies the command it was given.
            return False, wifi_reasons.tagged(
                f"wifi join timed out for {ssid!r}", wifi_reasons.TIMEOUT)
        except OSError:
            return False, wifi_reasons.tagged(
                prefix, wifi_reasons.RADIO, "NetworkManager is not answering")
        if r.returncode != 0:
            detail = clean(_nmcli_detail(r, psk))
            log.warning(
                "nmcli profile for %r could not be created (rc=%d): %s",
                ssid, r.returncode, detail,
            )
            return False, wifi_reasons.tagged(
                f"wifi setup failed for {ssid!r}", wifi_reasons.SETUP_FAILED,
                detail or "the connection could not be created")

        # Activate THE PROFILE WE JUST MADE, by the uuid nmcli printed for
        # it. Re-deriving it from a fresh listing and falling back to
        # `id <ssid>` when the listing is ambiguous was wrong in exactly
        # the case this function exists for: `id` resolves by connection
        # NAME, so a stale profile that survived the delete pass gets
        # activated instead and the customer sees the original error again.
        uuid = _uuid_from_add(r)
        if uuid is None:
            # An nmcli whose add output we cannot parse. One saved profile
            # for this ssid can only be the one just added, so use it;
            # more than one means we cannot tell ours from the leftover,
            # and guessing is what we are here to stop doing.
            uuids = _wifi_profiles_for_ssid(nmcli, ssid, run)
            if len(uuids) != 1:
                log.warning(
                    "nmcli did not name the profile it created for %r and "
                    "%d saved profiles carry that ssid - refusing to guess "
                    "which one to bring up", ssid, len(uuids),
                )
                return False, wifi_reasons.tagged(
                    f"wifi setup failed for {ssid!r}", wifi_reasons.SETUP_FAILED,
                    "the connection could not be identified after it was created")
            uuid = uuids[0]
        up = [nmcli, "connection", "up", "uuid", uuid]
        started = time.time()
        try:
            r = run(up, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            _note_attempt(run, started, km, pmf, False, wifi_reasons.TIMEOUT, "", ssid, psk)
            _delete_wifi_profiles(
                nmcli, _wifi_profiles_for_ssid(nmcli, ssid, run), run)
            return False, wifi_reasons.tagged(
                f"wifi join timed out for {ssid!r}", wifi_reasons.TIMEOUT)
        except OSError:
            return False, wifi_reasons.tagged(
                prefix, wifi_reasons.RADIO, "NetworkManager is not answering")
        if r.returncode == 0:
            # One profile answers to the home name: a re-provision onto a
            # different network leaves the previous network's profile
            # behind (it matched no ssid above), and `connection up id`
            # with two of them is a coin toss.
            _delete_other_home_profiles(nmcli, uuid, run)
            _clear_join_pin(nmcli, uuid, run)
            _note_attempt(run, started, km, pmf, True, None, "", ssid, psk)
            LAST_JOIN_LOG.clear()
            log.info("joined %r as %s (pmf %s)", ssid, km, pmf or "-")
            return True, None
        # Keep nmcli's own words — the reason line, not the Hint after it.
        text = _scrub(_nmcli_text(r), psk)
        detail = clean(_nmcli_detail(r, psk))
        kind = wifi_reasons.classify(text)
        kinds.append(kind)
        _note_attempt(run, started, km, pmf, False, kind, detail, ssid, psk)
        log.warning(
            "nmcli join of %r as %s (pmf %s) failed (rc=%d, %s): %s",
            ssid, km, pmf or "-", r.returncode, kind, detail,
        )
        if first is None:
            first = (kind, detail)
        if not _retryable(kind, text):
            break
    if tried:
        # Don't leave a profile that cannot associate sitting there with
        # autoconnect on: the next attempt would delete it anyway, and
        # nothing else on the device wants it.
        _delete_wifi_profiles(
            nmcli, _wifi_profiles_for_ssid(nmcli, ssid, run), run
        )
    LAST_JOIN_LOG[:] = _journal_tail(run, ssid, psk)
    for ln in LAST_JOIN_LOG:
        log.warning("NetworkManager: %s", ln)
    kind, detail = first or (wifi_reasons.OTHER, "")
    if family == "wpa3" and all(k in wifi_reasons.AUTH_KINDS for k in kinds):
        # Every attempt was SAE and every one was turned away. Either the
        # password is wrong or the radio cannot do WPA3; the words say both.
        kind = wifi_reasons.WPA3_ONLY
    elif kind == wifi_reasons.NOT_FOUND and only_24ghz:
        kind = wifi_reasons.NOT_FOUND_24
    return False, wifi_reasons.tagged(prefix, kind, detail)


def _wpa_conf_drop_ssid(ssid: str) -> int:
    """Remove any ``network={...}`` block this code wrote for ``ssid``
    before, and say how many went. Rewrites nothing when there are none.

    Same reason the nmcli path replaces its profile: a retry must not
    inherit a half-made attempt, and three submissions must not leave three
    blocks for one network — wpa_supplicant takes the first one it likes and
    the file grows with every try.
    """
    conf = WPA_SUPPLICANT_CONF
    want = f"ssid={ssid.encode('utf-8').hex()}"
    try:
        lines = conf.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError:
        return 0
    kept: list[str] = []
    dropped = 0
    i = 0
    while i < len(lines):
        if lines[i].strip() != "network={":
            kept.append(lines[i])
            i += 1
            continue
        j = i
        while j < len(lines) and lines[j].strip() != "}":
            j += 1
        block = lines[i:min(j + 1, len(lines))]
        if any(ln.strip() == want for ln in block):
            dropped += 1
            # The blank line we wrote in front of it goes with it.
            while kept and not kept[-1].strip():
                kept.pop()
        else:
            kept.extend(block)
        i = j + 1
    if not dropped:
        return 0
    tmp = conf.with_name(conf.name + ".domovoi.tmp")
    tmp.write_text("".join(kept), encoding="utf-8")
    try:
        os.chmod(tmp, stat.S_IMODE(os.stat(conf).st_mode))
    except OSError:
        pass
    os.replace(tmp, conf)
    return dropped


def apply_wifi(
    ssid: str,
    psk: str,
    country: str | None,
    hidden: bool,
    timeout: float,
    run=subprocess.run,
) -> tuple[bool, str | None]:
    """Join the network and verify reachability. (ok, error). The PSK is
    passed via argv to nmcli (process args are root-only readable here) and
    NEVER logged — errors mention the ssid only, and any text nmcli hands
    back is scrubbed before it reaches a log line or the setup page.

    The network name is checked first, on both paths: it lands in a
    root-owned network configuration, and a name with a newline, a quote
    or a brace in it is refused rather than written.

    Neither path infers anything: the nmcli profile names its own
    ``wifi-sec.key-mgmt`` and PMF (:func:`_join_plan`) and the
    wpa_supplicant block names its own ``psk=`` (or ``key_mgmt=NONE`` for
    an open network, the one case with a blank ``psk``). A failure is
    tagged with its :mod:`satellite.wifi_reasons` kind."""
    try:
        proto.validate_wifi_ssid(ssid)
    except proto.ProvisionInvalid as e:
        return False, f"wifi network name refused: {e}"
    if country:
        run(["iw", "reg", "set", country], capture_output=True, timeout=15)
    nmcli = shutil.which("nmcli")
    if nmcli:
        # A network flagged hidden never appears in a scan. For everything
        # else the scan is still worth waiting for — it says WPA2, mixed,
        # WPA3-only, open or enterprise — but the join below no longer
        # NEEDS it to have worked.
        security: str | None = None
        if not hidden:
            scan = _wait_for_ssid(nmcli, ssid, run, _WIFI_SCAN_WAIT_SEC)
            security = scan.security
            # Every access point broadcasting the name, from the scan just
            # taken: two radios on one name with different settings is
            # exactly what one merged SECURITY string hides.
            try:
                JOIN_DIAG.take_scan(run, nmcli, ssid)
            except Exception as e:  # noqa: BLE001
                log.debug("join scan diagnostics failed: %s", e)
            for row in JOIN_DIAG.scan:
                log.info("join scan: %s %s ch %s %s MHz signal %s", row["ap"],
                         row["security"], row["channel"], row["freq_mhz"], row["signal"])
            if not scan.visible:
                # Not seen: still hosting, out of range, 5 GHz only on a
                # 2.4 GHz radio, or a hidden network typed by name. The
                # profile probes for it either way: `hidden yes` makes NM
                # ask for the name instead of waiting to hear it, which
                # costs nothing when the network is not hidden.
                hidden = True
                log.warning(
                    "%r not visible after %.0fs of scanning - joining blind, "
                    "probing for it by name, with an explicit security type",
                    ssid, _WIFI_SCAN_WAIT_SEC,
                )
        return _nmcli_join(
            nmcli, ssid, psk, hidden, timeout, run, security,
            only_24ghz=_radio_is_24ghz_only(run),
        )
    # wpa_supplicant fallback: append a network block of our own making
    # (ssid= as hex, psk= as the derived key, no passphrase comment) and
    # reconfigure. Built in Python rather than taken from wpa_passphrase,
    # whose output carries the name verbatim and the passphrase in a
    # comment.
    try:
        block = proto.wpa_supplicant_network_block(ssid, psk, hidden=hidden)
    except proto.ProvisionInvalid as e:
        return False, f"wifi credentials refused: {e}"
    try:
        # A retry replaces its predecessor rather than stacking behind it.
        _wpa_conf_drop_ssid(ssid)
        with open(WPA_SUPPLICANT_CONF, "a", encoding="utf-8") as f:
            f.write("\n" + block)
        run(["wpa_cli", "-i", "wlan0", "reconfigure"], capture_output=True, timeout=30)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            st = run(
                ["wpa_cli", "-i", "wlan0", "status"],
                capture_output=True, timeout=10,
            )
            if b"wpa_state=COMPLETED" in (st.stdout or b""):
                return True, None
            time.sleep(3)
        return False, wifi_reasons.tagged(
            f"wifi join timed out for {ssid!r}", wifi_reasons.TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"wifi tooling unavailable: {type(e).__name__}"


# ─── State helpers ────────────────────────────────────────────────────────


def _read_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_state(state: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")




def _audio_device_match(profile_name: str) -> str:
    """The PortAudio name substring this board pins its audio to, if any.

    A name, not an index: indexes shuffle between boots, and a satellite
    that works until someone reboots it is worse than one that never did.
    """
    try:
        from satellite.devices import PROFILES

        prof = PROFILES.get(profile_name)
        return getattr(prof, "audio_device_match", "") or ""
    except Exception:      # noqa: BLE001 - an unknown board just gets defaults
        return ""


def _music_alsa_device(profile_name: str) -> str:
    """The ALSA device this board pins its mpg123 audio to, if any.

    Separate from :func:`_audio_device_match` because music, the wake
    greeting and the canned clips go out through mpg123/ALSA rather than
    PortAudio — a different key, a different naming scheme, and (before
    this) a different outcome: TTS pinned to the array while every clip
    left through the system default, where the array's AEC never sees it.
    """
    try:
        from satellite.devices import PROFILES

        prof = PROFILES.get(profile_name)
        return getattr(prof, "provisioned_music_alsa_device", "") or ""
    except Exception:      # noqa: BLE001 - an unknown board just gets defaults
        return ""


def setup_status(state: str, *args: str) -> None:
    """Drive the setup indicator - the LED ring and the spoken line.

    The same ``domovoi-status`` helper stage 1 and stage 2 call, so all four
    programs that make up setup share one description of each phase. Purely
    best-effort: a satellite with no ring, no speaker, or no helper at all
    must still provision.
    """
    helper_path = "/usr/local/sbin/domovoi-status"
    try:
        if not os.access(helper_path, os.X_OK):
            return
        subprocess.run([helper_path, state, *args], timeout=60, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        log.debug("setup status %r failed: %s", state, e)


def give_to_satellite_user(path: Path) -> bool:
    """Hand a file written by root to the user that has to read it.

    Provisioning mode runs as root — nmcli, the USB gadget, timedatectl and
    the reboot all need it — but everything it writes is read by the
    satellite client running as the ordinary satellite user. A 0600 file
    owned by root is not "private to the satellite", it is invisible to it.

    That cost us the entire approval flow, silently. The pairing token was
    written root:root 0600, ``_effective_pairing_token`` got PermissionError,
    caught it as a plain OSError and returned None, the hello frame went out
    tokenless, and the core accepted the device down the legacy "no token, no
    row" path instead of parking it for a human to approve. config.toml only
    escaped because write_text leaves it 0644.

    The home directory is the reference: useradd made it, so it is owned by
    the satellite user whatever that user is called. Best-effort — a failure
    here must not strand a device mid-provision.
    """
    chown = getattr(os, "chown", None)
    if chown is None:          # Windows dev host — no ownership to hand over
        return False
    # Never through a link. These paths are in the satellite user's home,
    # and root following a link planted there hands that user whatever it
    # points at (a reviewer had root chown a root helper to uid 1000 that
    # way, 2026-10-12). A link is refused outright, and the chown itself
    # does not follow one either, so a swap in between changes only the
    # link.
    path = Path(path)
    if path.is_symlink():
        log.warning("not handing %s to the satellite user: it is a symbolic link", path)
        return False
    try:
        st = Path.home().stat()
        if chown in os.supports_follow_symlinks:
            chown(path, st.st_uid, st.st_gid, follow_symlinks=False)
        else:
            chown(path, st.st_uid, st.st_gid)
        return True
    except OSError as e:
        log.warning(
            "could not give %s to the satellite user (%s) — the client may "
            "not be able to read it", path, e,
        )
        return False


def is_provisioned() -> bool:
    """Provisioned = a config exists and no provisioning phase is active.
    The satellite client's main() parks while this module owns the box."""
    if not CONFIG_PATH.exists():
        return False
    return _read_state().get("phase") in (None, "done")


def provisioning_active() -> bool:
    return _read_state().get("phase") not in (None, "done")


# ─── Apply ────────────────────────────────────────────────────────────────


def resolve_auto_url(configured: str) -> str | None:
    """Turn the ``auto`` sentinel into a real address, right after Wi-Fi joins.

    This MUST happen here rather than being left to the client. Stage 2 reads
    ``domovoi_url`` out of config.toml with plain tomllib and curls it — it
    cannot expand a sentinel, so it never reaches the server, never enables
    domovoi-satellite.service, and the client that *could* resolve it never
    runs. A deadlock: the device sits happily on the network, invisible to
    the core, with nothing in any log that names the cause.

    Best-effort. A failure leaves the sentinel in place but says so loudly,
    because the symptom is otherwise just a satellite that never appears.
    """
    if (configured or "").strip().lower() != proto.AUTO_DISCOVER_URL:
        return configured or None
    # stdlib-only, like this module
    from satellite import discovery, server_identity

    expected, source = server_identity.pinned_fingerprint()
    # The core's name first (domovoi.local), then the subnet sweep: the
    # same order the client uses (discovery.locate), every candidate held
    # to the pinned fingerprint.
    found = discovery.locate(
        names=discovery.server_names(), last_known=None,
        expected_fingerprint=expected,
    )
    url = found.url if found is not None else None
    if url is None:
        log.error(
            "joined Wi-Fi but found no Domovoi server on this network%s. "
            "Set [satellite] domovoi_url in %s by hand.",
            f" answering for {expected} ({source})" if expected else "",
            CONFIG_PATH,
        )
        return None
    # Not into config.toml. The address is a candidate until the server
    # says this device is paired — that is, until a person approves it on
    # the dashboard — and the client promotes it then. Writing it here is
    # what used to make the first host that answered permanent.
    if not server_identity.write_pending_server(url, expected):
        log.error("discovered %s but could not save it", url)
        return None
    give_to_satellite_user(server_identity.PENDING_SERVER_SIDECAR)
    log.info(
        "discovered the Domovoi server at %s (%s) — pending approval on the "
        "dashboard", url, found.detail if found is not None else "",
    )
    return url


def _write_not_approved() -> None:
    """Tell the client this device has not been approved by the core it is
    now pointed at. Best-effort: a failure here costs the gate a boot, not
    a provision, and the core still parks the device either way."""
    tmp = APPROVED_MARKER_PATH.with_name(APPROVED_MARKER_PATH.name + ".tmp")
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        # Written to a sibling and renamed, exactly as the client writes
        # it (`client._write_approval_record`). A plain write truncates
        # first, and a customer who power-cycles a Pi that "isn't doing
        # anything yet" can leave a first line that is a PREFIX of the
        # verdict. The client reads anything that is neither word as no
        # evidence at all and keeps the microphone shut, so a torn record
        # is not dangerous — but it is also not informative, and a rename
        # costs nothing.
        tmp.write_text(
            f"{APPROVAL_NOT_APPROVED}\n"
            "# Written by provisioning: a fresh pairing token is a fresh "
            "identity, so this device must be approved again before its "
            "microphone opens at boot.\n",
            encoding="utf-8",
        )
        os.replace(tmp, APPROVED_MARKER_PATH)
        # Best-effort, and it no longer decides anything: this file is
        # written as root and read by the satellite user, and when the
        # chown fails the client used to be unable to rewrite it for the
        # life of the device — a working satellite pinned at "not
        # approved". The client replaces the record by rename now, which
        # needs the DIRECTORY and not the file, so a failed chown here
        # costs the record nothing.
        give_to_satellite_user(APPROVED_MARKER_PATH)
    except OSError as e:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        log.warning("could not reset the approval record: %s", e)


def apply_provision(
    payload: dict[str, Any],
    *,
    transport: Transport,
    wifi_attempts: int,
    wifi_join_timeout: float,
    run=subprocess.run,
    before_join=None,
) -> tuple[bool, str | None]:
    """Write config + pairing token, join Wi-Fi, clean up. (ok, error).
    The credentials are cleared from the transport BEFORE any possible
    re-expose, so they never ride a setup volume twice. ``before_join``
    (room_id) runs just before the Wi-Fi join: :func:`run` passes
    :func:`name_before_join`."""
    # 1. Config from the example, comment-preserving.
    example = EXAMPLE_CONFIG.read_text(encoding="utf-8")
    changes: dict[str, Any] = {
        "satellite.room_id": payload["room_id"],
        "satellite.domovoi_url": payload["domovoi_url"],
        "satellite.sat_type": payload.get("sat_type", "voice"),
        "device.profile": payload["device_profile"],
    }
    # The identity of the core that prepared this card, copied out of the
    # root-owned pin first boot installed, so the client compares against
    # a value it cannot itself have invented. Absent on a card prepared
    # before server identities — the client then falls back to recording
    # the first core it meets.
    from satellite import server_identity

    baked, _source = server_identity.pinned_fingerprint()
    if baked:
        changes["satellite.server_fingerprint"] = baked
    # Pin capture AND playback to the array on boards that need it. Without
    # this the client runs on the system default: capture lands on device
    # -1, and playback leaves the array entirely, so its on-chip AEC has no
    # echo reference and barge-in misfires on the satellite's own voice. It
    # was a manual step in PROVISIONING that no prepared card ever ran.
    match = _audio_device_match(payload["device_profile"])
    if match:
        changes["audio.input_device"] = match
        changes["audio.output_device"] = match
    # And the mpg123 side. PROVISIONING §F documents all THREE keys, but this
    # function used to write only the two above — so every prepared card
    # shipped with music, the wake greeting and the canned clips leaving
    # through the ALSA default while TTS went through the array. The chip's
    # AEC can only cancel what it plays — music above all, which is on while
    # the satellite listens for its wake word.
    music_dev = _music_alsa_device(payload["device_profile"])
    if music_dev:
        changes["music.alsa_device"] = music_dev
    merged = config_writer.apply_changes(example, changes)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    # The directory too: root created it, and the client writes into it (the
    # resolved domovoi_url, the synced-sha sidecar) as the satellite user.
    give_to_satellite_user(CONFIG_DIR)
    CONFIG_PATH.write_text(merged, encoding="utf-8")
    give_to_satellite_user(CONFIG_PATH)

    # 2. Pairing token, owner-only. The core already stored its sha256 —
    #    first connect matches as an already-paired room.
    PAIRING_TOKEN_PATH.write_text(payload["pairing_token"], encoding="utf-8")
    # Ownership BEFORE the mode, so 0600 means "only the satellite user"
    # rather than "only root", which is nobody who needs it.
    give_to_satellite_user(PAIRING_TOKEN_PATH)
    PAIRING_TOKEN_PATH.chmod(0o600)

    # 2b. A new pairing token is a new identity, so the previous core's
    #     verdict does not travel with it. Approval gates the microphone
    #     (satellite/client.py `_start_voice_input`), and a device re-homed
    #     onto a different core — or re-provisioned onto the same one —
    #     arrives with no pairing row: the core parks it and answers
    #     `awaiting_approval`. Without this the old `approved` record would
    #     still be on disk at the next boot, the mic would open before the
    #     core got a word in, and the whole of F-V013 would be back on the
    #     one path the gate does not otherwise cover.
    #
    #     Written as an explicit `not-approved` rather than deleted:
    #     deleting it leaves the device UNDECIDED, and the client's upgrade
    #     bridge would then re-approve it from the synced-sha sidecar its
    #     old core left behind.
    _write_not_approved()

    # 3. Timezone (best-effort).
    if payload.get("tz"):
        run(
            ["timedatectl", "set-timezone", str(payload["tz"])],
            capture_output=True, timeout=15,
        )

    # 4. Credentials unreadable BEFORE any re-expose.
    transport.clear_provision()

    # 4b. The room's host name, before the join: the router names a device
    #     after the host name in its first DHCP request, and keeps that
    #     name until the device next connects.
    if before_join is not None:
        before_join(payload["room_id"])

    # 5. Wi-Fi — the step that can fail on a typo'd PSK.
    wifi = payload["wifi"]
    JOIN_DIAG.reset()
    last_err: str | None = None
    for attempt in range(1, wifi_attempts + 1):
        ok, err = apply_wifi(
            wifi["ssid"], wifi["psk"], wifi.get("country"),
            bool(wifi.get("hidden")), wifi_join_timeout, run=run,
        )
        if ok:
            url = resolve_auto_url(payload.get("domovoi_url", ""))
            # 5b. Root's own record of where this device's server is. The
            #     clock helper runs as root on the satellite user's say-so
            #     and reads this rather than trusting the address that
            #     user hands it.
            write_root_server_pin(url)
            # 6. The clock and the time zone, from the server we can now
            #    reach. Step 3 applied what the server knew at adopt time
            #    and nothing about the clock; this is the precise one.
            sync_time_with_server(url or "", run=run)
            return True, None
        last_err = err
        log.warning("wifi attempt %d/%d failed: %s", attempt, wifi_attempts, err)
        if attempt < wifi_attempts:
            time.sleep(_WIFI_RETRY_PAUSE_SEC)
    return False, last_err or "wifi join failed"


# Root's record of this device's server. Written here, at adoption, while
# we still have root; read by the clock helper, which is invoked BY the
# satellite user and so must not take that user's word for where the time
# comes from. 0644: the satellite user may read it, only root may write it.
ROOT_CONFIG_DIR = Path("/etc/domovoi")
ROOT_SERVER_URL_PIN = ROOT_CONFIG_DIR / "server.url"


def write_root_server_pin(url: str | None) -> bool:
    """Record the address this device's server lives at, as root.

    Best-effort: a unit where /etc is not writable (a test host, a
    hand-built device) simply has no pin, and the helper then behaves as
    it did before pins existed."""
    if not (url or "").strip():
        return False
    try:
        ROOT_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        ROOT_SERVER_URL_PIN.write_text(
            url.strip() + "\n", encoding="utf-8", newline="\n"
        )
        os.chmod(ROOT_SERVER_URL_PIN, 0o644)
    except OSError as e:
        log.warning("could not record the server address for root: %s", e)
        return False
    return True


# Installed by stage 1 next to domovoi-status; absent on a hand-built unit.
SYNC_TIME_HELPER = "/usr/local/sbin/domovoi-sync-time"


def sync_time_with_server(url: str, run=subprocess.run) -> str | None:
    """Take the clock and the time zone from the server.

    A Pi has no battery clock: it boots with the date its image was built,
    in Pi OS's Europe/London, and NTP repairs only the clock and only when
    the house has internet. The server is the authority on both, and this
    is the first moment it is reachable. The same helper stage 2 and the
    client call, so a device converges on the server's time from three
    directions and any one of them failing costs nothing.

    Returns the helper's one-line verdict for the log, or None when it did
    not run. Never raises - a satellite must provision with the wrong time
    rather than not at all.
    """
    if not url:
        return None
    try:
        if not os.access(SYNC_TIME_HELPER, os.X_OK):
            log.info("no %s on this unit - clock and zone left to NTP", SYNC_TIME_HELPER)
            return None
        r = run([SYNC_TIME_HELPER, url], capture_output=True, text=True, timeout=45)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("time sync with the server did not run: %s", e)
        return None
    lines = (getattr(r, "stdout", "") or "").strip().splitlines()
    verdict = lines[-1] if lines else ""
    if getattr(r, "returncode", 1) == 0:
        log.info("time sync: %s", verdict or "ok")
    else:
        log.warning(
            "time sync (rc=%s): %s", getattr(r, "returncode", "?"),
            verdict or (getattr(r, "stderr", "") or "").strip(),
        )
    return verdict or None


_GADGET_CONFIG_LINE = "dtoverlay=dwc2,dr_mode=peripheral"
_GADGET_CMDLINE_TOKEN = "modules-load=dwc2"
# Written by the media-prep overlay for a USB-mic unit, and what a USB-
# adopted one needs added once its gadget mode is reverted. See
# satellite_media/overlay.py for why it is this and NOT a dwc2 host overlay.
_USB_FULLSPEED_TOKEN = "dwc_otg.speed=1"
_USB_MIC_PROFILES = ("xvf3800_usb",)


def revert_usb_gadget_boot_config(boot_dirs=None) -> list[str]:
    """Take the USB controller back out of peripheral mode after adoption.

    Media prep writes ``dtoverlay=dwc2,dr_mode=peripheral`` so the device can
    present itself as a flash drive. Nothing else ever removes it, and on a
    Pi Zero 2 W that pins the ONLY data port as a peripheral — where a USB
    mic array cannot enumerate. Without this a satellite adopts perfectly
    and then can't hear anything.

    Best-effort and never raises: a satellite that is otherwise provisioned
    must still boot. Returns the files changed, for logging and tests.
    """
    changed: list[str] = []
    for boot in (boot_dirs if boot_dirs is not None else BOOT_DIRS):
        cfg = boot / "config.txt"
        if not cfg.is_file():
            continue
        try:
            text = cfg.read_text(encoding="utf-8", errors="replace")
            # Remove the peripheral-mode overlay and nothing else. The
            # host side comes from the OTG adapter's ID pin under the Pi's
            # default dwc_otg driver; the dwc2 host overlay that used to be
            # swapped in here enumerates the array at high speed, where its
            # output is not audio. The full-speed pin goes on the CMDLINE,
            # below.
            kept = [ln for ln in text.splitlines()
                    if ln.strip() != _GADGET_CONFIG_LINE]
            if kept != text.splitlines():
                # The boot partition is FAT, and a torn config.txt or
                # cmdline.txt is a Pi that does not boot: never in place.
                boot_write.write_text(cfg, "\n".join(kept) + "\n")
                changed.append(str(cfg))
        except OSError as e:
            log.warning("could not revert %s: %s", cfg, e)

        cmdline = boot / "cmdline.txt"
        if not cmdline.is_file():
            continue
        try:
            raw = cmdline.read_text(encoding="utf-8", errors="replace")
            line = raw.strip().splitlines()[0] if raw.strip() else ""
            tokens = [t for t in line.split() if t != _GADGET_CMDLINE_TOKEN]
            # A USB-adopted mic-array unit was prepared in peripheral mode
            # and never got the full-speed pin a portal unit gets at prep.
            # Add it now that the port is about to become a host.
            if (
                image_device_profile() in _USB_MIC_PROFILES
                and not any(t.startswith("dwc_otg.speed=") for t in tokens)
            ):
                tokens.append(_USB_FULLSPEED_TOKEN)
            rebuilt = " ".join(tokens) + "\n"
            if rebuilt != raw:
                # A malformed cmdline can stop the Pi booting — one line, no
                # stray newline, exactly as the firmware expects.
                boot_write.write_text(cmdline, rebuilt)
                changed.append(str(cmdline))
        except OSError as e:
            log.warning("could not revert %s: %s", cmdline, e)
        break   # the first boot dir that exists is the real one
    if changed:
        log.info("USB gadget mode reverted in %s", ", ".join(changed))
    return changed


# ─── Transport seam ───────────────────────────────────────────────────────
#
# How a satellite is reachable during adoption is the ONLY thing that varies
# between onboarding routes: the USB gadget (bench + manufacturing) and the
# Wi-Fi setup portal (shipped units) carry an identical protocol. `run()`
# below is written against this seam and never learns which it has.


class Transport(Protocol):
    """Publish device-info, receive a provision payload, go away again."""

    def expose(self, device_info: dict[str, Any]) -> None:
        """Publish ``device_info`` and become reachable to an adopter."""

    def wait_for_provision(self, nonce: str) -> dict[str, Any] | None:
        """Block until a payload validates against ``nonce``. None only when
        a bounded test loop gives up."""

    def withdraw(self) -> None:
        """Stop being reachable, without discarding anything."""

    def clear_provision(self) -> None:
        """Ensure the credentials can't be read back. The gadget deletes the
        file from its image; the portal no-ops, having never written one."""

    def dispose(self) -> None:
        """Final cleanup once provisioning has succeeded."""

    def reboot(self) -> None:
        """Reboot into the provisioned client."""


class GadgetTransport:
    """The USB mass-storage route: wraps a :class:`GadgetBackend` and the
    backing image so the backend itself stays exactly as it was (and every
    existing fake keeps working)."""

    def __init__(
        self,
        backend: GadgetBackend,
        image: Path,
        *,
        poll_sec: float = 2.0,
        max_loops: int | None = None,
    ) -> None:
        self.backend = backend
        self.image = image
        self.poll_sec = poll_sec
        self.max_loops = max_loops

    def expose(self, device_info: dict[str, Any]) -> None:
        # Rebuild + rebind so the host sees a clean re-plug; the image is
        # never loop-mounted while bound (host + device on one FAT corrupts).
        self.backend.unbind()
        self.backend.build_image(self.image, device_info)
        self.backend.bind(self.image)

    def wait_for_provision(self, nonce: str) -> dict[str, Any] | None:
        return _poll_for_provision(
            self.backend, self.image, nonce, self.poll_sec,
            max_loops=self.max_loops,
        )

    def withdraw(self) -> None:
        self.backend.unbind()

    def clear_provision(self) -> None:
        self.backend.delete_file(self.image, proto.PROVISION_NAME)

    def dispose(self) -> None:
        self.image.unlink(missing_ok=True)
        # Adoption is over, so the USB controller no longer needs to be a
        # peripheral — and leaving it pinned means a USB mic array can never
        # enumerate on a single-data-port Pi. Portal units never get the
        # overlay in the first place; this is the USB route's equivalent.
        revert_usb_gadget_boot_config()

    def reboot(self) -> None:
        self.backend.reboot()


def _default_transport() -> GadgetBackend | Transport:
    """Pick the onboarding transport this image was built for. Baked AP
    credentials mean the Wi-Fi setup portal; their absence means the USB
    mass-storage gadget, which stays the bench and manufacturing route."""
    creds = portal_credentials()
    if creds is None:
        return GadgetBackend()
    from satellite.portal_transport import PortalTransport

    profile = image_device_profile()
    return PortalTransport(
        ap_ssid=creds["ssid"],
        ap_psk=creds["psk"],
        device_profile=profile,
        sat_type=image_sat_type(),
        # ONLY the profile this image was built for. Offering the whole
        # catalogue put a Radxa video board at the top of the form on a Pi
        # with a USB mic array — the list is sorted and nothing was
        # selected. A prepared card knows its own hardware, so asking the
        # customer is both pointless and a way to get it wrong.
        profiles=[profile],
        radio={k: creds[k] for k in ("channel", "pmf") if k in creds},
    )
    set_join_pin(creds.get("join_bssid"), creds.get("join_band"))


# ─── Main loop ────────────────────────────────────────────────────────────


def run(
    backend: GadgetBackend | Transport | None = None,
    *,
    image: Path | None = None,
    poll_sec: float = 2.0,
    wifi_attempts: int = 3,
    wifi_join_timeout: float = 75.0,
    max_loops: int | None = None,
    after_setup=None,
) -> int:
    """The provisioning state machine. Returns an exit code (0 = nothing to
    do or provisioned successfully). ``max_loops`` bounds the poll loop for
    tests; None = poll until provisioned or killed. ``image`` resolves to
    the module's IMAGE_FILE at CALL time (monkeypatch-friendly).
    ``after_setup(room_id, when)`` runs once setup has succeeded, before the
    reboot (default :func:`apply_network_settings`).

    The first argument is a :class:`Transport`. A bare
    :class:`GadgetBackend` is still accepted and wrapped, so existing
    callers and fakes keep working unchanged."""
    if image is None:
        image = IMAGE_FILE
    if backend is None:
        backend = _default_transport()
    transport: Transport = (
        GadgetTransport(backend, image, poll_sec=poll_sec, max_loops=max_loops)
        if isinstance(backend, GadgetBackend)
        else backend
    )
    if is_provisioned():
        log.info("already provisioned — nothing to do")
        return 0

    state = _read_state()
    status = "wifi_failed" if state.get("phase") == "wifi_failed" else "awaiting_provision"
    error = state.get("error")

    board, model = read_board()
    loops = 0
    failures = 0
    while True:
        # Fresh nonce per gadget session: a stale provision.json from a
        # previous plug can never validate against it.
        nonce = secrets.token_hex(8)
        info = proto.build_device_info(
            nonce=nonce,
            mac=read_wlan_mac(),
            board=board,
            model=model,
            sat_type=image_sat_type(),
            status=status,
            error=error,
            profiles_supported=_profiles(),
        )
        # So the card never shows a previous boot's failure as current
        # while this attempt is still waiting for the radio. Once per run of
        # failures, not per retry: a retry loop would otherwise rewrite the
        # card (FAT, no journal) twice a turn, forever, with the "failed"
        # report it is about to repeat already there.
        if failures == 0:
            _report(transport, "starting",
                    top={"status": status, "step": "setup-network-starting",
                         "error": _card_error(status, error)},
                    error=_card_error(status, error), attempts=1)
        try:
            transport.expose(info)
        except Exception as e:  # noqa: BLE001 — reported, then retried or parked
            permanent = bool(getattr(e, "permanent", False))
            failures += 1
            _setup_network_failed(transport, e, permanent=permanent,
                                  status=status, attempt=failures)
            if permanent:
                # Nothing a retry can change (a package the card lacks): say
                # so once and stay down. Exiting with this code is what the
                # unit's RestartPreventExitStatus= recognises, so systemd
                # doesn't restart it into the same failure every 5 s.
                return EXIT_CANNOT_SET_UP
            loops += 1
            if max_loops is not None and loops >= max_loops:
                return 1
            # In-process and slowing down, rather than crashing for systemd
            # to restart: every restart raised and dropped half a hotspot.
            time.sleep(min(_EXPOSE_RETRY_MAX_SEC,
                           _EXPOSE_RETRY_SEC * 2 ** (failures - 1)))
            continue
        failures = 0
        # The portal is live. This is the one moment a customer is actively
        # waiting on the device itself for a cue.
        setup_status("join-failed" if status == "wifi_failed" else "ready-to-setup")
        _write_state({"phase": status, "error": error, "nonce": nonce})
        log.info("setup exposed (status=%s nonce=%s) — waiting for adopt", status, nonce)

        payload = transport.wait_for_provision(nonce)
        if payload is None:
            # Only reachable with max_loops (tests) — a real device waits on.
            return 1
        # Credentials accepted. The AP goes down here, so the customer's
        # phone drops the setup network and the device is their only signal.
        setup_status("joining")
        transport.withdraw()
        _report(transport, "closed", top={"step": "joining-home-wifi", "error": None})
        ok, err = apply_provision(
            payload,
            transport=transport,
            wifi_attempts=wifi_attempts,
            wifi_join_timeout=wifi_join_timeout,
            before_join=name_before_join,
        )
        if ok:
            _write_state({"phase": "done"})
            _report(None, None, top={"status": "provisioned",
                                     "step": "joined-home-wifi", "error": None})
            # Held briefly so success is seen rather than inferred from the
            # reboot that follows it.
            setup_status("on-network")
            transport.dispose()
            # The network settings every satellite carries from now on (no
            # setup hotspot profile, power saving off, the room's host
            # name), applied before the first boot as a satellite so a new
            # card has them by default. Every later boot re-checks them.
            (after_setup or apply_network_settings)(payload["room_id"], "setup")
            log.info("provisioned as %r — rebooting", payload["room_id"])
            transport.reboot()
            return 0
        # Wi-Fi failed: strip the half-applied config (the client must not
        # start against a network we never joined), then re-present with
        # the error so the dashboard can show it and a force-adopt retries.
        CONFIG_PATH.unlink(missing_ok=True)
        PAIRING_TOKEN_PATH.unlink(missing_ok=True)
        status, error = "wifi_failed", err
        # The named reason (it carries the house network's name) goes to
        # the portal page and the journal; the card gets generic words.
        # What NetworkManager itself said about the join, scrubbed of the
        # network's name, the password and hardware addresses: the one
        # place a household with no keyboard can read it (pull the card,
        # open domovoi/device-info.json).
        _report(None, None, top={"status": status, "step": "home-wifi-failed",
                                 "error": _card_error(status, err),
                                 "join_log": list(LAST_JOIN_LOG),
                                 **JOIN_DIAG.card()})
        loops += 1
        if max_loops is not None and loops >= max_loops:
            return 1


def _card_error(status: str, error: str | None) -> str | None:
    try:
        from satellite import setup_report

        return setup_report.card_error(status, error)
    except Exception:  # noqa: BLE001 — generic words rather than none
        return "couldn't join your home Wi-Fi" if error else None


def _report(transport, state: str | None, *, top: dict[str, Any] | None = None,
            error: str | None = None, **extra: Any) -> None:
    """Update the card's device-info.json (satellite/setup_report.py): the
    top-level status stage 1 started and, for the setup portal, its
    ``setup_network`` block. Best-effort: never stops setup."""
    try:
        from satellite import setup_report

        network = None
        if state and hasattr(transport, "network_report"):
            network = transport.network_report(state, error)
            network.update(extra)
        setup_report.update(top=top, network=network)
    except Exception as e:  # noqa: BLE001
        log.warning("could not update the card's setup report: %s", e)


def _setup_network_failed(transport, exc: BaseException, *, permanent: bool,
                          status: str, attempt: int) -> None:
    """The setup network (or the gadget) would not start: red on the ring,
    the reason on the card, and nothing left half up."""
    msg = str(exc) or type(exc).__name__
    log.error("setup could not be exposed (attempt %d%s): %s", attempt,
              ", will not retry" if permanent else ", retrying", msg)
    setup_status("startup-failed")
    _report(
        transport, "unavailable" if permanent else "failed",
        top={"status": status,
             "step": "setup-network-unavailable" if permanent else "setup-network-failed",
             "error": msg},
        error=msg, attempts=attempt,
    )
    try:
        transport.withdraw()
    except Exception as e:  # noqa: BLE001 — it is already down or never came up
        log.debug("withdraw after a failed expose: %s", e)


def _profiles() -> list[str]:
    try:
        from satellite import devices

        return sorted(devices.PROFILES)
    except Exception:
        return []


def _poll_for_provision(
    backend: GadgetBackend,
    image: Path,
    nonce: str,
    poll_sec: float,
    max_loops: int | None = None,
) -> dict[str, Any] | None:
    """Poll the backing image for a provision file; a candidate must parse,
    validate (nonce + checksum), AND read back byte-identical one poll
    later (the two-stable-reads rule) before it's accepted."""
    previous: bytes | None = None
    rejected: bytes | None = None
    loops = 0
    while True:
        time.sleep(poll_sec)
        raw = backend.read_file(image, proto.PROVISION_NAME)
        if raw is not None and raw != rejected:
            if previous is not None and raw == previous:
                try:
                    return proto.validate_provision(
                        json.loads(raw.decode("utf-8")), nonce
                    )
                except (ValueError, proto.ProvisionInvalid) as e:
                    # Warn once per distinct content — a stale file that
                    # never changes shouldn't spam the journal.
                    log.warning("rejecting provision file: %s", e)
                    rejected = raw
                    previous = None
            else:
                previous = raw
        elif raw is None:
            previous = None
        loops += 1
        if max_loops is not None and loops >= max_loops:
            return None


def name_before_join(room_id: str | None) -> None:
    """The room's host name (satellite/net_settings.py), set before setup
    joins the house Wi-Fi, so the router's very first DHCP request from
    this unit already carries it. Root on a satellite only, like
    :func:`apply_network_settings`; never raises."""
    geteuid = getattr(os, "geteuid", None)
    if sys.platform != "linux" or geteuid is None or geteuid() != 0:
        return
    try:
        from satellite import net_settings

        net_settings.set_hostname(room_id, subprocess.run, net_settings.Report(),
                                  restart_avahi=False)
    except Exception as e:  # noqa: BLE001 — setup goes on with the old name
        log.warning("host name before joining the Wi-Fi: %s", e)


def apply_network_settings(room_id: str | None, when: str) -> dict[str, Any] | None:
    """satellite/net_settings.py, as root on the satellite itself — and
    nowhere else: on a developer's machine or in a test this is a no-op, so
    nothing here can rename a laptop or delete its Wi-Fi profiles. Never
    raises."""
    geteuid = getattr(os, "geteuid", None)
    if sys.platform != "linux" or geteuid is None or geteuid() != 0:
        log.info("network settings: not root on a satellite; skipped")
        return None
    try:
        from satellite import net_settings

        # At boot, root's record of the server's address also follows a
        # move the client made — after root checks the new address itself
        # (net_settings.refresh_server_pin). At setup it was just written.
        return net_settings.apply(
            room_id, when=when,
            server_pin=net_settings.refresh_server_pin if when == "boot" else None,
        )
    except Exception as e:  # noqa: BLE001 — never take setup or boot down
        log.warning("network settings could not be applied: %s", e)
        return None


def apply_base_packages() -> dict[str, Any] | None:
    """satellite/base_packages.py, as root on the satellite itself; a no-op
    anywhere else, like :func:`apply_network_settings`. Never raises."""
    geteuid = getattr(os, "geteuid", None)
    if sys.platform != "linux" or geteuid is None or geteuid() != 0:
        log.info("base packages: not root on a satellite; skipped")
        return None
    try:
        from satellite import base_packages

        return base_packages.repair()
    except Exception as e:  # noqa: BLE001 — never take the boot down
        log.warning("base packages could not be checked: %s", e)
        return None


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    if sys.platform != "linux":
        log.error("provisioning mode only runs on the satellite itself")
        return 2
    if is_provisioned():
        # Every provisioned boot: the network settings, checked and applied
        # where missing (satellite/net_settings.py). This is how a satellite
        # set up before they existed gets them — the upgrade brings this
        # code, the next boot runs it.
        from satellite import net_settings

        apply_network_settings(net_settings.read_room_id(CONFIG_PATH), "boot")
        # Then the base system packages (satellite/base_packages.py): a card
        # whose setup lost one (mpg123 on the office unit, 2026-10-17) gets it
        # back at the next boot, online. Nothing runs when none is missing.
        apply_base_packages()
    return run()


if __name__ == "__main__":
    sys.exit(main())
