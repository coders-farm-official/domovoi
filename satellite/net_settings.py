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
  first reboot into the client; the host name alone also before setup
  joins the house Wi-Fi (``provisioning_mode.apply_provision``), so even
  that first DHCP request carries it;
* EVERY card, at every boot (``provisioning_mode.main``, the
  ``domovoi-provisioning.service`` that already runs as root before the
  client). That is how a satellite set up before 2026-10-12 gets them: the
  satellite upgrade replaces this file, and the next boot applies them.
  docs/SECURITY_PRIVACY.md records that this is the root-runs-updatable-
  code path, and that the owner accepted it for these three settings;
* the host name alone, at every boot BEFORE NetworkManager starts
  (``python -m satellite.net_settings --before-network``, from
  ``domovoi-hostname.service``: stage 1 installs it on a new card, the
  boot step above on an older one). The router learns a satellite's name
  from its DHCP request, which NetworkManager sends as soon as the Wi-Fi
  joins, with whatever name the unit had at that moment. Renamed later in
  the boot, a unit kept its old name on the router until it next
  reconnected: every satellite needed a second reboot (2026-10-12). Where
  the boot step itself still does the rename (the first boot after the
  upgrade, before that unit exists), it asks NetworkManager for one fresh
  DHCP exchange under the new name (:func:`refresh_dhcp_name`), once,
  right after the change.

