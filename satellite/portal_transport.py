"""Wi-Fi setup portal — the transport shipped units onboard over.

The satellite raises its own WPA2 access point, serves a captive portal on
it, and waits for a customer's phone to post their house credentials. It
implements the same :class:`~satellite.provisioning_mode.Transport` seam as
the USB gadget, so the state machine, the protocol, the validation and the
``wifi_failed`` retry are all shared — only the pipe differs.

Why a portal rather than the USB gadget, for a device someone buys:

* No cable to the core box, no SD card handling, no tools.
* On a single-data-port Pi the gadget needs ``dr_mode=peripheral``, which
  is mutually exclusive with a USB microphone array on that same port.
* The credentials travel between two devices the customer is holding.

WPA2 on the setup AP is not optional. Captive-portal detection works fine
on a secured network, and HTTPS is not usable here — a self-signed
certificate produces a full-page security warning at exactly the moment
someone is about to type their Wi-Fi password. WPA2 encrypts the link; the
printed per-device key is what makes it worth encrypting.

Stdlib only, like everything else provisioning mode imports.
"""

from __future__ import annotations

import json
import logging
import queue
import secrets
import shutil
import subprocess
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from satellite import portal_pages
from satellite import provisioning_protocol as proto

log = logging.getLogger(__name__)

PORTAL_IP = "192.168.4.1"
PORTAL_PORT = 80
# Bind every interface rather than PORTAL_IP. Binding a specific address
# races the AP's address assignment — the socket is created microseconds
# after nmcli returns, and if the address isn't up yet bind() fails with
# EADDRNOTAVAIL and takes the whole service down. An unprovisioned device
# has no network but the setup AP and loopback, so there is nothing else to
# be exposed on.
BIND_HOST = "0.0.0.0"
AP_CONNECTION = "domovoi-setup"

# ── The setup network's radio settings ──────────────────────────────────
#
# Found on hardware (2026-10-17): a Samsung phone said "Couldn't connect to
# network" to a Pi's setup hotspot every time, with the right password,
# while a Motorola joined the same network at once (and the same Samsung
# had set up every earlier satellite, in September). `nmcli device wifi
# hotspot` leaves Protected Management Frames at NM's default, and for a
# key-mgmt=wpa-psk access point NetworkManager (1.38 on) then runs
# WPA2/WPA3 "transition mode": WPA-PSK plus SAE, PMF optional, whenever
# wpa_supplicant and the driver report SAE, PMF and BIP support
# (src/core/supplicant/nm-supplicant-config.c). Phones that pick WPA2 join;
# phones that pick WPA3 when it is offered - Samsung's do - negotiate SAE
# and PMF, which the Pi's Wi-Fi driver (brcmfmac) breaks in access-point
# mode. So whether a phone could join depended on what the Pi's driver
# happened to advertise and what the phone happened to prefer.
#
# PMF off is the standard workaround, and NM drops SAE with it (it never
# runs transition mode with PMF disabled): the setup network is plain
# WPA2-PSK with CCMP, which is exactly what the phones that already worked
# were using. WPA2 still encrypts the link.
#
# The band and channel are pinned for the same reason: nothing left for the
# driver to choose. 2.4 GHz, as before (NM picked a 2.4 GHz channel from
# 1/6/11/13 by hashing the SSID), because every phone has it and the
# Pi Zero 2 W has nothing else; channels 1, 6 and 11 only, because those
# are the three that don't overlap AND are legal in every regulatory
# domain, the "world" domain 00 included (12 and 13 are not legal in the
# US, 14 almost nowhere) - so whatever wireless country the card was
# prepared with, the channel is allowed.
AP_BAND = "bg"
AP_CHANNELS = (6, 1, 11)       # tie-break order: 6 first
AP_PMF = "disable"
# What `ap.json` on the boot partition may change, for testing a radio on
# real hardware without rebuilding the card (key -> allowed values).
# Anything else in it is ignored, and a value outside these never reaches
# nmcli.
RADIO_OVERRIDES = {
    "channel": ("auto", "1", "6", "11"),
    "pmf": ("disable", "optional", "default"),
}

# What NetworkManager's shared mode needs to run a hotspot. Missing any of
# these, the hotspot can never come up, however often it is retried — and
# retrying is what made a card built without its offline packages flap its
# setup network up and down forever. Name the package, not just the file:
# the person reading this is choosing what to rebuild.
PREREQUISITES = (
    ("nmcli", "network-manager"),
    ("dnsmasq", "dnsmasq-base"),        # shared mode's DHCP + DNS
    ("wpa_supplicant", "wpasupplicant"),  # NM runs the AP through it
)
# A systemd unit's PATH has these, but shutil.which() alone depends on the
# caller's environment.
_SBIN_DIRS = ("/usr/sbin", "/sbin", "/usr/local/sbin")

