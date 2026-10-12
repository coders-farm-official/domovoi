"""The network settings root applies to a satellite — idempotently, and
nothing else.

Three settings, found missing on every prepared satellite on 2026-10-12
(design-notes/SAT-WIFI-FIX-BRIEF.md):

1. **The setup hotspot profile is deleted** once setup is over. Left behind,
   ``domovoi-setup`` is a profile NetworkManager may pick whenever the house
   network is out of sight (an access point is always "available"), and a
   satellite then sits broadcasting ``Domovoi-Setup-XXXX`` instead of being
   on the house network. Deleted by NAME only; any other profile, access
   point or not, is left exactly as it is.
2. **Wi-Fi power saving is off** on the house network's profile
   (``802-11-wireless.powersave 2``, persisted by NetworkManager) and on the
   radio right now (``iw dev wlan0 set power_save off``). The Pi's brcmfmac
   chip dozes between beacons with it on, which is what a voice satellite
   waiting for its wake word and a server waiting for its pong can least
   afford.
3. **The host name is** ``domovoi-<room>`` (a valid DNS label, at most 63
   characters), in ``/etc/hostname`` and on ``/etc/hosts``' ``127.0.1.1``
   line, so the router's client list and mDNS (``domovoi-<room>.local``,
   when avahi is running: it is restarted to pick the name up) say which
   room this is instead of a row of ``raspberrypi``. A name a person chose
   is left alone: only the stock name (``raspberrypi``), an empty one, or an
   earlier ``domovoi-*`` name (the room was renamed) is changed.

Who runs it, as root:

* a NEW card, at the end of setup (``provisioning_mode.run``) — before its
  first reboot into the client;
* EVERY card, at every boot (``provisioning_mode.main``, the
  ``domovoi-provisioning.service`` that already runs as root before the
  client). That is how a satellite set up before 2026-10-12 gets them: the
  satellite upgrade replaces this file, and the next boot applies them.
  docs/SECURITY_PRIVACY.md records that this is the root-runs-updatable-
  code path, and that the owner accepted it for these three settings.

Every run says exactly what it changed: in the journal, and in
``~/.domovoi/net-settings.json``, which the client reads and logs — so the
change reaches the log the core retains for the room. A run that changes
nothing writes the record (the version it holds is how the client knows
the settings are in place) and logs one quiet line.

Stdlib only, like everything provisioning mode imports.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from satellite import wifi_profiles

log = logging.getLogger("net-settings")

#: Bumped when this module starts applying something new. The client
#: compares the record's version with its own and, while it is behind,
#: says a reboot is needed (the boot-time run is what applies it).
NET_SETTINGS_VERSION = 1

CONFIG_DIR = Path("~/.domovoi").expanduser()
CONFIG_PATH = CONFIG_DIR / "config.toml"
REPORT_PATH = CONFIG_DIR / "net-settings.json"
HOSTS_PATH = Path("/etc/hosts")
HOSTNAME_PATH = Path("/etc/hostname")
#: Present when this unit runs the boot-time step at all (a prepared card;
#: a hand-built one installed with --with-provisioning).
PROVISIONING_UNIT = Path("/etc/systemd/system/domovoi-provisioning.service")

HOSTNAME_PREFIX = "domovoi-"
#: Host names nobody chose: the stock Raspberry Pi OS name and the blanks.
STOCK_HOSTNAMES = frozenset({"", "raspberrypi", "localhost", "localhost.localdomain"})
#: NetworkManager's value for "power saving off".
POWERSAVE_DISABLE = 2

_NM_WAIT_SEC = 45.0
_NM_POLL_SEC = 1.5
_TIMEOUT_SEC = 30.0
_LABEL_MAX = 63


# ─── the host name ─────────────────────────────────────────────────────────


def hostname_for_room(room_id: str | None) -> str:
    """``domovoi-<room>`` as a valid DNS label: lowercase letters, digits and
    dashes, no leading or trailing dash, at most 63 characters."""
    room = (room_id or "").strip().lower()
    room = re.sub(r"[^a-z0-9-]+", "-", room)
    room = re.sub(r"-{2,}", "-", room).strip("-")
    name = (HOSTNAME_PREFIX + room)[:_LABEL_MAX].rstrip("-")
    if name == HOSTNAME_PREFIX.rstrip("-") or not room:
        return HOSTNAME_PREFIX + "satellite"
    return name


def may_rename(current: str, wanted: str) -> bool:
    """Whether this step may change ``current`` to ``wanted``: only from a
    name nobody chose, or from an earlier room's ``domovoi-*`` name."""
    cur = (current or "").strip().lower()
    if cur == wanted:
        return False
    return cur in STOCK_HOSTNAMES or cur.startswith(HOSTNAME_PREFIX)