Every run says exactly what it changed: in the journal, and in
``/var/lib/domovoi/net-settings.json`` — root's own directory, 0644, which
the client reads and logs, so the change reaches the log the core retains
for the room. Never in the satellite user's home: root writing there
follows whatever link that user planted (the first version did, and a
reviewer had it overwrite a root helper). A run that changes nothing writes
the record (the version it holds, with no errors, is how the client knows
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
#: 2 (2026-10-12): the host name is set before the network starts
#: (domovoi-hostname.service), and a late rename asks DHCP again.
NET_SETTINGS_VERSION = 2

CONFIG_DIR = Path("~/.domovoi").expanduser()
CONFIG_PATH = CONFIG_DIR / "config.toml"
#: Root's own state directory (stage 1 made it for its step markers).
STATE_DIR = Path("/var/lib/domovoi")
REPORT_PATH = STATE_DIR / "net-settings.json"
#: What the before-the-network run did this boot (the boot step folds it
#: into its own record).
EARLY_REPORT_PATH = STATE_DIR / "net-settings-early.json"
#: The host name this step set, so it renames only a name it set itself.
HOSTNAME_RECORD = STATE_DIR / "hostname-set"
#: Where root's units live, and the one that sets the host name before the
#: network starts. Its template rides the code tree (satellite/scripts/),
#: so an upgrade brings it and the boot step installs or refreshes it.
SYSTEMD_DIR = Path("/etc/systemd/system")
EARLY_UNIT_NAME = "domovoi-hostname.service"
EARLY_UNIT_TEMPLATE = Path(__file__).resolve().parent / "scripts" / EARLY_UNIT_NAME
#: The enable link of the boot step's own unit: without it nothing runs
#: this module at boot, whatever the unit file says.
PROVISIONING_UNIT_NAME = "domovoi-provisioning.service"
WANTS_DIRNAME = "multi-user.target.wants"
WLAN_MAC_PATH = Path("/sys/class/net/wlan0/address")
MACHINE_ID_PATH = Path("/etc/machine-id")
#: Root's record of the server's address (provisioning_mode
#: ROOT_SERVER_URL_PIN), which the clock helper trusts over its caller, and
#: the root-owned server identity pin first boot installed.
ROOT_SERVER_URL_PIN = Path("/etc/domovoi/server.url")
ROOT_IDENTITY_PIN = Path("/etc/domovoi/server-identity.json")
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
#: Before the network the unit has 30 s in all (TimeoutStartSec), and
#: NetworkManager waits for it: hostnamectl gets this long, then the
#: name is written by hand.
_EARLY_HOSTNAMECTL_SEC = 8.0
#: How long the boot step waits for the Wi-Fi to come up before asking
#: DHCP again under a new name (the next connect carries it anyway).
_LINK_WAIT_SEC = 45.0
_LABEL_MAX = 63
#: NetworkManager's device state "connected".
_NM_CONNECTED = 100
#: A home directory this module will write into a root unit file.
_SAFE_HOME = re.compile(r"^/[A-Za-z0-9._/-]+$")


# ─── the host name ─────────────────────────────────────────────────────────


def unit_suffix(mac_path: Path | None = None, machine_id_path: Path | None = None) -> str:
    """Six hex digits that stay with this unit: the end of the Wi-Fi MAC,
    else the start of the machine id; "" when neither can be read."""
    for path, take in ((mac_path or WLAN_MAC_PATH, lambda t: t[-6:]),
                       (machine_id_path or MACHINE_ID_PATH, lambda t: t[:6])):
        try:
            text = re.sub(r"[^0-9a-f]", "", path.read_text(encoding="utf-8").strip().lower())
        except OSError:
            continue
        if len(text) >= 6:
            return take(text)
    return ""


def hostname_for_room(room_id: str | None, suffix: str = "") -> str:
    """``domovoi-<room>`` as a valid DNS label: lowercase letters, digits and
    dashes, no leading or trailing dash, at most 63 characters. A room with
    nothing usable in it (no ASCII letter or digit) is ``domovoi-<suffix>``
    instead — a stable piece of this unit (:func:`unit_suffix`) — so two
    such rooms do not both claim one mDNS name."""
    room = (room_id or "").strip().lower()
    room = re.sub(r"[^a-z0-9-]+", "-", room)
    room = re.sub(r"-{2,}", "-", room).strip("-")
    if not room:
        tail = re.sub(r"[^a-z0-9]", "", (suffix or "").lower())[:12]
        return HOSTNAME_PREFIX + (tail or "satellite")
    return (HOSTNAME_PREFIX + room)[:_LABEL_MAX].rstrip("-")


def may_rename(current: str, wanted: str, set_before: str | None = None) -> bool:
    """Whether this step may change ``current`` to ``wanted``: only from a
    name nobody chose (the stock one, a blank), or from the name this step
    itself set earlier (the room was renamed). A ``domovoi-pi`` a person
    typed is theirs."""
    cur = (current or "").strip().lower()
    if cur == wanted:
        return False
    if cur in STOCK_HOSTNAMES:
        return True
    return bool(set_before) and cur == set_before.strip().lower()


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
        #: ``[old, new]`` when this run renamed the unit, else None.
        self.renamed: list[str] | None = None

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
            "renamed": self.renamed,
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


def _uptime() -> float | None:
    try:
        return float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def _call(run: Callable[..., Any], argv: list[str],
          timeout: float = _TIMEOUT_SEC) -> tuple[int, str]:
    """``(returncode, last line of output)``; 127 when the tool is missing."""
    try:
        r = run(argv, capture_output=True, text=True, timeout=timeout)
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
        # The HOUSE profile only (`domovoi-home`, or the one in use on an
        # older card) — not every saved client profile on the unit.
        home = wifi_profiles.home_profile(profiles, strict=True)
        clients = [home] if home is not None else []
        if not clients:
            report.skip("power saving: no profile is clearly the house network "
                        "(none named domovoi-home, none active); left for the next boot")
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
                 hostname_path: Path | None = None, hosts_path: Path | None = None,
                 record_path: Path | None = None, suffix: str | None = None,
                 hostnamectl_timeout: float = _TIMEOUT_SEC,
                 restart_avahi: bool = True) -> str | None:
    """``domovoi-<room>`` where :func:`may_rename` allows it; returns the
    name the unit has afterwards. A rename is noted in ``report.renamed``.
    ``restart_avahi`` False leaves mDNS to the caller (before the network,
    avahi is not up yet, and asking systemd to restart a unit from inside
    another unit's start job is how boots hang)."""
    hostname_path = hostname_path or HOSTNAME_PATH
    hosts_path = hosts_path or HOSTS_PATH
    record_path = record_path or HOSTNAME_RECORD
    if not room_id:
        report.skip("host name: no room_id in config.toml")
        return None
    wanted = hostname_for_room(room_id, unit_suffix() if suffix is None else suffix)
    current = read_hostname(hostname_path).strip().lower()
    try:
        set_before: str | None = record_path.read_text(encoding="utf-8").strip() or None
    except OSError:
        set_before = None
    renamed = False
    if current != wanted:
        if not may_rename(current, wanted, set_before):
            report.skip(
                f"host name {current!r} looks chosen by a person; not renaming it "
                f"to {wanted!r}"
            )
            return current
        rc, detail = _call(run, ["hostnamectl", "set-hostname", wanted], timeout=hostnamectl_timeout)
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
        report.renamed = [current, wanted]
        renamed = True
    if renamed:
        try:
            record_path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            safe_write(record_path.parent, record_path.name, (wanted + "\n").encode("utf-8"))
        except OSError as e:
            report.error(f"record the host name this step set: {e}")
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
    if renamed and restart_avahi:
        restart_avahi_for(wanted, run, report)
    return wanted