# NetworkManager's dnsmasq lease file for the hotspot: one line per phone
# that got an address, which a phone only does AFTER the Wi-Fi handshake
# succeeded. So "phones that joined" counts leases, never association
# attempts — exactly the number that reads 0 when the handshake is broken.
LEASES_DIR = Path("/var/lib/NetworkManager")
_REPORT_EVERY_SEC = 30.0


class MissingPrerequisite(RuntimeError):
    """The hotspot can never start on this card. Retrying won't help."""

    permanent = True


def _have(binary: str) -> bool:
    if shutil.which(binary):
        return True
    return any(Path(d, binary).exists() for d in _SBIN_DIRS)


def check_prerequisites() -> None:
    """Raise :class:`MissingPrerequisite` naming every package the setup
    hotspot needs and this card lacks."""
    missing = [pkg for binary, pkg in PREREQUISITES if not _have(binary)]
    if missing:
        raise MissingPrerequisite(
            f"this card is missing {', '.join(missing)}, which the setup "
            "network needs. Flash stock Raspberry Pi OS Lite and prepare the "
            "card again (with offline packages: the server needs Docker and "
            "internet access while it prepares the card)."
        )


def radio_settings(overrides: dict[str, Any] | None = None) -> dict[str, str]:
    """The band / channel / PMF the hotspot is raised with: the defaults
    above, then any allowed override from ``ap.json``. ``channel`` is
    ``"auto"`` until :func:`choose_channel` has had a scan to look at."""
    out = {"band": AP_BAND, "channel": "auto", "pmf": AP_PMF}
    for key, allowed in RADIO_OVERRIDES.items():
        value = (overrides or {}).get(key)
        if value is None:
            continue
        value = str(value).strip().lower()
        if value in allowed:
            out[key] = value
        else:
            log.warning("ignoring ap.json %s=%r (allowed: %s)",
                        key, value, ", ".join(allowed))
    return out


def choose_channel(rows: list[dict[str, Any]]) -> int:
    """The least crowded of 1, 6 and 11, from the scan taken before the
    radio starts hosting. Each network seen costs the channels it overlaps
    (within four of its own) by its signal strength; 5 GHz networks cost
    nothing. A scan with no channels in it gives 6."""
    cost = {c: 0 for c in AP_CHANNELS}
    for row in rows:
        try:
            chan = int(row.get("chan") or 0)
            signal = int(row.get("signal") or 0)
        except (TypeError, ValueError):
            continue
        if not 1 <= chan <= 14:
            continue
        for c in cost:
            if abs(c - chan) < 5:
                cost[c] += max(signal, 1)
    return min(AP_CHANNELS, key=lambda c: cost[c])

# ── The approval code ────────────────────────────────────────────────────
#
# The customer reads this off the portal page (and hears it from the
# device) and types it into the dashboard, where the core compares it
# before it binds the room to this device. Six digits, because the code is
# now a credential rather than a label: four gave one chance in ten
# thousand per guess, six gives one in a million, and the server's attempt
# limit does the rest. Digits only, and still short enough to read aloud
# and type on a phone in one go.
#
# The core has the same constant (domovoi/satellite_media/overlay.py,
# beside the setup-AP key it reasons about the same way) for the codes it
# mints itself; this tree ships standalone on the Pi and cannot import it.
APPROVAL_CODE_DIGITS = 6


def generate_approval_code() -> str:
    """A fresh six-digit approval code, zero-padded so every code is the
    same length (a leading zero is a digit the customer must type, not one
    the format may drop)."""
    return f"{secrets.randbelow(10 ** APPROVAL_CODE_DIGITS):0{APPROVAL_CODE_DIGITS}d}"

# The connectivity-check URLs each OS probes right after associating. We
# answer all of them with a redirect, which is what makes the sign-in sheet
# open by itself. Matched on path — wildcard DNS is what brings the
# hostnames here in the first place.
PROBE_PATHS = (
    "/hotspot-detect.html",          # iOS / macOS
    "/library/test/success.html",    # older iOS
    "/generate_204",                 # Android
    "/gen_204",                      # Android (alternate)
    "/connecttest.txt",              # Windows
    "/ncsi.txt",                     # Windows (legacy)
    "/success.txt",                  # Firefox
    "/canonical.html",               # Ubuntu / GNOME
)

# The most a credentials form can legitimately weigh: a 32-byte SSID, a
# 63-byte passphrase, a room name and a server address, URL-encoded, is
# well under a kilobyte. Anything larger is refused before it is read -
# this server runs as root, and a body is memory.
MAX_POST_BYTES = 8192

