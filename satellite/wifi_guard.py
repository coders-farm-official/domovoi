"""Keep wpa_supplicant to the Wi-Fi security this radio can actually do.

Kamron's card on 2026-10-23 (two TP-Link access points in EasyMesh, both
"WPA3-Personal+WPA2-PSK"): 18 "Trying to associate" lines, every one ending
"Authentication with <AP> timed out" ten seconds later, on both access
points, whatever the profile said. The supplicant was handed

    key_mgmt=WPA-PSK WPA-PSK-SHA256 FT-PSK SAE FT-SAE

for every wpa-psk attempt, PMF off included: NetworkManager 1.52 (the
Raspberry Pi OS Trixie one) adds SAE to a client wpa-psk profile whenever
the radio claims SAE, whatever its PMF setting (the PMF condition in
nm-supplicant-config.c applies to access-point mode only), and adds FT-PSK /
FT-SAE whenever wpa_supplicant was built with 802.11r. wpa_supplicant then
prefers FT-SAE, SAE, then FT-PSK over plain WPA-PSK, and the Pi Zero 2 W's
brcmfmac firmware completes none of them: the connect request is never
answered. NetworkManager has no setting that removes an AKM from that list.

So this small root service (``domovoi-wifi-guard.service``, this file
installed root-owned as ``/usr/local/lib/domovoi/wifi-guard`` and run by
``/usr/bin/python3 -I``; self-contained, standard library only) watches the
supplicant's control socket and, on every station network NetworkManager
adds, rewrites ``key_mgmt`` to the AKMs this board can do, before the
association starts (and re-associates when it had already started):

* every Raspberry Pi (brcmfmac): no FT-PSK / FT-SAE (802.11r). Roaming
  between access points still works, by plain re-association;
* a Pi whose radio cannot finish SAE (Zero 2 W, Zero W, 3 B,
  satellite/wifi_adapt.board_can_sae): no SAE either, unless SAE is all the
  network offers (WPA3-only), which is then left alone to fail in words;
* ``domovoi/ap.json`` on the boot partition may override it, as a
  diagnostic aid: ``"join_no_ft": false`` keeps FT, ``"join_key_mgmt":
  "WPA-PSK"`` (or "WPA-PSK WPA-PSK-SHA256", "SAE", ...) forces one list,
  ``"join_key_mgmt": "auto"`` turns the guard off.

The setup hotspot (``mode=2``) is never touched. Stdlib only.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path


def board_can_sae(model: str | None) -> bool | None:
    """satellite/wifi_adapt.board_can_sae, repeated so this file runs on its
    own: it is installed root-owned (/usr/local/lib/domovoi/wifi-guard) and
    run by the system Python with -I, never from the satellite account's
    code tree. The tests hold the two to the same answers."""
    low = (model or "").lower()
    if not low:
        return None
    if "zero 2 w" in low or "zero w" in low:
        return False
    if low.startswith("raspberry pi 3 model b") and "plus" not in low:
        return False
    if low.startswith("raspberry pi"):
        return True
    return None

log = logging.getLogger("wifi-guard")

CTRL_DIR = Path("/run/wpa_supplicant")
IFACE = "wlan0"
MODEL_FILE = Path("/proc/device-tree/model")
BOOT_DIRS = (Path("/boot/firmware"), Path("/boot"))

FT = ("FT-PSK", "FT-SAE", "FT-EAP", "FT-SAE-EXT-KEY", "FT-EAP-SHA384", "FT-FILS-SHA256",
      "FT-FILS-SHA384")
SAE = ("SAE", "FT-SAE", "SAE-EXT-KEY", "FT-SAE-EXT-KEY")
_ALLOWED_FORCED = {"WPA-PSK", "WPA-PSK-SHA256", "SAE", "FT-PSK", "FT-SAE", "SAE-EXT-KEY"}


@dataclass(frozen=True)
class Policy:
    drop_ft: bool
    drop_sae: bool
    forced: tuple[str, ...] | None = None
    off: bool = False

    def describe(self) -> str:
        if self.off:
            return "off (ap.json join_key_mgmt=auto)"
        if self.forced:
            return "key_mgmt forced to " + " ".join(self.forced) + " (ap.json)"
        parts = []
        if self.drop_ft:
            parts.append("no FT (802.11r)")
        if self.drop_sae:
            parts.append("no SAE (WPA3) unless the network is WPA3-only")
        return ", ".join(parts) or "nothing removed"


def policy_for(model: str | None, override: dict | None = None) -> Policy:
    """What this board may use. Every Raspberry Pi has a brcmfmac radio
    whose firmware cannot do FT; the ones :func:`board_can_sae` names cannot
    finish SAE. ``override``: the ap.json diagnostic keys."""
    low = (model or "").lower()
    drop_ft = low.startswith("raspberry pi")
    drop_sae = board_can_sae(model) is False
    ov = override or {}
    km = ov.get("join_key_mgmt")
    if isinstance(km, str) and km.strip():
        if km.strip().lower() == "auto":
            return Policy(False, False, off=True)
        tokens = tuple(t for t in km.upper().split() if t in _ALLOWED_FORCED)
        if tokens:
            return Policy(False, False, forced=tokens)
        log.warning("ignoring ap.json join_key_mgmt=%r", km)
    no_ft = ov.get("join_no_ft")
    if isinstance(no_ft, bool):
        drop_ft = no_ft
    return Policy(drop_ft, drop_sae)


def filtered(key_mgmt: str, policy: Policy) -> str | None:
    """The key_mgmt to set instead of ``key_mgmt``, or None to leave it."""
    have = key_mgmt.split()
    if policy.off or not have:
        return None
    if policy.forced:
        want = list(policy.forced)
    else:
        want = [t for t in have if not (policy.drop_ft and t in FT)]
        if policy.drop_sae:
            no_sae = [t for t in want if t not in SAE]
            # WPA3-only: SAE is all there is; leave it (it fails, and the
            # join says why) rather than send an empty list.
            if no_sae:
                want = no_sae
    if not want or want == have:
        return None
    return " ".join(want)


def read_override(boot_dirs=BOOT_DIRS) -> dict:
    for d in boot_dirs:
        p = Path(d) / "domovoi" / "ap.json"
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict):
            return {k: doc[k] for k in ("join_key_mgmt", "join_no_ft") if k in doc}
    return {}


def read_model(path: Path = MODEL_FILE) -> str | None:
    try:
        return path.read_bytes().split(b"\x00", 1)[0].decode("utf-8", "replace").strip() or None
    except OSError:
        return None


# ─── the supplicant's control socket ─────────────────────────────────────


class Ctrl:
    """One datagram socket to wpa_supplicant's per-interface control
    interface (what wpa_cli uses)."""

    _n = 0

    def __init__(self, path: Path, timeout: float = 5.0):
        Ctrl._n += 1
        self.local = f"/run/domovoi-wifi-guard-{os.getpid()}-{Ctrl._n}"
        try:
            os.unlink(self.local)
        except OSError:
            pass
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.sock.bind(self.local)
        self.sock.settimeout(timeout)
        self.sock.connect(str(path))

    def request(self, cmd: str) -> str:
        self.sock.send(cmd.encode("utf-8"))
        while True:
            data = self.sock.recv(8192).decode("utf-8", "replace")
            if data.startswith("<"):          # an event on an attached socket
                continue
            return data.strip()

    def close(self) -> None:
        try:
            self.sock.close()
        finally:
            try:
                os.unlink(self.local)
            except OSError:
                pass


_BUSY = {"AUTHENTICATING", "ASSOCIATING", "ASSOCIATED", "4WAY_HANDSHAKE", "GROUP_HANDSHAKE"}


def apply_to_network(cmd, net_id: str, policy: Policy) -> str | None:
    """Rewrite one network's key_mgmt; returns what was done, or None.
    ``cmd`` sends one control command and returns the reply."""
    mode = cmd(f"GET_NETWORK {net_id} mode")
    if mode.strip() == "2":                       # an access point: the setup hotspot
        return None
    current = cmd(f"GET_NETWORK {net_id} key_mgmt")
    if not current or current.startswith("FAIL"):
        return None
    new = filtered(current, policy)
    if new is None:
        return None
    if cmd(f"SET_NETWORK {net_id} key_mgmt {new}") != "OK":
        return f"network {net_id}: could not set key_mgmt {new!r}"
    status = cmd("STATUS")
    state = next((ln.split("=", 1)[1] for ln in status.splitlines()
                  if ln.startswith("wpa_state=")), "")
    if state in _BUSY:
        # The association had already started with the old list.
        cmd("REASSOCIATE")
    return f"network {net_id}: key_mgmt {current!r} -> {new!r} ({policy.describe()})"


def run(iface: str = IFACE, ctrl_dir: Path = CTRL_DIR) -> int:
    policy = policy_for(read_model(), read_override())
    log.info("wifi guard: %s on %s (%s)", policy.describe(), iface, read_model() or "unknown board")
    if policy.off or (not policy.drop_ft and not policy.drop_sae and not policy.forced):
        # Nothing to do on this board; stay up so systemd sees a healthy unit.
        while True:
            time.sleep(3600)
    path = ctrl_dir / iface
    while True:
        try:
            events = Ctrl(path, timeout=None)
            cmds = Ctrl(path)
        except OSError:
            time.sleep(2.0)
            continue
        try:
            if events.request("ATTACH") != "OK":
                raise OSError("ATTACH refused")
            # Networks NetworkManager added before this started.
            for row in cmds.request("LIST_NETWORKS").splitlines()[1:]:
                net_id = row.split("\t", 1)[0]
                done = apply_to_network(cmds.request, net_id, policy)
                if done:
                    log.info("wifi guard: %s", done)
            while True:
                msg = events.sock.recv(8192).decode("utf-8", "replace")
                if "CTRL-EVENT-NETWORK-ADDED" in msg:
                    net_id = msg.rsplit(" ", 1)[-1].strip()
                    done = apply_to_network(cmds.request, net_id, policy)
                    if done:
                        log.info("wifi guard: %s", done)
                elif "CTRL-EVENT-TERMINATING" in msg:
                    raise OSError("wpa_supplicant is stopping")
        except (OSError, socket.timeout) as e:
            log.info("wifi guard: control socket closed (%s); waiting for it", e)
            time.sleep(2.0)
        finally:
            events.close()
            cmds.close()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    if os.geteuid() != 0:
        log.error("run as root (the supplicant's control socket is root's)")
        return 1
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
