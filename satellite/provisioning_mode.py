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
import stat
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

# The two `wifi-sec.key-mgmt` values a home network with a passphrase can
# want. `wpa-psk` covers WPA2 and WPA2/WPA3 mixed; `sae` is WPA3-only.
_KEY_MGMT_PSK = "wpa-psk"
_KEY_MGMT_SAE = "sae"
# Lowercased fragments of an nmcli failure that mean the join REACHED
# activation and the AP would not have us — the only kind of failure where
# trying the other key-mgmt can possibly help. An AP that is not there, or a
# radio that is not usable, says something else and gets no second attempt.
_ACTIVATION_FAILURE_MARKERS = (
    "activation failed",
    "secrets were required",
    "no secrets",
    "authentication",
)
_PSK_PLACEHOLDER = "<psk>"


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


def _nmcli_detail(r, psk: str) -> str:
    """nmcli's own last word about a failure, scrubbed. Empty when it said
    nothing at all."""
    out = r.stderr or r.stdout or ""
    if isinstance(out, bytes):
        out = out.decode("utf-8", "replace")
    out = _scrub(out, psk).strip()
    return out.splitlines()[-1].strip() if out else ""


def _wait_for_ssid(
    nmcli: str, ssid: str, run, timeout: float, sleep=time.sleep
) -> tuple[bool, str | None]:
    """Rescan until NetworkManager can see ``ssid``, or give up. Returns
    ``(visible, security)`` — the AP's advertised SECURITY field, or None
    when we never saw it.

    Found on hardware, reliably: the first join ALWAYS failed and the second
    always worked, same password pasted both times. The join ran the instant
    the setup AP came down, while the interface was still leaving AP mode and
    NetworkManager's scan cache held nothing - it had been hosting, not
    scanning. `nmcli device wifi connect` refuses an SSID it cannot see, and
    the retries that followed were back-to-back with no rescan, so all three
    failed the same way. By the customer's second submission NM had scanned.

    The join no longer DEPENDS on this scan (see :func:`_nmcli_join`) — but
    the SECURITY field is still the only way to know WPA2 from WPA3-only
    without guessing, so it is worth the same wait it always was. An AP that
    answers on both bands contributes both rows' flags.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            r = run(
                [nmcli, "-t", "-f", "SSID,SECURITY", "device", "wifi",
                 "list", "--rescan", "yes"],
                capture_output=True, text=True, timeout=20,
            )
        except (OSError, subprocess.TimeoutExpired):
            r = None
        if r is not None and r.returncode == 0:
            flags: list[str] = []
            found = False
            for ln in (r.stdout or "").splitlines():
                row = _split_terse(ln.strip())
                if row and row[0].strip() == ssid:
                    found = True
                    if len(row) > 1:
                        flags.append(row[1].strip())
            if found:
                return True, " ".join(f for f in flags if f) or None
        if time.monotonic() >= deadline:
            return False, None
        sleep(_WIFI_SCAN_POLL_SEC)


def _key_mgmt_candidates(security: str | None) -> list[str]:
    """Which ``wifi-sec.key-mgmt`` values to try, best first.

    From the scan when there is one. When there is NOT — the blind join this
    whole path exists to survive — the order is ``wpa-psk`` then ``sae``,
    because WPA2 and WPA2/WPA3-mixed are what nearly every home AP
    advertises and ``wpa-psk`` is the single value that joins both of them,
    while ``sae`` joins only an AP that offers SAE. Getting it wrong costs
    one extra attempt, never a failure the customer sees.

    An AP the scan says is WPA3 with no WPA2 alongside it gets ``sae`` first
    — and ``wpa-psk`` still queued behind it, because a scan row read
    through one rescan of a radio just out of AP mode is evidence, not
    proof.
    """
    tokens = set((security or "").upper().replace(",", " ").split())
    sae = bool(tokens & {"WPA3", "SAE"})
    psk = bool(tokens & {"WPA", "WPA1", "WPA2", "PSK", "WPA-PSK"})
    if sae and not psk:
        return [_KEY_MGMT_SAE, _KEY_MGMT_PSK]
    return [_KEY_MGMT_PSK, _KEY_MGMT_SAE]


def _wifi_profiles_for_ssid(nmcli: str, ssid: str, run) -> list[str]:
    """UUIDs of every saved Wi-Fi profile whose SSID is ``ssid``.

    Matched on the ssid PROPERTY rather than the profile's name, because
    that is what NetworkManager itself matches on when it decides to reuse a
    profile: ``Kamber Wifi 2.0 1`` carries the same ssid as
    ``Kamber Wifi 2.0`` and is just as able to break the next attempt.
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