_SCAN_TIMEOUT_SEC = 20.0
# A wireless device that has only just appeared reports itself ready before
# it can actually survey, and the first rescan comes back empty. Ask again
# rather than telling the customer there are no networks.
_SCAN_ATTEMPTS = 3
_SCAN_RETRY_SEC = 2.0
# The wireless device can lag the service at boot.
_IFACE_WAIT_SEC = 30.0
_IFACE_POLL_SEC = 1.0
_NMCLI_TIMEOUT_SEC = 30.0


def _no_iface() -> str:
    return (f"no Wi-Fi interface after {_IFACE_WAIT_SEC:.0f}s — is the "
            "radio rfkill-blocked for want of a wireless country?")


def _nmcli() -> str | None:
    return shutil.which("nmcli")


def scan_networks(run=subprocess.run, *, iface: str | None = None,
                  attempts: int | None = None) -> list[str]:
    """Visible SSIDs, strongest first. Best-effort: a failed scan yields an
    empty list and the form falls back to a free-text field rather than
    dead-ending the customer.

    Call this only once a Wi-Fi interface exists. Scanning before the device
    is up returns nothing at all, and "no networks found" on a form served
    by a working access point is a confusing thing to hand someone.
    """
    return portal_pages.summarize_networks(
        scan_rows(run=run, iface=iface, attempts=attempts))


