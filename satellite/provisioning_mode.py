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
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

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


def is_provisioned() -> bool:
    """Provisioned = a config exists and no provisioning phase is active.
    The satellite client's main() parks while this module owns the box."""
    if not CONFIG_PATH.exists():
        return False
    return _read_state().get("phase") in (None, "done")


def provisioning_active() -> bool:
    return _read_state().get("phase") not in (None, "done")


# ─── Apply ────────────────────────────────────────────────────────────────


def apply_provision(
    payload: dict[str, Any],
    *,
    backend: GadgetBackend,
    image: Path,
    wifi_attempts: int,
    wifi_join_timeout: float,
    run=subprocess.run,
) -> tuple[bool, str | None]:
    """Write config + pairing token, join Wi-Fi, clean up. (ok, error).
    The provision file is wiped from the image BEFORE any possible
    re-expose, so credentials never ride the volume again."""
    # 1. Config from the example, comment-preserving.
    example = EXAMPLE_CONFIG.read_text(encoding="utf-8")
    changes: dict[str, Any] = {
        "satellite.room_id": payload["room_id"],
        "satellite.domovoi_url": payload["domovoi_url"],
        "satellite.sat_type": payload.get("sat_type", "voice"),
        "device.profile": payload["device_profile"],
    }
    merged = config_writer.apply_changes(example, changes)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(merged, encoding="utf-8")

    # 2. Pairing token, owner-only. The core already stored its sha256 —
    #    first connect matches as an already-paired room.
    PAIRING_TOKEN_PATH.write_text(payload["pairing_token"], encoding="utf-8")
    PAIRING_TOKEN_PATH.chmod(0o600)

    # 3. Timezone (best-effort).
    if payload.get("tz"):
        run(
            ["timedatectl", "set-timezone", str(payload["tz"])],
            capture_output=True, timeout=15,
        )

    # 4. Credentials off the volume BEFORE any re-expose.
    backend.delete_file(image, proto.PROVISION_NAME)

    # 5. Wi-Fi — the step that can fail on a typo'd PSK.
    wifi = payload["wifi"]
    last_err: str | None = None
    for attempt in range(1, wifi_attempts + 1):
        ok, err = apply_wifi(
            wifi["ssid"], wifi["psk"], wifi.get("country"),
            bool(wifi.get("hidden")), wifi_join_timeout, run=run,
        )
        if ok:
            return True, None
        last_err = err
        log.warning("wifi attempt %d/%d failed: %s", attempt, wifi_attempts, err)
    return False, last_err or "wifi join failed"


# ─── Main loop ────────────────────────────────────────────────────────────


def run(
    backend: GadgetBackend | None = None,
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
    the module's IMAGE_FILE at CALL time (monkeypatch-friendly)."""
    backend = backend or GadgetBackend()
    if image is None:
        image = IMAGE_FILE
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
        backend.unbind()
        backend.build_image(image, info)
        backend.bind(image)
        _write_state({"phase": status, "error": error, "nonce": nonce})
        log.info("gadget exposed (status=%s nonce=%s) — waiting for adopt", status, nonce)

        payload = _poll_for_provision(
            backend, image, nonce, poll_sec,
            max_loops=max_loops,
        )
        if payload is None:
            # Only reachable with max_loops (tests) — a real device polls on.
            return 1
        backend.unbind()
        ok, err = apply_provision(
            payload,
            backend=backend,
            image=image,
            wifi_attempts=wifi_attempts,
            wifi_join_timeout=wifi_join_timeout,
        )
        if ok:
            _write_state({"phase": "done"})
            image.unlink(missing_ok=True)
            log.info("provisioned as %r — rebooting", payload["room_id"])
            backend.reboot()
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