def rewrite_hosts(text: str, old: str, new: str) -> str | None:
    """``/etc/hosts`` with the ``127.0.1.1`` line naming ``new`` (in place of
    ``old``), or None when it already does. Every other line, and every
    other name on that line, is kept as it was."""
    lines = text.splitlines(keepends=True)
    found = False
    changed = False
    out: list[str] = []
    for line in lines:
        body = line.rstrip("\r\n")
        ending = line[len(body):]
        fields = body.split("#", 1)[0].split()
        if fields and fields[0] == "127.0.1.1":
            found = True
            names = fields[1:]
            renamed = [new if n.lower() == (old or "").lower() and old else n for n in names]
            if new not in renamed:
                renamed = [new] + renamed
            if renamed != names:
                comment = body.split("#", 1)[1] if "#" in body else None
                body = "127.0.1.1\t" + " ".join(renamed) + (f"\t#{comment}" if comment else "")
                changed = True
            out.append(body + ending)
        else:
            out.append(line)
    if not found:
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n"
        out.append(f"127.0.1.1\t{new}\n")
        changed = True
    return "".join(out) if changed else None


def read_hostname(path: Path | None = None) -> str:
    path = path or HOSTNAME_PATH
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        text = ""
    return text or socket.gethostname()


# ─── one run ───────────────────────────────────────────────────────────────


class Report:
    """What one run changed, left alone, and failed at."""

    def __init__(self) -> None:
        self.changed: list[str] = []
        self.unchanged: list[str] = []
        self.skipped: list[str] = []
        self.errors: list[str] = []

    def change(self, what: str) -> None:
        log.info("changed: %s", what)
        self.changed.append(what)

    def same(self, what: str) -> None:
        self.unchanged.append(what)

    def skip(self, what: str) -> None:
        log.info("left alone: %s", what)
        self.skipped.append(what)

    def error(self, what: str) -> None:
        log.warning("could not: %s", what)
        self.errors.append(what)

    def document(self, *, room_id: str | None, hostname: str | None, when: str) -> dict[str, Any]:
        return {
            "version": NET_SETTINGS_VERSION,
            "ts": time.time(),
            "boot_id": _boot_id(),
            "when": when,
            "room_id": room_id,
            "hostname": hostname,
            "changed": self.changed,
            "unchanged": self.unchanged,
            "skipped": self.skipped,
            "errors": self.errors,
        }


def _boot_id() -> str | None:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()[:64] or None
    except OSError:
        return None


def _call(run: Callable[..., Any], argv: list[str]) -> tuple[int, str]:
    """``(returncode, last line of output)``; 127 when the tool is missing."""
    try:
        r = run(argv, capture_output=True, text=True, timeout=_TIMEOUT_SEC)
    except FileNotFoundError:
        return 127, f"{argv[0]} is not installed"
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        return 1, str(e)
    out = (getattr(r, "stderr", "") or "") + (getattr(r, "stdout", "") or "")
    if isinstance(out, bytes):
        out = out.decode("utf-8", "replace")
    lines = [ln.strip() for ln in out.strip().splitlines() if ln.strip()]
    return int(getattr(r, "returncode", 1)), (lines[-1] if lines else "")


def _stdout(run: Callable[..., Any], argv: list[str]) -> str | None:
    try:
        r = run(argv, capture_output=True, text=True, timeout=_TIMEOUT_SEC)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if getattr(r, "returncode", 1) != 0:
        return None
    out = getattr(r, "stdout", "") or ""
    return out.decode("utf-8", "replace") if isinstance(out, bytes) else out


def wait_for_networkmanager(nmcli: str, run: Callable[..., Any],
                            sleep: Callable[[float], None] = time.sleep,
                            timeout: float = _NM_WAIT_SEC) -> bool:
    """At boot this service can start before NetworkManager answers."""
    deadline = time.monotonic() + timeout
    while True:
        out = _stdout(run, [nmcli, "-t", "-f", "RUNNING", "general"])
        if out is not None and out.strip().lower().startswith("running"):
            return True
        if time.monotonic() >= deadline:
            return False
        sleep(_NM_POLL_SEC)


def _powersave_of(nmcli: str, uuid: str, run: Callable[..., Any]) -> int | None:
    out = _stdout(run, [nmcli, "-t", "-f", "802-11-wireless.powersave",
                        "connection", "show", "uuid", uuid])
    if out is None:
        return None
    for line in out.splitlines():
        key, _, value = line.partition(":")
        if key.strip() == "802-11-wireless.powersave":
            m = re.match(r"\s*(\d+)", value)
            return int(m.group(1)) if m else None
    return None