def _nmcli_join(
    nmcli: str,
    ssid: str,
    psk: str,
    hidden: bool,
    timeout: float,
    run,
    security: str | None,
) -> tuple[bool, str | None]:
    """Build the connection profile ourselves, then bring it up.

    The old form — ``nmcli device wifi connect <ssid> password <psk>`` —
    asks NetworkManager to INFER the security type from a recent scan of
    that AP. Joining blind, which is exactly what happens when wlan0 has
    spent the last minute HOSTING the setup AP rather than scanning, leaves
    nothing to infer from: NM writes a profile carrying a PSK and no
    key-mgmt, and activation dies on ``802-11-wireless-security.key-mgmt:
    property is missing`` while the customer stares at a password they typed
    correctly. Naming key-mgmt ourselves deletes the inference, and with it
    the dependency on a scan that may never have happened.

    Each candidate key-mgmt starts from a profile WE made this second: see
    :func:`_wifi_profiles_for_ssid` for why inheriting one is what made
    every retry fail identically.
    """
    first_err: str | None = None
    tried = False
    for km in _key_mgmt_candidates(security):
        # Whatever an earlier attempt left behind goes first — an earlier
        # submission's half-made profile, or the previous candidate's.
        # `nmcli device wifi connect` REUSED a matching saved profile, so
        # one broken profile made every later attempt fail the same way no
        # matter what was re-typed; and each fresh one is how
        # `Kamber Wifi 2.0 1`, `... 2` pile up.
        _delete_wifi_profiles(
            nmcli, _wifi_profiles_for_ssid(nmcli, ssid, run), run
        )
        add = [
            nmcli, "connection", "add", "type", "wifi",
            "con-name", ssid, "ifname", _WIFI_IFACE, "ssid", ssid,
            "connection.autoconnect", "yes",
            "wifi-sec.key-mgmt", km,
            "wifi-sec.psk", psk,
        ]
        if hidden:
            # A hidden AP is in no scan by definition, so the profile has to
            # say so or NM never probes for it. Same inference hole as the
            # visible case, same explicit answer.
            add += ["802-11-wireless.hidden", "yes"]
        tried = True
        try:
            r = run(add, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            # Never re-raise: the argv of that call holds the passphrase and
            # TimeoutExpired stringifies the command it was given.
            return False, f"wifi join timed out for {ssid!r}"
        except OSError:
            return False, (
                f"wifi join failed for {ssid!r}: NetworkManager is not answering"
            )
        if r.returncode != 0:
            detail = _nmcli_detail(r, psk)
            log.warning(
                "nmcli profile for %r could not be created (rc=%d): %s",
                ssid, r.returncode, detail,
            )
            return False, (
                f"wifi setup failed for {ssid!r}: "
                f"{detail or 'the connection could not be created'}"
            )

        uuids = _wifi_profiles_for_ssid(nmcli, ssid, run)
        up = [nmcli, "connection", "up"]
        up += ["uuid", uuids[0]] if len(uuids) == 1 else ["id", ssid]
        try:
            r = run(up, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return False, f"wifi join timed out for {ssid!r}"
        except OSError:
            return False, (
                f"wifi join failed for {ssid!r}: NetworkManager is not answering"
            )
        if r.returncode == 0:
            return True, None
        # Keep nmcli's own words. Reporting every failure as "wrong
        # password?" hid a scan-cache problem behind a message that sent
        # people re-typing a password that was right the first time.
        detail = _nmcli_detail(r, psk)
        log.warning(
            "nmcli join of %r as %s failed (rc=%d): %s",
            ssid, km, r.returncode, detail,
        )
        if first_err is None:
            # The FIRST candidate's reason is the one the customer gets:
            # wpa-psk is what their AP almost certainly speaks, so "secrets
            # were required" from that attempt is the true story, and the
            # sae attempt behind it is our business, not theirs.
            first_err = f"wifi join failed for {ssid!r}: {detail or 'wrong password?'}"
        if not any(m in detail.lower() for m in _ACTIVATION_FAILURE_MARKERS):
            break
    if tried:
        # Don't leave a profile that cannot associate sitting there with
        # autoconnect on: the next attempt would delete it anyway, and
        # nothing else on the device wants it.
        _delete_wifi_profiles(
            nmcli, _wifi_profiles_for_ssid(nmcli, ssid, run), run
        )
    return False, first_err or f"wifi join failed for {ssid!r}"


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
    ``wifi-sec.key-mgmt`` (:func:`_nmcli_join`) and the wpa_supplicant block
    names its own ``psk=``. An open network has no passphrase and
    ``validate_wifi_psk`` refuses one, so there is nothing here for it."""
    try:
        proto.validate_wifi_ssid(ssid)
    except proto.ProvisionInvalid as e:
        return False, f"wifi network name refused: {e}"
    if country:
        run(["iw", "reg", "set", country], capture_output=True, timeout=15)
    nmcli = shutil.which("nmcli")
    if nmcli:
        # A hidden network never appears in a scan. For everything else the
        # scan is still worth waiting for — it says WPA2 or WPA3 and saves an
        # attempt — but the join below no longer NEEDS it to have worked.
        security: str | None = None
        if not hidden:
            visible, security = _wait_for_ssid(
                nmcli, ssid, run, _WIFI_SCAN_WAIT_SEC
            )
            if not visible:
                log.warning(
                    "%r not visible after %.0fs of scanning - joining blind "
                    "with an explicit security type",
                    ssid, _WIFI_SCAN_WAIT_SEC,
                )
        return _nmcli_join(nmcli, ssid, psk, hidden, timeout, run, security)
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
    # stdlib-only, like this module
    from satellite import discovery, server_identity

    expected, source = server_identity.pinned_fingerprint()
    url = discovery.resolve_url(
        proto.AUTO_DISCOVER_URL, expected_fingerprint=expected
    )
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
        "discovered the Domovoi server at %s — pending approval on the "
        "dashboard", url,
    )
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
    # through the ALSA default while TTS went through the array. The greeting
    # overlaps command capture on the promise that the chip's AEC keeps it out
    # of the mic; that promise only holds if the clip goes through the chip.
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