def restart_avahi_for(name: str, run: Callable[..., Any], report: Report) -> None:
    """mDNS: avahi announces the name it read when it started. Restarted
    only when it is running; a stopped or absent avahi is left so."""
    rc, _ = _call(run, ["systemctl", "is-active", "--quiet", "avahi-daemon.service"])
    if rc == 0:
        rc, detail = _call(run, ["systemctl", "try-restart", "avahi-daemon.service"])
        if rc == 0:
            report.change(f"avahi-daemon restarted: this unit is {name}.local over mDNS")
        else:
            report.error(f"restart avahi-daemon for the new name: {detail}")


def read_room_id(config_path: Path | None = None) -> str | None:
    import tomllib

    try:
        with open(config_path or CONFIG_PATH, "rb") as f:
            doc = tomllib.load(f)
    except (OSError, ValueError):
        return None
    room = (doc.get("satellite") or {}).get("room_id")
    return str(room) if room else None


def _pinned_fingerprint_root(path: Path) -> str | None:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    fp = doc.get("fingerprint") if isinstance(doc, dict) else None
    return fp if isinstance(fp, str) and fp.startswith("SHA256:") else None


def _configured_url(config_path: Path) -> str | None:
    import tomllib

    try:
        with open(config_path, "rb") as f:
            doc = tomllib.load(f)
    except (OSError, ValueError):
        return None
    url = (doc.get("satellite") or {}).get("domovoi_url")
    url = str(url).strip() if url else ""
    return url if url and url.lower() != "auto" else None