def scan_rows(run=subprocess.run, *, iface: str | None = None,
              attempts: int | None = None) -> list[dict[str, Any]]:
    """One row per network seen: ``ssid``, ``signal`` and, where nmcli
    gave it, ``chan`` (what :func:`choose_channel` reads). Same
    best-effort rules as :func:`scan_networks`."""
    nmcli = _nmcli()
    if nmcli is None:
        return []
    attempts = _SCAN_ATTEMPTS if attempts is None else attempts
    cmd = [nmcli, "-t", "-f", "SSID,SIGNAL,CHAN", "device", "wifi", "list",
           "--rescan", "yes"]
    if iface:
        cmd += ["ifname", iface]
    proc = None
    for attempt in range(1, attempts + 1):
        try:
            proc = run(cmd, capture_output=True, text=True,
                       timeout=_SCAN_TIMEOUT_SEC, check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            log.warning("wifi scan attempt %d failed: %s", attempt, e)
            proc = None
        else:
            if proc.returncode != 0:
                # Silence here is how a scan that never worked looked
                # identical to a neighbourhood with no Wi-Fi in it.
                log.warning(
                    "wifi scan attempt %d exited %d: %s",
                    attempt, proc.returncode, (proc.stderr or "").strip(),
                )
                proc = None
            elif (proc.stdout or "").strip():
                break
            else:
                log.info("wifi scan attempt %d saw nothing — retrying", attempt)
        if attempt < attempts:
            time.sleep(_SCAN_RETRY_SEC)
    if proc is None or proc.returncode != 0:
        return []
    rows: list[dict[str, Any]] = []
    for line in (proc.stdout or "").splitlines():
        # -t output is colon-separated with backslash-escaped colons in
        # values; an SSID may legitimately contain one.
        parts = line.replace("\\:", "\x00").split(":")
        if len(parts) < 2:
            continue
        row = {"ssid": parts[0].replace("\x00", ":"), "signal": parts[1]}
        if len(parts) >= 3:
            row["chan"] = parts[2]
        rows.append(row)
    return rows


class PortalTransport:
    """Soft AP + captive portal implementing the provisioning Transport."""

    def __init__(
        self,
        *,
        ap_ssid: str,
        ap_psk: str,
        device_profile: str,
        sat_type: str = "voice",
        iface: str | None = None,
        ip: str = PORTAL_IP,
        port: int = PORTAL_PORT,
        bind_host: str = BIND_HOST,
        profiles: list[str] | None = None,
        state_dir: Path | None = None,
        radio: dict[str, Any] | None = None,
        run=subprocess.run,
        server_factory=None,
    ) -> None:
        self.ap_ssid = ap_ssid
        self.ap_psk = ap_psk
        self.device_profile = device_profile
        self.sat_type = sat_type
        self.iface = iface
        self.ip = ip            # advertised: DNS target and redirect URL
        self.port = port
        self.bind_host = bind_host
        self.profiles = profiles or [device_profile]
        self.state_dir = state_dir or Path("~/.domovoi").expanduser()
        self.run = run
        self._server_factory = server_factory or ThreadingHTTPServer

        self.device_info: dict[str, Any] = {}
        self.nonce: str = ""
        self.networks: list[str] = []
        self.approval_code: str = ""
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        self._server: Any = None
        self._thread: threading.Thread | None = None
        self._ap_up = False
        self.phone_seen = False
        # The last form submitted (portal_pages.render_form's keep-fields),
        # in memory only and never the password.
        self.last_choice: dict[str, Any] | None = None
        # Requested settings (ap.json overrides applied); `channel` becomes
        # a number once the scan has picked one.
        self.radio = radio_settings(radio)
        self.channel: int | None = None
        # Phones that got an address from the hotspot (lease MACs, kept in
        # memory only — the card gets the count).
        self._phones: set[str] = set()
        self._watch_stop = threading.Event()
        self._watcher: threading.Thread | None = None
        self._ap_iface: str | None = None

    # ── Transport seam ───────────────────────────────────────────────────

    def expose(self, device_info: dict[str, Any]) -> None:
        self.device_info = device_info
        self.nonce = device_info["nonce"]
        # Before anything else: a card that cannot run a hotspot at all
        # must say so once and stop, not raise half an AP every few seconds.
        check_prerequisites()
        # A fresh code per session, for the same reason as the nonce: an
        # approval shown in a previous attempt must not still be valid.
        self.approval_code = generate_approval_code()
        # Wait for the radio BEFORE surveying with it. This service can win
        # the race against the wireless device at boot, and a scan issued
        # then finds no device, returns nothing, and leaves the form saying
        # "no networks found" on a device whose access point then comes up
        # perfectly — the wait used to live inside _start_ap(), which is
        # after this point, so the AP survived the race and the scan didn't.
        iface = self.wait_for_wifi_interface()
        if iface is None:
            # Said now, after the one wait: scanning without a radio and
            # then waiting again in _start_ap doubled the time to the error
            # (seen in a container with no Wi-Fi device, 2026-10-17).
            raise RuntimeError(_no_iface())
        # Scan BEFORE the radio is committed to hosting — once the AP is up
        # the interface can no longer survey the neighbourhood. The same
        # scan picks the quietest channel.
        rows = scan_rows(run=self.run, iface=iface)
        self.networks = portal_pages.summarize_networks(rows)
        self.channel = (
            int(self.radio["channel"]) if self.radio["channel"] != "auto"
            else choose_channel(rows)
        )
        self._persist_approval_code()
        # Serve BEFORE the radio exists, not after. A phone probes for a
        # captive portal the instant it associates, and a probe that finds
        # nothing listening is recorded as "connected, no internet" with no
        # sign-in offer at all — a verdict the phone then CACHES against that
        # SSID, so the prompt never appears again on that device. Listening
        # first means the very first probe is always answered.
        #
        # Possible only because the socket binds 0.0.0.0: it needs no address
        # from an interface that doesn't exist yet.
        self._start_server()
        try:
            self._start_ap(iface=iface)
        except Exception:
            self._stop_server()      # don't leave a socket behind on failure
            raise
        log.info(
            "setup portal up: ssid=%s channel=%s band=%s pmf=%s "
            "http://%s:%d (%d networks visible)",
            self.ap_ssid, self.channel, self.radio["band"], self.radio["pmf"],
            self.ip, self.port, len(self.networks),
        )
        self._report_up()
        self._start_watcher()

    def wait_for_provision(self, nonce: str) -> dict[str, Any] | None:
        """Block until the portal handler accepts a payload. No polling and
        no two-stable-reads dance — that rule exists because FAT write
        caching makes torn reads inevitable, and HTTP delivers a whole
        request or none."""
        payload = self._queue.get()
        return payload

    def withdraw(self) -> None:
        self._stop_watcher()
        self._stop_server()
        self._stop_ap()

    def clear_provision(self) -> None:
        """No-op with a real purpose: the payload only ever existed in this
        process's memory, so there is no file to shred. Drop the reference
        so a re-exposed session can't serve it back."""
        with self._queue.mutex:
            self._queue.queue.clear()

    def dispose(self) -> None:
        """Setup succeeded: the hotspot goes, and so does its PROFILE.

        Taking it down is not enough. A saved access-point profile is one
        NetworkManager may pick whenever the house network is out of sight
        — an AP is always "available" — and a satellite that lost its
        router for a minute then sat broadcasting its setup network for
        weeks (2026-10-12). The boot-time step deletes it too
        (satellite/net_settings.py), which is what reaches a unit set up
        before this."""
        self.withdraw()
        try:
            self._nmcli(["connection", "delete", "id", AP_CONNECTION],
                        "deleting the setup AP profile")
            log.info("setup AP profile %s deleted", AP_CONNECTION)
        except RuntimeError as e:
            log.warning("%s", e)

    def reboot(self) -> None:
        self.run(["systemctl", "--no-block", "reboot"], timeout=60)

    def setup_error(self) -> str | None:
        """Why the last attempt failed, in words for the person retrying.

        The state machine re-raises the AP after a failed join with the
        reason in device_info — but nothing showed it, so the customer
        rejoined to a blank form and had no idea the password was wrong.
        Silence here is the difference between "try again" and "this thing
        is broken".
        """
        status = (self.device_info or {}).get("status")
        detail = ((self.device_info or {}).get("error") or "").strip()
        if status == "wifi_failed":
            # The reason's own words: only a password that was refused is
            # called a password problem (2026-10-22: every failure said
            # "Check the password", the real one included a WPA3 router).
            from satellite import wifi_reasons

            kind = wifi_reasons.kind_of(detail) or wifi_reasons.classify(detail)
            msg = wifi_reasons.PORTAL[kind]
            if wifi_reasons.kind_of(detail):
                nm = wifi_reasons.detail_of(detail)
                return f"{msg} (Details: {nm})" if nm else msg
            return f"{msg} ({detail})" if detail else msg
        if status == "room_taken":
            return "That room name is already in use. Pick a different one."
        return None

    def _persist_approval_code(self) -> None:
        """The code outlives this process: the portal shows it, the reboot
        happens, and the client presents it on connect so the dashboard can
        ask the customer to match what they saw."""
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            path = self.state_dir / "approval_code"
            path.write_text(self.approval_code + "\n", encoding="utf-8")
            # Written by root, read by the satellite client — see
            # provisioning_mode.give_to_satellite_user.
            from satellite.provisioning_mode import give_to_satellite_user

            give_to_satellite_user(self.state_dir)
            give_to_satellite_user(path)
            path.chmod(0o600)
        except OSError as e:
            # Not fatal: the satellite still adopts, the dashboard just has
            # nothing to match against and falls back to plain approval.
            log.warning("could not persist the approval code: %s", e)

    # ── AP control ───────────────────────────────────────────────────────

    def _nmcli(self, args: list[str], what: str) -> subprocess.CompletedProcess:
        """Run one nmcli command and RAISE on failure.

        Every call here used to be fire-and-forget. A hotspot that failed to
        come up looked exactly like one that worked: the service went on to
        bind its socket, reported "setup portal up", and waited forever for
        a phone that had nothing to join. Failing loudly means systemd
        restarts us and the journal says why.

        The command is never included in the message — the PSK is in argv.
        """
        nmcli = _nmcli()
        if nmcli is None:
            raise RuntimeError(
                "nmcli not found — the setup portal needs NetworkManager"
            )
        try:
            proc = self.run(
                [nmcli, *args], capture_output=True, text=True,
                timeout=_NMCLI_TIMEOUT_SEC, check=False,
            )
        except subprocess.TimeoutExpired as e:
            # Not str(e): a TimeoutExpired prints the whole command, PSK
            # included, and this message reaches the journal and the card.
            raise RuntimeError(
                f"{what} timed out after {_NMCLI_TIMEOUT_SEC:.0f}s") from e
        except OSError as e:
            raise RuntimeError(f"{what} failed: {e}") from e
        if proc.returncode != 0:
            # nmcli's own words, through the same scrubber as a home-Wi-Fi
            # join's (provisioning_mode._nmcli_detail): this text is
            # written onto the card, which anyone holding it can read.
            from satellite.provisioning_mode import _nmcli_detail

            raise RuntimeError(
                f"{what} failed (rc={proc.returncode}): "
                f"{_nmcli_detail(proc, self.ap_psk)}"
            )
        return proc

    def wifi_interface(self) -> str | None:
        """The first real Wi-Fi interface.

        ``p2p-dev-*`` is a virtual companion device that cannot host an
        access point, and it sits right next to wlan0 in nmcli's list. Left
        to choose for itself, nmcli can land on the wrong one — so name the
        interface explicitly rather than hoping.
        """
        if self.iface:
            return self.iface
        try:
            proc = self._nmcli(
                ["-t", "-f", "DEVICE,TYPE", "device", "status"],
                "listing wifi interfaces",
            )
        except RuntimeError:
            return None
        for line in (proc.stdout or "").splitlines():
            device, _, kind = line.partition(":")
            if kind.strip() == "wifi" and not device.startswith("p2p-"):
                return device
        return None

    def wait_for_wifi_interface(self, timeout: float | None = None) -> str | None:
        """Poll for a Wi-Fi interface rather than assuming one at boot.

        Observed on hardware: this service wins the race against the
        wireless device becoming ready, fails, and is restarted by systemd
        eight seconds later — during which the setup network doesn't exist.
        Waiting turns a crash-and-retry into a short pause.
        """
        # Resolved at CALL time, not bound as a default: a default argument
        # is evaluated once at import and would make the module constant
        # look tunable while silently ignoring any change to it.
        timeout = _IFACE_WAIT_SEC if timeout is None else timeout
        deadline = time.monotonic() + timeout
        while True:
            iface = self.wifi_interface()
            if iface is not None:
                return iface
            if time.monotonic() >= deadline:
                return None
            log.info("waiting for a Wi-Fi interface to appear...")
            time.sleep(_IFACE_POLL_SEC)

    def _start_ap(self, *, iface: str | None = None) -> None:
        # Already waited for by expose(); re-resolved only when this is
        # driven directly, as the tests do.
        iface = iface or self.wait_for_wifi_interface()
        if iface is None:
            raise RuntimeError(_no_iface())
        # A profile left by an earlier run (a crash, a power cut) would
        # otherwise sit beside the new one under the same name. Absent is
        # the usual case, so failure here means nothing.
        try:
            self._nmcli(["connection", "delete", "id", AP_CONNECTION],
                        "clearing an old setup AP profile")
        except RuntimeError:
            pass
        # ONE profile with every setting in it, then ONE activation. This
        # used to be `nmcli device wifi hotspot` (which activates at once
        # with NM's defaults: PMF optional, any channel, its own subnet),
        # then a modify, then a second `up`: the setup network appeared,
        # vanished, and came back re-addressed — and a phone that joined
        # the first one was dropped.
        self._nmcli(self.ap_profile_args(iface),
                    f"creating the setup AP profile on {iface}")
        # From here there is something to take down again.
        self._ap_up = True
        self._ap_iface = iface
        self._nmcli(["connection", "up", "id", AP_CONNECTION],
                    "activating the setup AP")
        self._verify_ap_active()

    def ap_profile_args(self, iface: str) -> list[str]:
        """The nmcli arguments that create the setup AP's profile.

        The PSK rides in argv, which is root-only readable here, and is
        never logged — same rule as apply_wifi()."""
        channel = self.channel or AP_CHANNELS[0]
        return [
            "connection", "add", "type", "wifi", "ifname", iface,
            "con-name", AP_CONNECTION,
            # Only ever brought up by this process. An AP profile that
            # autoconnects is one NetworkManager may raise whenever the
            # house network is out of sight (see dispose()).
            "connection.autoconnect", "no",
            "802-11-wireless.ssid", self.ap_ssid,
            "802-11-wireless.mode", "ap",
            "802-11-wireless.band", self.radio["band"],
            "802-11-wireless.channel", str(channel),
            # NetworkManager's shared mode picks its own subnet (10.42.x.1
            # by default). The captive-portal DNS drop-in written at
            # prepare time points at a FIXED address, so pin the AP to
            # match — otherwise every hostname resolves to an address
            # nothing is listening on and the sign-in page never opens.
            "ipv4.method", "shared",
            "ipv4.addresses", f"{self.ip}/24",
            "ipv6.method", "ignore",
            # WPA2-PSK with CCMP (AES) only: no WPA1, no TKIP, nothing a
            # phone has to negotiate down from.
            "802-11-wireless-security.key-mgmt", "wpa-psk",
            "802-11-wireless-security.proto", "rsn",
            "802-11-wireless-security.pairwise", "ccmp",
            "802-11-wireless-security.group", "ccmp",
            # See AP_PMF: brcmfmac breaks PMF in AP mode, Samsung phones
            # take it when offered.
            "802-11-wireless-security.pmf", self.radio["pmf"],
            "802-11-wireless-security.psk", self.ap_psk,
        ]

    def _verify_ap_active(self) -> None:
        """Confirm the AP is really there before anyone is told it is.

        nmcli can return 0 and still leave nothing usable behind, and the
        cost of believing it is a device that waits forever for a phone that
        can't see it.
        """
        proc = self._nmcli(["-t", "-f", "NAME", "connection", "show", "--active"],
                           "checking active connections")
        active = [ln.strip() for ln in (proc.stdout or "").splitlines()]
        if AP_CONNECTION not in active:
            raise RuntimeError(
                f"the setup AP is not active after being brought up "
                f"(active connections: {', '.join(active) or 'none'})"
            )
        log.info("setup AP %s active on %s", self.ap_ssid, self.ip)

    def _stop_ap(self) -> None:
        """Best-effort teardown. Unlike bringing it up, a failure here is not
        worth crashing over — the device is about to reboot anyway."""
        if not self._ap_up:
            return
        try:
            self._nmcli(["connection", "down", AP_CONNECTION],
                        "taking the setup AP down")
        except RuntimeError as e:
            log.warning("%s", e)
        self._ap_up = False

    # ── What the card says (satellite/setup_report.py) ───────────────────

    def lease_file(self) -> Path | None:
        iface = self._ap_iface or self.iface
        if not iface:
            return None
        return LEASES_DIR / f"dnsmasq-{iface}.leases"

    def _count_phones(self) -> bool:
        """Fold the hotspot's current DHCP leases into the phones seen this
        session. True when that added one."""
        path = self.lease_file()
        try:
            text = path.read_text(encoding="utf-8", errors="replace") if path else ""
        except OSError:
            return False
        before = len(self._phones)
        for row in text.splitlines():
            # dnsmasq: "<expiry> <mac> <ip> <hostname> <client-id>"
            fields = row.split()
            if len(fields) >= 3:
                self._phones.add(fields[1].lower())
        return len(self._phones) > before

    def network_report(self, state: str = "up", error: str | None = None) -> dict[str, Any]:
        """The ``setup_network`` block for the card. Never the PSK."""
        from satellite import setup_report

        return {
            "state": state,
            "ssid": self.ap_ssid,
            "band": self.radio["band"],
            "channel": self.channel,
            "pmf": self.radio["pmf"],
            "address": self.ip,
            "phones_joined": len(self._phones),
            "page_opened": self.phone_seen,
            "last_error": error,
            "line": setup_report.line(
                state=state, ssid=self.ap_ssid, channel=self.channel,
                phones=len(self._phones), error=error,
            ),
        }

    def _report_up(self) -> None:
        """Best-effort, like everything that writes to the card."""
        try:
            from satellite import setup_report

            self._count_phones()
            info = self.device_info or {}
            # A failed join's reason stays until the next attempt — on the
            # card in generic words: the named version carries the house
            # network's name, which is for the portal page and the journal.
            error = setup_report.card_error(info.get("status"), info.get("error"))
            setup_report.update(
                top={"status": info.get("status") or "awaiting_provision",
                     "step": "setup-network-up",
                     "error": error},
                network=self.network_report("up", error),
            )
        except Exception as e:  # noqa: BLE001 — never take the portal down
            log.warning("could not report the setup network: %s", e)

    def _start_watcher(self) -> None:
        """Re-count phones every so often while the hotspot is up, so a
        phone that joined but never opened the page still shows on the
        card. Writes only when the count changed."""
        self._watch_stop.clear()

        def watch() -> None:
            while not self._watch_stop.wait(_REPORT_EVERY_SEC):
                try:
                    if self._count_phones():
                        self._report_up()
                except Exception as e:  # noqa: BLE001
                    log.debug("phone count failed: %s", e)

        self._watcher = threading.Thread(target=watch, name="domovoi-portal-report",
                                         daemon=True)
        self._watcher.start()

    def _stop_watcher(self) -> None:
        self._watch_stop.set()
        if self._watcher is not None:
            self._watcher.join(timeout=5)
            self._watcher = None

    # ── HTTP server ──────────────────────────────────────────────────────

    def _start_server(self) -> None:
        handler = _make_handler(self)
        self._server = self._server_factory((self.bind_host, self.port), handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="domovoi-portal", daemon=True
        )
        self._thread.start()

    def _stop_server(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    # ── Form handling ────────────────────────────────────────────────────

    def build_payload(self, fields: dict[str, str]) -> dict[str, Any]:
        """Turn submitted form fields into a validated provision payload.

        Raises :class:`proto.ProvisionInvalid` with a message safe to show a
        customer — it must never echo the password back.
        """
        # "Other network" (hidden, or not in the scan) is typed by hand
        # and wins over the picker, like the room.
        custom_ssid = (fields.get("ssid_custom") or "").strip()
        raw_ssid = custom_ssid or (fields.get("ssid") or "").strip()
        # What was chosen, for the form when it comes back after a failed
        # join (the hotspot returns, the page reloads). Never the password.
        self.last_choice = {
            "ssid": raw_ssid or None,
            "room": (fields.get("room") or "").strip() or None,
            "room_custom": (fields.get("room_custom") or "").strip() or None,
            "url": (fields.get("url") or "").strip() or None,
        }
        ssid = proto.validate_wifi_ssid(raw_ssid)
        # Blank is an open network (the join refuses it, in words, if the
        # network turns out to need a password). Anything else must be
        # something WPA can carry - said here, before the hotspot goes.
        psk = proto.validate_wifi_psk(fields.get("psk") or "", allow_open=True)

        # The custom field wins when the picker was set to "something else".
        room_raw = (fields.get("room") or "").strip()
        custom = (fields.get("room_custom") or "").strip()
        if custom:
            room_raw = custom
        if not room_raw:
            raise proto.ProvisionInvalid("Choose a room, or type a name for it.")
        room_id = proto.slugify_room(room_raw)

        profile = (fields.get("profile") or "").strip() or self.device_profile
        if profile not in self.profiles:
            raise proto.ProvisionInvalid("Unknown microphone board.")

        # Blank means "find the server yourself at first start". Anything
        # else has to be a ws:// address on the house LAN: this field is
        # typed by whoever joined the setup network, and the satellite
        # hands its pairing token to whatever it dials.
        url = proto.normalize_server_url(fields.get("url") or "", lan_only=True)

        doc = proto.build_provision(
            nonce=self.nonce,
            room_id=room_id,
            domovoi_url=url,
            sat_type=self.sat_type,
            device_profile=profile,
            # Nobody preseeded this: the core is not a participant in a
            # portal adoption, so first connect is trust-on-first-use and
            # the dashboard approval (approval_code) is what closes it.
            pairing_token=secrets.token_hex(32),
            wifi_ssid=ssid,
            wifi_psk=psk,
        )
        return proto.validate_provision(doc, self.nonce)

    def accept(self, payload: dict[str, Any]) -> None:
        """Hand a validated payload to the waiting state machine. Called
        only AFTER the response has been flushed to the phone — accepting
        tears down the network that phone is reading over."""
        self._queue.put(payload)


def _make_handler(transport: PortalTransport):
    """Handler bound to one transport. A factory rather than attributes on
    the class so two portals can't collide in tests."""

    portal_root = f"http://{transport.ip}/"

    class PortalHandler(BaseHTTPRequestHandler):
        server_version = "domovoi-portal"
        protocol_version = "HTTP/1.1"

        # BaseHTTPRequestHandler logs to stderr; route it to our logger so a
        # journal reader sees one consistent stream.
        #
        # INFO, not DEBUG. This service lives for minutes and serves a
        # handful of requests, so the volume is nil — and it answers the one
        # question that is otherwise unanswerable: did the phone's captive
        # probe actually arrive? "No auto-open" has two completely different
        # causes (the probe never reached us, or our answer was wrong) and
        # they are indistinguishable without this line.
        def log_message(self, fmt: str, *args: Any) -> None:
            log.info("portal %s - %s", self.address_string(), fmt % args)

        # ── helpers ──
        def _send(self, body: str, status: int = 200,
                  ctype: str = "text/html; charset=utf-8") -> None:
            raw = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)
            self.wfile.flush()

        def _redirect_to_portal(self) -> None:
            body = portal_pages.render_probe_redirect(portal_root).encode("utf-8")
            self.send_response(302)
            self.send_header("Location", portal_root)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()

        def _form(self, error: str | None = None, status: int = 200, **kw: Any) -> None:
            self._send(portal_pages.render_form(
                networks=transport.networks,
                profiles=transport.profiles,
                profile=transport.device_profile,
                error=error,
                **kw,
            ), status=status)

        # ── routes ──
        def do_GET(self) -> None:  # noqa: N802 — stdlib naming
            path = urllib.parse.urlparse(self.path).path
            if path == "/":
                # A browser has actually opened the sign-in page, as opposed
                # to an OS probe hitting one of the PROBE_PATHS. Settling the
                # ring from breath to solid acknowledges the phone without
                # spending another spoken line. Once only - this is on the
                # request path and xvf_host is a process spawn.
                if not transport.phone_seen:
                    transport.phone_seen = True
                    from satellite.provisioning_mode import setup_status

                    setup_status("phone-connected")
                    transport._report_up()
                self._form(error=transport.setup_error(),
                           **(transport.last_choice or {}))
            elif path == "/device-info":
                self._send(json.dumps(transport.device_info),
                           ctype="application/json")
            elif path == "/networks":
                self._send(json.dumps(transport.networks),
                           ctype="application/json")
            elif path == "/status":
                self._send(json.dumps({
                    "status": transport.device_info.get("status"),
                    "error": transport.device_info.get("error"),
                }), ctype="application/json")
            else:
                # Every probe URL, and anything else wildcard DNS sent here.
                self._redirect_to_portal()

        def _too_large(self) -> None:
            """413 without reading the body. The unread bytes are still on
            the socket, so this connection cannot carry another request."""
            self.close_connection = True
            body = portal_pages.render_probe_redirect(portal_root).encode("utf-8")
            self.send_response(413)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()

        def do_POST(self) -> None:  # noqa: N802 — stdlib naming
            path = urllib.parse.urlparse(self.path).path
            if path != "/provision":
                self._redirect_to_portal()
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length < 0:
                length = 0
            if length > MAX_POST_BYTES:
                self._too_large()
                return
            raw = self.rfile.read(length).decode("utf-8", "replace") if length else ""
            fields = {
                k: v[0] for k, v in urllib.parse.parse_qs(raw, keep_blank_values=True).items()
            }
            try:
                payload = transport.build_payload(fields)
            except proto.ProvisionInvalid as e:
                # Re-render with the reason, as a 400: the submission was
                # refused, and a client reading the status can tell. Never
                # echo the password back.
                self._form(error=str(e), status=400,
                           **(transport.last_choice or {}))
                return

            # Respond FIRST. Accepting drops the AP, and a phone that never
            # got this page has no way to learn what happened. The page
            # names the server the satellite will dial, as resolved - the
            # last chance to notice a typo before the AP is gone.
            self._send(portal_pages.render_accepted(
                room_id=payload["room_id"], code=transport.approval_code,
                server=payload["domovoi_url"],
            ))
            transport.accept(payload)

    return PortalHandler