def delete_setup_profile(nmcli: str, profiles: list[wifi_profiles.Profile],
                         run: Callable[..., Any], report: Report) -> None:
    ours = [p for p in profiles if p.name == wifi_profiles.AP_CONNECTION]
    if not ours:
        report.same(f"no {wifi_profiles.AP_CONNECTION} profile")
    for p in ours:
        rc, detail = _call(run, [nmcli, "connection", "delete", "uuid", p.uuid])
        if rc == 0:
            report.change(
                f"deleted the setup hotspot profile {p.name} ({p.uuid}); a "
                "satellite that loses the house network can no longer fall back "
                "onto broadcasting its setup network"
            )
        else:
            report.error(f"delete the setup hotspot profile {p.name} ({p.uuid}): {detail}")
    for p in profiles:
        if p.is_ap and p.name != wifi_profiles.AP_CONNECTION:
            report.skip(f"access-point profile {p.name!r} is not Domovoi's")


def disable_powersave(nmcli: str | None, profiles: list[wifi_profiles.Profile],
                      run: Callable[..., Any], report: Report,
                      iface: str = wifi_profiles.WIFI_IFACE) -> None:
    if nmcli is not None:
        clients = [p for p in profiles if not p.is_ap]
        if not clients:
            report.same("no house Wi-Fi profile to set power saving on")
        for p in clients:
            current = _powersave_of(nmcli, p.uuid, run)
            if current == POWERSAVE_DISABLE:
                report.same(f"power saving already off on {p.name!r}")
                continue
            rc, detail = _call(run, [
                nmcli, "connection", "modify", "uuid", p.uuid,
                "802-11-wireless.powersave", str(POWERSAVE_DISABLE),
            ])
            if rc == 0:
                report.change(
                    f"Wi-Fi power saving off on {p.name!r} "
                    f"(802-11-wireless.powersave {current if current is not None else '?'} -> "
                    f"{POWERSAVE_DISABLE})"
                )
            else:
                report.error(f"turn power saving off on {p.name!r}: {detail}")
    # And on the radio now: NetworkManager applies a profile's setting when
    # it next activates it, and this boot's activation may already be done.
    # On a unit without NetworkManager this is the only place it is set,
    # and it runs every boot.
    iw = shutil.which("iw") or "iw"
    out = _stdout(run, [iw, "dev", iface, "get", "power_save"])
    if out is None:
        report.same(f"no {iface} power-save state to read")
        return
    if "off" in out.lower():
        report.same(f"power saving already off on {iface}")
        return
    rc, detail = _call(run, [iw, "dev", iface, "set", "power_save", "off"])
    if rc == 0:
        report.change(f"Wi-Fi power saving off on {iface} now (iw)")
    else:
        report.error(f"turn power saving off on {iface}: {detail}")


def set_hostname(room_id: str | None, run: Callable[..., Any], report: Report, *,
                 hostname_path: Path | None = None, hosts_path: Path | None = None) -> str | None:
    hostname_path = hostname_path or HOSTNAME_PATH
    hosts_path = hosts_path or HOSTS_PATH
    if not room_id:
        report.skip("host name: no room_id in config.toml")
        return None
    wanted = hostname_for_room(room_id)
    current = read_hostname(hostname_path).strip().lower()
    renamed = False
    if current != wanted:
        if not may_rename(current, wanted):
            report.skip(
                f"host name {current!r} looks chosen by a person; not renaming it "
                f"to {wanted!r}"
            )
            return current
        rc, detail = _call(run, ["hostnamectl", "set-hostname", wanted])
        if rc != 0:
            # No D-Bus yet this early in boot, or no systemd-hostnamed:
            # the same two things hostnamectl would do, by hand.
            try:
                hostname_path.write_text(wanted + "\n", encoding="utf-8")
            except OSError as e:
                report.error(f"set the host name to {wanted!r}: {detail}; {e}")
                return current
            _call(run, ["hostname", wanted])
        report.change(f"host name {current!r} -> {wanted!r}")
        renamed = True
    else:
        report.same(f"host name already {wanted!r}")
    try:
        hosts = hosts_path.read_text(encoding="utf-8")
    except OSError as e:
        report.error(f"read {hosts_path}: {e}")
        hosts = None
    if hosts is not None:
        updated = rewrite_hosts(hosts, current, wanted)
        if updated is None:
            report.same(f"{hosts_path} already names {wanted!r}")
        else:
            try:
                tmp = hosts_path.with_name(hosts_path.name + ".domovoi.tmp")
                tmp.write_text(updated, encoding="utf-8", newline="")
                try:
                    os.chmod(tmp, os.stat(hosts_path).st_mode & 0o7777)
                except OSError:
                    pass
                os.replace(tmp, hosts_path)
                report.change(f"{hosts_path}: 127.0.1.1 names {wanted!r}")
            except OSError as e:
                report.error(f"update {hosts_path}: {e}")
    if renamed:
        # mDNS: avahi announces the name it read when it started. Restart
        # it only when it is running; a stopped or absent avahi is left so.
        rc, _ = _call(run, ["systemctl", "is-active", "--quiet", "avahi-daemon.service"])
        if rc == 0:
            rc, detail = _call(run, ["systemctl", "try-restart", "avahi-daemon.service"])
            if rc == 0:
                report.change(f"avahi-daemon restarted: this unit is {wanted}.local over mDNS")
            else:
                report.error(f"restart avahi-daemon for the new name: {detail}")
    return wanted