def refresh_server_pin(
    report: Report, *,
    config_path: Path | None = None,
    pin_path: Path | None = None,
    identity_path: Path | None = None,
    probe: Callable[..., bool] | None = None,
    attempts: int = 6,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Keep root's record of the server's address with the server when it
    moves (a new DHCP lease): the client finds it again by name and saves
    the new address in config.toml, but the clock helper believes only
    ``/etc/domovoi/server.url`` and would refuse the time from then on.

    Root does not take the satellite user's config.toml for it: the
    address there has to prove, to THIS process, that it is the server the
    root-owned identity pin names (a fresh nonce signed for the address
    dialled). Without that pin nothing is changed. A server that does not
    answer this boot is left for the next one (not an error: the core may
    be restarting)."""
    from satellite import discovery, server_identity

    pin_path = pin_path or ROOT_SERVER_URL_PIN
    try:
        pinned = pin_path.read_text(encoding="utf-8").strip() or None
    except OSError:
        pinned = None
    if pinned is None:
        report.same("no root server-address record (the clock helper is unpinned)")
        return
    fingerprint = _pinned_fingerprint_root(identity_path or ROOT_IDENTITY_PIN)
    configured = _configured_url(config_path or CONFIG_PATH)
    if configured is None:
        report.same("no server address in config.toml to compare with root's record")
        return
    dialed = server_identity.dialed_address
    if dialed(discovery.http_base_of(configured)) == dialed(discovery.http_base_of(pinned)):
        report.same("root's server address is current")
        return
    if fingerprint is None:
        report.skip(f"the server address moved to {configured}, but there is no root-owned "
                    "identity pin to check it against; root's record left at " + pinned)
        return
    host, port = discovery.host_port_of(configured)
    probe = discovery.probe if probe is None else probe
    ok = False
    for i in range(max(1, attempts)):
        try:
            ok = bool(host) and bool(probe(host, port=port, timeout=3.0,
                                           expected_fingerprint=fingerprint))
        except Exception:  # noqa: BLE001
            ok = False
        if ok:
            break
        if i + 1 < attempts:
            sleep(5.0)
    if not ok:
        report.skip(f"the server address moved to {configured}, but it did not prove to root "
                    f"that it is this device's server this boot; root's record stays {pinned} "
                    "(tried again next boot)")
        return
    try:
        pin_path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        safe_write(pin_path.parent, pin_path.name, (configured + "\n").encode("utf-8"))
    except OSError as e:
        report.error(f"update root's server address to {configured}: {e}")
        return
    report.change(f"root's server address {pinned} -> {configured} (it signed for that "
                  "address with the pinned key); the clock follows it")


def satellite_home() -> Path | None:
    """The satellite account's home: ``$HOME`` as root's units set it, else
    the home this code tree lives in (``~/domovoi/satellite/``) — under
    ``sudo`` ``$HOME`` is usually root's own. Recognised by the venv and
    the checkout every satellite has; None when neither looks like one."""
    candidates: list[Path] = []
    env = os.environ.get("HOME")
    if env:
        candidates.append(Path(env))
    try:
        candidates.append(Path(__file__).resolve().parents[2])
    except IndexError:
        pass
    for home in candidates:
        if (home / "satellite-venv").is_dir() and (home / "domovoi" / "satellite").is_dir():
            return home
    return None


def config_path() -> Path:
    """config.toml: under ``$HOME`` when it is there (root's units set
    ``HOME``), else under :func:`satellite_home` (a manual ``sudo`` run)."""
    if CONFIG_PATH.exists():
        return CONFIG_PATH
    home = satellite_home()
    return home / ".domovoi" / "config.toml" if home is not None else CONFIG_PATH


def _device_state(nmcli: str, iface: str, run: Callable[..., Any]) -> tuple[int | None, str]:
    """NetworkManager's ``(state code, active connection name)`` for ``iface``."""
    out = _stdout(run, [nmcli, "-t", "-f", "GENERAL.STATE,GENERAL.CONNECTION",
                        "device", "show", iface])
    state: int | None = None
    conn = ""
    for line in (out or "").splitlines():
        key, _, value = line.partition(":")
        value = value.replace("\\:", ":").strip()
        if key.strip() == "GENERAL.STATE":
            m = re.match(r"(\d+)", value)
            state = int(m.group(1)) if m else None
        elif key.strip() == "GENERAL.CONNECTION":
            conn = "" if value == "--" else value
    return state, conn


def refresh_dhcp_name(nmcli: str, name: str, profiles: list[wifi_profiles.Profile],
                      run: Callable[..., Any], report: Report, *,
                      sleep: Callable[[float], None] = time.sleep,
                      wait: float = _LINK_WAIT_SEC,
                      iface: str = wifi_profiles.WIFI_IFACE) -> None:
    """After a rename that came too late for this boot's DHCP request: ask
    DHCP once more, under the new name, so the router's client list shows
    it now rather than after the next reboot.

    ``nmcli device modify wlan0 ipv4.dhcp-hostname <name>`` changes the
    ACTIVE connection only (nothing is saved in the profile) and
    NetworkManager reapplies it: an IPv4 setting changed, so it restarts its
    DHCP client, which asks again with the name given. No reassociation:
    the radio stays on the network. (A reapply with nothing changed would
    do nothing at all, which is why the name is passed.) Only ever called
    right after an actual rename; the next boot's request carries the new
    name by itself."""
    deadline = time.monotonic() + wait
    while True:
        state, conn = _device_state(nmcli, iface, run)
        if state == _NM_CONNECTED:
            break
        if time.monotonic() >= deadline:
            report.same(f"{iface} is not connected yet; it introduces itself to the router as "
                        f"{name!r} when it connects")
            return
        sleep(_NM_POLL_SEC)
    ap_names = {p.name for p in profiles if p.is_ap} | {wifi_profiles.AP_CONNECTION}
    if conn in ap_names:
        report.skip(f"{iface} is hosting {conn!r} rather than being on the house network; "
                    "no DHCP request to repeat")
        return
    rc, detail = _call(run, [nmcli, "device", "modify", iface, "ipv4.dhcp-hostname", name])
    if rc == 0:
        report.change(f"asked the router for this unit's address again as {name!r} "
                      f"(one DHCP exchange on {conn or iface!r}, nothing saved in the profile), "
                      "so the router shows the new name now")
    else:
        report.error(f"ask DHCP again as {name!r}: {detail}; the router learns the name "
                     "at the next reboot")


def ensure_early_unit(run: Callable[..., Any], report: Report, *,
                      template: Path | None = None, unit_dir: Path | None = None,
                      home: Path | str | None = None) -> None:
    """Install (or refresh) and enable ``domovoi-hostname.service``, the
    unit that sets the host name before NetworkManager starts. Rendered
    from the template in this code tree with the satellite account's home,
    written without following a link (:func:`safe_write`), enabled with
    ``systemctl --no-reload enable`` (the link only: nothing is reloaded or
    started mid-boot; it runs from the next boot). Idempotent: an
    identical, enabled unit is left alone."""
    template = template or EARLY_UNIT_TEMPLATE
    unit_dir = unit_dir or SYSTEMD_DIR
    home = home if home is not None else satellite_home()
    if home is None or not _SAFE_HOME.match(str(home)) or ".." in str(home).split("/"):
        report.skip(f"{EARLY_UNIT_NAME}: cannot tell the satellite account's home directory, "
                    "so the host name is still set later in the boot")
        return
    try:
        rendered = template.read_text(encoding="utf-8").replace("@HOME@", str(home)).encode("utf-8")
    except OSError as e:
        report.error(f"read {template.name}: {e}")
        return
    path = unit_dir / EARLY_UNIT_NAME
    existed = os.path.lexists(path)
    current: bytes | None = None
    if existed and not os.path.islink(path):
        try:
            current = path.read_bytes()
        except OSError:
            current = None
    if current != rendered:
        try:
            safe_write(unit_dir, EARLY_UNIT_NAME, rendered)
        except OSError as e:
            report.error(f"install {path}: {e}")
            return
        report.change(f"{'updated' if existed else 'installed'} {path}: the host name is set "
                      "before the network starts")
    else:
        report.same(f"{EARLY_UNIT_NAME} is current")
    if os.path.lexists(unit_dir / WANTS_DIRNAME / EARLY_UNIT_NAME):
        report.same(f"{EARLY_UNIT_NAME} is enabled")
        return
    rc, detail = _call(run, ["systemctl", "--no-reload", "enable", EARLY_UNIT_NAME])
    if rc == 0:
        report.change(f"enabled {EARLY_UNIT_NAME}: from the next boot the router learns this "
                      "unit's name from its first DHCP request")
    else:
        report.error(f"enable {EARLY_UNIT_NAME}: {detail}")


def _early_this_boot(path: Path, boot_id: str | None) -> dict[str, Any] | None:
    doc = read_report(path)
    if doc is None or boot_id is None or doc.get("boot_id") != boot_id:
        return None
    return doc


_BOOT_ID_NOW = object()


def apply(
    room_id: str | None,
    *,
    run: Callable[..., Any] = subprocess.run,
    when: str = "boot",
    report_path: Path | None = None,
    hostname_path: Path | None = None,
    hosts_path: Path | None = None,
    record_path: Path | None = None,
    suffix: str | None = None,
    sleep: Callable[[float], None] = time.sleep,
    nmcli: str | None = None,
    nm_wait: float = _NM_WAIT_SEC,
    link_wait: float = _LINK_WAIT_SEC,
    server_pin: Callable[[Report], None] | None = None,
    early_report_path: Path | None = None,
    unit_dir: Path | None = None,
    unit_template: Path | None = None,
    home: Path | str | None = None,
    boot_id: Any = _BOOT_ID_NOW,
) -> dict[str, Any]:
    """Apply the settings; return (and record) what changed. Never raises:
    a satellite must boot whatever this step manages.

    The host name goes FIRST, before anything waits for NetworkManager: at
    boot NetworkManager is starting beside this step and asks DHCP as soon
    as the Wi-Fi joins. When the before-the-network unit already renamed
    the unit this boot, its record is folded in here. When this run is the
    one that renames it (the first boot after the upgrade, before that unit
    existed), DHCP is asked once more under the new name."""
    report = Report()
    hostname: str | None = None
    boot = _boot_id() if boot_id is _BOOT_ID_NOW else boot_id
    renamed_before: list[str] | None = None
    if when == "boot":
        early = _early_this_boot(early_report_path or EARLY_REPORT_PATH, boot)
        if early is not None:
            for what in early.get("changed") or []:
                report.change(f"before the network started: {what}")
            renamed_before = early.get("renamed") if isinstance(early.get("renamed"), list) else None
            early_errors = [e for e in early.get("errors") or [] if isinstance(e, str)]
        else:
            early_errors = []
    else:
        early_errors = []
    try:
        errors_before = len(report.errors)
        hostname = set_hostname(room_id, run, report,
                                hostname_path=hostname_path, hosts_path=hosts_path,
                                record_path=record_path, suffix=suffix, restart_avahi=False)
        for what in early_errors:
            # Moot when this run managed what the early one could not.
            if len(report.errors) > errors_before:
                report.error(f"before the network started: {what}")
            else:
                report.same(f"before the network started, could not: {what} (done here instead)")
    except Exception as e:  # noqa: BLE001 — never take the boot down
        report.error(f"host name: unexpected failure: {e!r}")
    renamed_now = report.renamed
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
        # At setup the reboot that follows makes the next request carry it.
        if renamed_now and nm and when in ("boot", "manual"):
            refresh_dhcp_name(nm, renamed_now[1], profiles, run, report,
                              sleep=sleep, wait=link_wait)
    except Exception as e:  # noqa: BLE001
        report.error(f"unexpected failure: {e!r}")
    if renamed_now or renamed_before:
        try:
            restart_avahi_for((renamed_now or renamed_before)[1], run, report)
        except Exception as e:  # noqa: BLE001
            report.error(f"avahi: unexpected failure: {e!r}")
    try:
        ensure_early_unit(run, report, template=unit_template, unit_dir=unit_dir, home=home)
    except Exception as e:  # noqa: BLE001
        report.error(f"{EARLY_UNIT_NAME}: unexpected failure: {e!r}")
    if server_pin is not None:
        try:
            server_pin(report)
        except Exception as e:  # noqa: BLE001
            report.error(f"server address record: unexpected failure: {e!r}")
    if renamed_before and not report.renamed:
        report.renamed = renamed_before
    doc = report.document(room_id=room_id, hostname=hostname, when=when)
    doc["boot_id"] = boot
    write_report(doc, report_path or REPORT_PATH)
    if not report.changed and not report.errors:
        log.info("network settings v%d already in place; nothing changed", NET_SETTINGS_VERSION)
    return doc


def apply_before_network(
    room_id: str | None,
    *,
    run: Callable[..., Any] = subprocess.run,
    report_path: Path | None = None,
    hostname_path: Path | None = None,
    hosts_path: Path | None = None,
    record_path: Path | None = None,
    suffix: str | None = None,
    hostnamectl_timeout: float = _EARLY_HOSTNAMECTL_SEC,
) -> dict[str, Any]:
    """The host name alone, before NetworkManager starts
    (``domovoi-hostname.service``): nothing here waits for or asks the
    network, restarts a unit, or raises. Recorded in
    :data:`EARLY_REPORT_PATH` for the boot step to fold into its own."""
    report = Report()
    hostname: str | None = None
    try:
        hostname = set_hostname(room_id, run, report, hostname_path=hostname_path,
                                hosts_path=hosts_path, record_path=record_path, suffix=suffix,
                                hostnamectl_timeout=hostnamectl_timeout, restart_avahi=False)
    except Exception as e:  # noqa: BLE001
        report.error(f"unexpected failure: {e!r}")
    doc = report.document(room_id=room_id, hostname=hostname, when="before-network")
    write_report(doc, report_path or EARLY_REPORT_PATH)
    return doc


_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)


