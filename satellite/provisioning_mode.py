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
``domovoi-provisioning.service`` (Before=domovoi-satellite.service; the
unit is a no-op exit 0 on every provisioned boot). Root required:
configfs, mkfs, nmcli, timedatectl, reboot.

Deliberately imports ONLY stdlib + ``provisioning_protocol`` +
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
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Protocol

from satellite import config_writer
from satellite import provisioning_protocol as proto

log = logging.getLogger("provisioning")

CONFIG_DIR = Path("~/.domovoi").expanduser()
CONFIG_PATH = CONFIG_DIR / "config.toml"
PAIRING_TOKEN_PATH = CONFIG_DIR / "pairing_token"
STATE_FILE = CONFIG_DIR / "provisioning_state.json"
IMAGE_FILE = CONFIG_DIR / "setup_gadget.img"

IMAGE_BYTES = 16 * 1024 * 1024
GADGET_DIR = Path("/sys/kernel/config/usb_gadget/domovoi")
UDC_DIR = Path("/sys/class/udc")

EXAMPLE_CONFIG = Path(__file__).resolve().parent / "config.toml.example"


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
        return {"ssid": str(doc["ssid"]), "psk": str(doc["psk"])}
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
    NEVER logged — errors mention the ssid only."""
    if country:
        run(["iw", "reg", "set", country], capture_output=True, timeout=15)
    nmcli = shutil.which("nmcli")
    if nmcli:
        cmd = [nmcli, "device", "wifi", "connect", ssid, "password", psk]
        if hidden:
            cmd += ["hidden", "yes"]
        try:
            r = run(cmd, capture_output=True, timeout=timeout)
            if r.returncode == 0:
                return True, None
            return False, f"wifi join failed for {ssid!r} (wrong password?)"
        except subprocess.TimeoutExpired:
            return False, f"wifi join timed out for {ssid!r}"
    # wpa_supplicant fallback: render a network block and reconfigure.
    conf = Path("/etc/wpa_supplicant/wpa_supplicant.conf")
    try:
        gen = run(
            ["wpa_passphrase", ssid, psk], capture_output=True, timeout=15
        )
        if gen.returncode != 0:
            return False, "wpa_passphrase failed"
        block = gen.stdout.decode()
        if hidden:
            block = block.replace("}", "\tscan_ssid=1\n}")
        with open(conf, "a", encoding="utf-8") as f:
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
        return False, f"wifi join timed out for {ssid!r}"
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
    try:
        st = Path.home().stat()
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
    from satellite import discovery      # stdlib-only, like this module

    url = discovery.resolve_url(proto.AUTO_DISCOVER_URL)
    if url is None:
        log.error(
            "joined Wi-Fi but found no Domovoi server on this network. "
            "Set [satellite] domovoi_url in %s by hand.", CONFIG_PATH,
        )
        return None
    try:
        current = CONFIG_PATH.read_text(encoding="utf-8")
        CONFIG_PATH.write_text(
            config_writer.apply_changes(current, {"satellite.domovoi_url": url}),
            encoding="utf-8", newline="\n",
        )
    except OSError as e:
        log.error("discovered %s but could not save it: %s", url, e)
        return None
    log.info("discovered the Domovoi server at %s", url)
    return url


def apply_provision(
    payload: dict[str, Any],
    *,
    transport: Transport,
    wifi_attempts: int,
    wifi_join_timeout: float,
    run=subprocess.run,
) -> tuple[bool, str | None]:
    """Write config + pairing token, join Wi-Fi, clean up. (ok, error).
    The credentials are cleared from the transport BEFORE any possible
    re-expose, so they never ride a setup volume twice."""
    # 1. Config from the example, comment-preserving.
    example = EXAMPLE_CONFIG.read_text(encoding="utf-8")
    changes: dict[str, Any] = {
        "satellite.room_id": payload["room_id"],
        "satellite.domovoi_url": payload["domovoi_url"],
        "satellite.sat_type": payload.get("sat_type", "voice"),
        "device.profile": payload["device_profile"],
    }
    # Pin capture AND playback to the array on boards that need it. Without
    # this the client runs on the system default: capture lands on device
    # -1, and playback leaves the array entirely, so its on-chip AEC has no
    # echo reference and barge-in misfires on the satellite's own voice. It
    # was a manual step in PROVISIONING that no prepared card ever ran.
    match = _audio_device_match(payload["device_profile"])
    if match:
        changes["audio.input_device"] = match
        changes["audio.output_device"] = match
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

    # 3. Timezone (best-effort).
    if payload.get("tz"):
        run(
            ["timedatectl", "set-timezone", str(payload["tz"])],
            capture_output=True, timeout=15,
        )

    # 4. Credentials unreadable BEFORE any re-expose.
    transport.clear_provision()

    # 5. Wi-Fi — the step that can fail on a typo'd PSK.
    wifi = payload["wifi"]
    last_err: str | None = None
    for attempt in range(1, wifi_attempts + 1):
        ok, err = apply_wifi(
            wifi["ssid"], wifi["psk"], wifi.get("country"),
            bool(wifi.get("hidden")), wifi_join_timeout, run=run,
        )
        if ok:
            resolve_auto_url(payload.get("domovoi_url", ""))
            return True, None
        last_err = err
        log.warning("wifi attempt %d/%d failed: %s", attempt, wifi_attempts, err)
    return False, last_err or "wifi join failed"


_GADGET_CONFIG_LINE = "dtoverlay=dwc2,dr_mode=peripheral"
_GADGET_CMDLINE_TOKEN = "modules-load=dwc2"
_HOST_CONFIG_LINE = "dtoverlay=dwc2,dr_mode=host"
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
            kept = [ln for ln in text.splitlines()
                    if ln.strip() != _GADGET_CONFIG_LINE]
            # A USB mic array needs the port driven as a host, and the dwc2
            # driver clocks its audio correctly where the legacy dwc_otg one
            # delivers ~8x the sample rate. Swap rather than merely remove.
            if (
                image_device_profile() in _USB_MIC_PROFILES
                and not any(ln.strip() == _HOST_CONFIG_LINE for ln in kept)
            ):
                kept.append(_HOST_CONFIG_LINE)
            if kept != text.splitlines():
                cfg.write_text("\n".join(kept) + "\n", encoding="utf-8", newline="\n")
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
            rebuilt = " ".join(tokens) + "\n"
            if rebuilt != raw:
                # A malformed cmdline can stop the Pi booting — one line, no
                # stray newline, exactly as the firmware expects.
                cmdline.write_text(rebuilt, encoding="utf-8", newline="\n")
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
    )


# ─── Main loop ────────────────────────────────────────────────────────────


def run(
    backend: GadgetBackend | Transport | None = None,
    *,
    image: Path | None = None,
    poll_sec: float = 2.0,
    wifi_attempts: int = 3,
    wifi_join_timeout: float = 75.0,
    max_loops: int | None = None,
) -> int:
    """The provisioning state machine. Returns an exit code (0 = nothing to
    do or provisioned successfully). ``max_loops`` bounds the poll loop for
    tests; None = poll until provisioned or killed. ``image`` resolves to
    the module's IMAGE_FILE at CALL time (monkeypatch-friendly).

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
        transport.expose(info)
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
        ok, err = apply_provision(
            payload,
            transport=transport,
            wifi_attempts=wifi_attempts,
            wifi_join_timeout=wifi_join_timeout,
        )
        if ok:
            _write_state({"phase": "done"})
            # Held briefly so success is seen rather than inferred from the
            # reboot that follows it.
            setup_status("on-network")
            transport.dispose()
            log.info("provisioned as %r — rebooting", payload["room_id"])
            transport.reboot()
            return 0
        # Wi-Fi failed: strip the half-applied config (the client must not
        # start against a network we never joined), then re-present with
        # the error so the dashboard can show it and a force-adopt retries.
        CONFIG_PATH.unlink(missing_ok=True)
        PAIRING_TOKEN_PATH.unlink(missing_ok=True)
        status, error = "wifi_failed", err
        loops += 1
        if max_loops is not None and loops >= max_loops:
            return 1


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


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    if sys.platform != "linux":
        log.error("provisioning mode only runs on the satellite itself")
        return 2
    return run()


if __name__ == "__main__":
    sys.exit(main())
