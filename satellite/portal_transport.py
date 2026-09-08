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

_SCAN_TIMEOUT_SEC = 20.0
_NMCLI_TIMEOUT_SEC = 30.0


def _nmcli() -> str | None:
    return shutil.which("nmcli")


def scan_networks(run=subprocess.run) -> list[str]:
    """Visible SSIDs, strongest first. Best-effort: a failed scan yields an
    empty list and the form falls back to a free-text field rather than
    dead-ending the customer."""
    nmcli = _nmcli()
    if nmcli is None:
        return []
    try:
        proc = run(
            [nmcli, "-t", "-f", "SSID,SIGNAL", "device", "wifi", "list", "--rescan", "yes"],
            capture_output=True, text=True, timeout=_SCAN_TIMEOUT_SEC, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("wifi scan failed: %s", e)
        return []
    if proc.returncode != 0:
        return []
    rows: list[dict[str, Any]] = []
    for line in (proc.stdout or "").splitlines():
        # -t output is colon-separated with backslash-escaped colons in
        # values; an SSID may legitimately contain one.
        parts = line.replace("\\:", "\x00").split(":")
        if len(parts) < 2:
            continue
        rows.append({
            "ssid": parts[0].replace("\x00", ":"),
            "signal": parts[-1],
        })
    return portal_pages.summarize_networks(rows)


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

    # ── Transport seam ───────────────────────────────────────────────────

    def expose(self, device_info: dict[str, Any]) -> None:
        self.device_info = device_info
        self.nonce = device_info["nonce"]
        # A fresh code per session, for the same reason as the nonce: an
        # approval shown in a previous attempt must not still be valid.
        self.approval_code = f"{secrets.randbelow(10000):04d}"
        # Scan BEFORE the radio is committed to hosting — once the AP is up
        # the interface can no longer survey the neighbourhood.
        self.networks = scan_networks(run=self.run)
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
            self._start_ap()
        except Exception:
            self._stop_server()      # don't leave a socket behind on failure
            raise
        log.info(
            "setup portal up: ssid=%s http://%s:%d (%d networks visible)",
            self.ap_ssid, self.ip, self.port, len(self.networks),
        )

    def wait_for_provision(self, nonce: str) -> dict[str, Any] | None:
        """Block until the portal handler accepts a payload. No polling and
        no two-stable-reads dance — that rule exists because FAT write
        caching makes torn reads inevitable, and HTTP delivers a whole
        request or none."""
        payload = self._queue.get()
        return payload

    def withdraw(self) -> None:
        self._stop_server()
        self._stop_ap()

    def clear_provision(self) -> None:
        """No-op with a real purpose: the payload only ever existed in this
        process's memory, so there is no file to shred. Drop the reference
        so a re-exposed session can't serve it back."""
        with self._queue.mutex:
            self._queue.queue.clear()

    def dispose(self) -> None:
        self.withdraw()

    def reboot(self) -> None:
        self.run(["systemctl", "--no-block", "reboot"], timeout=60)

    def _persist_approval_code(self) -> None:
        """The code outlives this process: the portal shows it, the reboot
        happens, and the client presents it on connect so the dashboard can
        ask the customer to match what they saw."""
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            path = self.state_dir / "approval_code"
            path.write_text(self.approval_code + "\n", encoding="utf-8")
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
        except (OSError, subprocess.TimeoutExpired) as e:
            raise RuntimeError(f"{what} failed: {e}") from e
        if proc.returncode != 0:
            raise RuntimeError(
                f"{what} failed (rc={proc.returncode}): "
                f"{(proc.stderr or '').strip()}"
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

    def _start_ap(self) -> None:
        iface = self.wifi_interface()
        if iface is None:
            raise RuntimeError(
                "no Wi-Fi interface available — is the radio rfkill-blocked "
                "for want of a wireless country?"
            )
        cmd = ["device", "wifi", "hotspot", "con-name", AP_CONNECTION,
               "ssid", self.ap_ssid, "password", self.ap_psk, "ifname", iface]
        # The PSK rides in argv, which is root-only readable here, and is
        # never logged — same rule as apply_wifi().
        self._nmcli(cmd, f"raising the setup AP on {iface}")
        self._ap_up = True

        # NetworkManager's shared mode picks its own subnet (10.42.x.1 by
        # default, and which one depends on what's already in use). The
        # captive-portal DNS drop-in written at prepare time points at a
        # FIXED address, so pin the AP to match — otherwise every hostname
        # resolves to an address nothing is listening on and the sign-in
        # page never opens.
        self._nmcli(
            ["connection", "modify", AP_CONNECTION,
             "ipv4.method", "shared", "ipv4.addresses", f"{self.ip}/24"],
            "pinning the setup AP address",
        )
        self._nmcli(["connection", "up", AP_CONNECTION],
                    "activating the setup AP")
        self._verify_ap_active()

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
        ssid = (fields.get("ssid") or "").strip()
        psk = fields.get("psk") or ""
        if not ssid:
            raise proto.ProvisionInvalid("Choose your Wi-Fi network.")
        if not psk:
            raise proto.ProvisionInvalid("Enter your Wi-Fi password.")

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

        # Blank means "find the server yourself at first start". The
        # sentinel keeps validate_provision strict about non-empty strings
        # rather than teaching it to accept blanks.
        url = (fields.get("url") or "").strip() or proto.AUTO_DISCOVER_URL

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

        def _form(self, error: str | None = None, **kw: Any) -> None:
            self._send(portal_pages.render_form(
                networks=transport.networks,
                profiles=transport.profiles,
                error=error,
                **kw,
            ))

        # ── routes ──
        def do_GET(self) -> None:  # noqa: N802 — stdlib naming
            path = urllib.parse.urlparse(self.path).path
            if path == "/":
                self._form()
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

        def do_POST(self) -> None:  # noqa: N802 — stdlib naming
            path = urllib.parse.urlparse(self.path).path
            if path != "/provision":
                self._redirect_to_portal()
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            raw = self.rfile.read(length).decode("utf-8", "replace") if length else ""
            fields = {
                k: v[0] for k, v in urllib.parse.parse_qs(raw, keep_blank_values=True).items()
            }
            try:
                payload = transport.build_payload(fields)
            except proto.ProvisionInvalid as e:
                # Re-render with the reason. Never echo the password back.
                self._form(error=str(e),
                           room=fields.get("room") or None,
                           ssid=fields.get("ssid") or None)
                return

            # Respond FIRST. Accepting drops the AP, and a phone that never
            # got this page has no way to learn what happened.
            self._send(portal_pages.render_accepted(
                room_id=payload["room_id"], code=transport.approval_code,
            ))
            transport.accept(payload)

    return PortalHandler