def safe_write(directory: Path, name: str, data: bytes, *, mode: int = 0o644,
               owner: tuple[int, int] | None = None) -> None:
    """Write ``directory/name`` atomically without following a link anywhere.

    The directory is opened once, refusing a symlink (``O_NOFOLLOW |
    O_DIRECTORY``), and everything after happens relative to that open
    directory: the temporary file is created ``O_CREAT | O_EXCL |
    O_NOFOLLOW`` (a link planted under its name is removed, never written
    through), its mode and owner are set on the open file (``fchmod`` /
    ``fchown``, never by path), and it is renamed over ``name`` — which
    replaces a link planted there rather than writing to its target.
    Running as root, the directory must also be root's. Raises OSError."""
    tmp = f".{name}.tmp{os.getpid()}"
    if os.open in os.supports_dir_fd and _NOFOLLOW and _DIRECTORY:
        dfd = os.open(directory, os.O_RDONLY | _DIRECTORY | _NOFOLLOW)
        try:
            geteuid = getattr(os, "geteuid", None)
            if geteuid is not None and geteuid() == 0 and owner is None:
                if os.fstat(dfd).st_uid != 0:
                    raise PermissionError(f"{directory} is not root's; not writing into it as root")
            try:
                os.unlink(tmp, dir_fd=dfd)
            except FileNotFoundError:
                pass
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, mode, dir_fd=dfd)
            try:
                view = memoryview(data)
                while view:
                    view = view[os.write(fd, view):]
                os.fchmod(fd, mode)
                if owner is not None:
                    os.fchown(fd, owner[0], owner[1])
                try:
                    os.fsync(fd)
                except OSError:
                    pass
            finally:
                os.close(fd)
            try:
                os.rename(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
            except OSError:
                try:
                    os.unlink(tmp, dir_fd=dfd)
                except OSError:
                    pass
                raise
        finally:
            os.close(dfd)
        return
    # A platform without dir_fd / O_NOFOLLOW (a developer's Windows box):
    # the same sequence by path. Nothing here runs as root there.
    target = Path(directory) / name
    tmp_path = Path(directory) / tmp
    tmp_path.unlink(missing_ok=True)
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), mode)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    os.replace(tmp_path, target)