def read_room_id(config_path: Path | None = None) -> str | None:
    import tomllib

    try:
        with open(config_path or CONFIG_PATH, "rb") as f:
            doc = tomllib.load(f)
    except (OSError, ValueError):
        return None
    room = (doc.get("satellite") or {}).get("room_id")
    return str(room) if room else None


def apply(
    room_id: str | None,
    *,
    run: Callable[..., Any] = subprocess.run,
    when: str = "boot",
    report_path: Path | None = None,
    hostname_path: Path | None = None,
    hosts_path: Path | None = None,
    sleep: Callable[[float], None] = time.sleep,
    nmcli: str | None = None,
    nm_wait: float = _NM_WAIT_SEC,
) -> dict[str, Any]:
    """Apply the three settings; return (and record) what changed. Never
    raises: a satellite must boot whatever this step manages."""
    report = Report()
    hostname: str | None = None
    try:
        nm = nmcli if nmcli is not None else shutil.which("nmcli")
        profiles: list[wifi_profiles.Profile] = []
        if nm and not wait_for_networkmanager(nm, run, sleep, nm_wait):
            report.error("NetworkManager did not answer; its profiles were left for the next boot")
            nm = None
        if nm:
            listed = wifi_profiles.list_wifi_profiles(run, nm)
            if listed is None:
                report.error("list NetworkManager's Wi-Fi profiles")
                nm = None
            else:
                profiles = listed
        if nm:
            delete_setup_profile(nm, profiles, run, report)
        else:
            report.same("no NetworkManager: no setup hotspot profile to delete")
        disable_powersave(nm, profiles, run, report)
        hostname = set_hostname(room_id, run, report,
                                hostname_path=hostname_path, hosts_path=hosts_path)
    except Exception as e:  # noqa: BLE001 — never take the boot down
        report.error(f"unexpected failure: {e!r}")
    doc = report.document(room_id=room_id, hostname=hostname, when=when)
    write_report(doc, report_path or REPORT_PATH)
    if not report.changed and not report.errors:
        log.info("network settings v%d already in place; nothing changed", NET_SETTINGS_VERSION)
    return doc


def write_report(doc: dict[str, Any], path: Path) -> bool:
    """Atomically, and handed to the satellite user (who reads it)."""
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(doc, indent=1), encoding="utf-8", newline="\n")
        os.replace(tmp, path)
    except OSError as e:
        log.warning("could not write %s: %s", path, e)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    chown = getattr(os, "chown", None)
    if chown is not None:
        try:
            st = path.parent.stat()
            chown(path, st.st_uid, st.st_gid)
        except OSError:
            pass
    return True


# ─── what the client reads ────────────────────────────────────────────────


def read_report(path: Path | None = None) -> dict[str, Any] | None:
    try:
        doc = json.loads((path or REPORT_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def reboot_needed(report: dict[str, Any] | None, *, unit_installed: bool) -> str | None:
    """Why this unit needs a reboot for its network settings, or None. Only
    a unit that runs the boot-time step can be asked to reboot for it."""
    if not unit_installed:
        return None
    version = (report or {}).get("version")
    if isinstance(version, int) and not isinstance(version, bool) and version >= NET_SETTINGS_VERSION:
        return None
    return (
        "network settings (no setup hotspot, Wi-Fi power saving off, host name "
        "domovoi-<room>) are applied at boot: reboot this satellite once"
    )


def main(argv: list[str] | None = None) -> int:
    """``sudo ~/satellite-venv/bin/python -m satellite.net_settings`` — the
    same step by hand, for a unit without ``domovoi-provisioning.service``."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if sys.platform != "linux":
        log.error("this only runs on the satellite itself")
        return 2
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        log.error("run it as root (sudo): it changes the host name and NetworkManager profiles")
        return 2
    doc = apply(read_room_id(), when="manual")
    print(json.dumps(doc, indent=1))
    return 1 if doc.get("errors") else 0


if __name__ == "__main__":
    sys.exit(main())