def write_report(doc: dict[str, Any], path: Path) -> bool:
    """Atomically, into root's own directory (made 0755 if it is missing),
    0644 so the satellite user can read it. Never through a link."""
    try:
        path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        safe_write(path.parent, path.name, json.dumps(doc, indent=1).encode("utf-8"))
    except OSError as e:
        log.warning("could not write %s: %s", path, e)
        return False
    return True


# ─── what the client reads ────────────────────────────────────────────────


def read_report(path: Path | None = None) -> dict[str, Any] | None:
    try:
        doc = json.loads((path or REPORT_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


#: What the boot step did, as the health sample says it (``state``).
STEP_STATES = ("applied", "unchanged", "skipped", "failed", "not_run", "not_installed", "running")
#: For this long after boot, a record from an earlier boot means the step
#: has not finished yet (it starts beside the client and may wait 45 s for
#: NetworkManager), not that it did not run.
STEP_GRACE_SEC = 180.0
_DETAIL_MAX = 200

PROVISIONING_WANTS = SYSTEMD_DIR / WANTS_DIRNAME / PROVISIONING_UNIT_NAME


def boot_step_unit_state() -> str:
    """``enabled``, ``disabled`` (the unit file is there and nothing starts
    it) or ``missing``: whether anything runs this module at boot."""
    if not PROVISIONING_UNIT.exists():
        return "missing"
    if not os.path.lexists(PROVISIONING_WANTS):
        return "disabled"
    return "enabled"


def _cut(text: str) -> str:
    return text if len(text) <= _DETAIL_MAX else text[: _DETAIL_MAX - 3] + "..."


def _current(report: dict[str, Any] | None) -> bool:
    version = (report or {}).get("version")
    return isinstance(version, int) and not isinstance(version, bool) and version >= NET_SETTINGS_VERSION


def _strings(report: dict[str, Any], key: str) -> list[str]:
    return [x for x in report.get(key) or [] if isinstance(x, str)]


def _outcome(report: dict[str, Any]) -> tuple[str, str]:
    """``(state, detail)`` of one run, most telling first: a failure, then a
    host name left alone (the thing a person looks for on the router), then
    what changed, then anything else left alone, then nothing to do."""
    errors = _strings(report, "errors")
    if errors:
        more = f" (and {len(errors) - 1} more)" if len(errors) > 1 else ""
        return "failed", errors[0] + more
    skipped = _strings(report, "skipped")
    named = [s for s in skipped if "host name" in s]
    if named:
        return "skipped", named[0]
    changed = _strings(report, "changed")
    if changed:
        return "applied", "; ".join(changed)
    if skipped:
        return "skipped", skipped[0]
    return "unchanged", f"nothing to change (host name {report.get('hostname') or '?'})"


def assess(report: dict[str, Any] | None, *, unit_state: str, boot_id: str | None,
           uptime: float | None, this_boot: bool | None = None) -> tuple[dict[str, Any], str | None]:
    """``(net_step, reboot_needed)`` for the health sample.

    ``net_step`` is ``{state, detail, when, at, version}``: what the boot
    step did on this boot, or why there is nothing to say. ``reboot_needed``
    is set whenever the step has not run since the code that needs it
    arrived (no record, or one from an older version) on a unit where a
    reboot runs it, and when its run at this version failed (a reboot
    retries it); never while it may still be running this boot, and never
    on a unit where nothing runs it (a reboot would not help there)."""
    report = report if isinstance(report, dict) else None
    doc = report or {}
    status: dict[str, Any] = {
        "state": "unchanged",
        "detail": "",
        "when": doc.get("when") if isinstance(doc.get("when"), str) else None,
        "at": doc.get("ts") if isinstance(doc.get("ts"), (int, float)) and not isinstance(doc.get("ts"), bool) else None,
        "version": doc.get("version") if isinstance(doc.get("version"), int) and not isinstance(doc.get("version"), bool) else None,
    }
    current = _current(report)
    if this_boot is None:
        this_boot = report is not None and boot_id is not None and report.get("boot_id") == boot_id
    booting = uptime is not None and uptime < STEP_GRACE_SEC
    if unit_state != "enabled" and not current:
        status["state"] = "not_installed"
        status["detail"] = (
            "domovoi-provisioning.service is installed but not enabled, so nothing runs the "
            "network step at boot (sudo systemctl enable domovoi-provisioning)"
            if unit_state == "disabled" else
            "no domovoi-provisioning.service on this device, so nothing runs the network step "
            "at boot (a hand-built satellite or a spare device); on a satellite Pi, "
            "sudo ~/satellite-venv/bin/python -m satellite.net_settings applies it once"
        )
        return status, None
    if not current:
        if booting and not this_boot:
            status["state"] = "running"
            status["detail"] = f"the boot step is still running ({uptime:.0f} s since boot)"
            return status, None
        last = (f"; it last ran at version {status['version']}"
                if status["version"] is not None else "")
        status["state"] = "not_run"
        status["detail"] = (f"has not run since this code arrived (version "
                            f"{NET_SETTINGS_VERSION}){last}: it runs at boot")
        return status, (
            "network settings (no setup hotspot, Wi-Fi power saving off, host name "
            "domovoi-<room>, set before the Wi-Fi joins) are applied at boot: reboot this "
            "satellite once"
        )
    assert report is not None
    if unit_state == "enabled" and not this_boot and report.get("when") != "manual":
        state, detail = _outcome(report)
        if booting:
            status["state"] = "running"
            status["detail"] = _cut(f"the boot step is still running; last time: {state}: {detail}")
        else:
            status["state"] = "not_run"
            status["detail"] = _cut(f"did not run this boot; last run ({report.get('when') or 'boot'}): "
                                    f"{state}: {detail}")
        return status, None
    state, detail = _outcome(report)
    status["state"] = state
    status["detail"] = _cut(detail)
    if state == "failed":
        errors = _strings(report, "errors")
        return status, (
            f"the boot-time network settings did not all apply ({errors[0][:120]}"
            f"{f' and {len(errors) - 1} more' if len(errors) > 1 else ''}); "
            "reboot this satellite to retry"
        )
    return status, None


def reboot_needed(report: dict[str, Any] | None, *, unit_installed: bool) -> str | None:
    """Why this unit needs a reboot for its network settings, or None
    (:func:`assess`, the record taken to be this boot's)."""
    _status, reason = assess(report, unit_state="enabled" if unit_installed else "missing",
                             boot_id=None, uptime=None, this_boot=report is not None)
    return reason


def main(argv: list[str] | None = None) -> int:
    """``sudo ~/satellite-venv/bin/python -m satellite.net_settings`` — the
    same step by hand, for a unit without ``domovoi-provisioning.service``.

    ``--before-network`` is the host name alone, as
    ``domovoi-hostname.service`` runs it before NetworkManager starts. It
    always exits 0: NetworkManager starts after it either way, and what it
    could not do is in its record, which the boot step reports."""
    args = list(sys.argv[1:] if argv is None else argv)
    early = "--before-network" in args
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if sys.platform != "linux":
        log.error("this only runs on the satellite itself")
        return 0 if early else 2
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        log.error("run it as root (sudo): it changes the host name and NetworkManager profiles")
        return 0 if early else 2
    room = read_room_id(config_path())
    if early:
        try:
            apply_before_network(room)
        except Exception as e:  # noqa: BLE001 — never hold the network up
            log.warning("host name before the network: %r", e)
        return 0
    doc = apply(room, when="manual")
    print(json.dumps(doc, indent=1))
    return 1 if doc.get("errors") else 0


if __name__ == "__main__":
    sys.exit(main())
