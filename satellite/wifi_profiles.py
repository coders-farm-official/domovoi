"""NetworkManager Wi-Fi profiles: which one is home, and how to get back to it.

A satellite has at most two kinds of Wi-Fi profile: the house network it
was set up on, and — on a unit adopted through the setup portal — the
``domovoi-setup`` access point it hosted while nobody had told it that
network yet. Until 2026-10-12 every recovery ran ``nmcli device connect
wlan0``, which lets NetworkManager pick "the best available profile" for
the radio. An access-point profile is always available (it needs no
network to be in range), so a satellite that lost the house network — a
router rebooting is enough — could recover straight onto its own setup
hotspot and stay there: NetworkManager reports an AP as connected, there is
no gateway to be silent, and nothing escalated. The basement unit sat like
that for 18 days.

So every recovery now names the HOME profile, and refuses rather than
guesses when it cannot:

* a unit set up by this code has its house profile named
  :data:`HOME_CONNECTION` and a sudoers line for exactly
  ``nmcli connection up id domovoi-home``;
* an older unit has neither (its profile is named after the network, and
  its sudoers allows only ``nmcli device connect wlan0``). The house
  profile by uuid is tried first anyway (a unit whose owner allowed it gets
  it); then the old command, but only where it cannot land on an access
  point: no AP profile is left on the unit (the boot-time step deletes
  ``domovoi-setup`` after the upgrade and a reboot), or the house network
  is in the last scan AND its profile was used more recently than every AP
  profile — NetworkManager picks the most recently used available profile,
  and an AP is always available. Once the radio has hosted an AP, that AP
  is the most recent, so the old command cannot run again: no loop.

And never at all while the link is up and the house network is not in the
last scan: reconnecting then can only drop a working association.

Stdlib only and read-only: listing profiles and the cached scan needs no
privilege. Whatever this module decides is run by the caller, through sudo.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger(__name__)

#: The setup hotspot's profile (satellite/portal_transport.py AP_CONNECTION).
AP_CONNECTION = "domovoi-setup"
#: The house network's profile, on a unit set up since 2026-10-12.
HOME_CONNECTION = "domovoi-home"
#: The one radio a satellite has.
WIFI_IFACE = "wlan0"
#: Absolute, because sudoers matches the path.
NMCLI = "/usr/bin/nmcli"

_TIMEOUT_SEC = 10.0
#: The mode of a profile whose details nmcli would not give.
UNKNOWN_MODE = "?"


def split_terse(line: str) -> list[str]:
    """One ``nmcli -t`` row -> its fields. nmcli escapes a colon inside a
    value as ``\\:`` (and a backslash as ``\\\\``), so a plain split turns
    ``Guest\\:5G`` into two fields."""
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


@dataclass(frozen=True)
class Profile:
    name: str
    uuid: str
    # 802-11-wireless.mode: "infrastructure" (a client of an AP), "ap",
    # "adhoc", "mesh", "" when the profile leaves it unset (NetworkManager's
    # default, a client), or UNKNOWN_MODE when nmcli could not be asked.
    mode: str = ""
    ssid: str = ""
    active: bool = False
    # When NetworkManager last activated it (connection.timestamp, epoch
    # seconds; 0 = never or unknown). It picks the most recent of the
    # profiles available when nobody names one.
    timestamp: int = 0

    @property
    def is_ap(self) -> bool:
        # Unknown counts as an AP: a profile whose mode could not be read
        # must never be taken for the house network, nor let `device
        # connect` run as if no AP profile were there.
        return self.mode in ("ap", UNKNOWN_MODE) or self.name == AP_CONNECTION


def _run(run: Callable[..., Any], argv: list[str]) -> str | None:
    try:
        r = run(argv, capture_output=True, text=True, timeout=_TIMEOUT_SEC)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if getattr(r, "returncode", 1) != 0:
        return None
    out = getattr(r, "stdout", "") or ""
    if isinstance(out, bytes):
        out = out.decode("utf-8", "replace")
    return out


def profile_details(uuid: str, run: Callable[..., Any] = subprocess.run,
                    nmcli: str = "nmcli") -> tuple[str, str] | None:
    """``(mode, ssid)`` of one saved profile, or None when nmcli would not
    say. The ``-t -f`` form prints ``field:value`` per line."""
    out = _run(run, [nmcli, "-t", "-f", "802-11-wireless.mode,802-11-wireless.ssid",
                     "connection", "show", "uuid", uuid])
    if out is None:
        return None
    mode = ssid = ""
    for line in out.splitlines():
        row = split_terse(line.rstrip("\r"))
        key = row[0].strip()
        # Rejoined: a colon nmcli escaped is already one field, and one it
        # did not escape is put back.
        value = ":".join(row[1:])
        if key == "802-11-wireless.mode":
            mode = value.strip().lower()
        elif key == "802-11-wireless.ssid":
            ssid = value.strip()
    return mode, ssid


def list_wifi_profiles(run: Callable[..., Any] = subprocess.run,
                       nmcli: str = "nmcli") -> list[Profile] | None:
    """Every saved Wi-Fi profile, or None when NetworkManager could not be
    asked (and nothing may be decided from an empty list)."""
    out = _run(run, [nmcli, "-t", "-f", "NAME,UUID,TYPE,ACTIVE,TIMESTAMP", "connection", "show"])
    if out is None:
        return None
    profiles: list[Profile] = []
    for line in out.splitlines():
        row = split_terse(line.rstrip("\r"))
        if len(row) < 3 or row[2] not in ("802-11-wireless", "wifi"):
            continue
        name, uuid = row[0], row[1]
        active = len(row) > 3 and row[3].strip().lower() == "yes"
        try:
            stamp = int(row[4]) if len(row) > 4 and row[4].strip() else 0
        except ValueError:
            stamp = 0
        details = profile_details(uuid, run, nmcli) if uuid else None
        mode, ssid = details if details is not None else (UNKNOWN_MODE, "")
        profiles.append(Profile(name=name, uuid=uuid, mode=mode, ssid=ssid,
                                active=active, timestamp=stamp))
    return profiles


def visible_ssids(run: Callable[..., Any] = subprocess.run, nmcli: str = "nmcli",
                  iface: str = WIFI_IFACE) -> set[str] | None:
    """The SSIDs in NetworkManager's LAST scan. ``--rescan no``: asking for a
    fresh scan would take the radio off-channel for seconds, on a link that
    may be the only thing still working. None when nmcli would not say."""
    out = _run(run, [nmcli, "-t", "-f", "SSID", "device", "wifi", "list",
                     "ifname", iface, "--rescan", "no"])
    if out is None:
        return None
    seen: set[str] = set()
    for line in out.splitlines():
        row = split_terse(line.rstrip("\r"))
        if row and row[0]:
            seen.add(row[0])
    return seen


def home_profile(profiles: list[Profile], last_ssid: str | None = None, *,
                 strict: bool = False) -> Profile | None:
    """The house network's profile: the one named :data:`HOME_CONNECTION`;
    else the client profile for the network the radio was last on; else the
    active client profile; else the only one. Never an access point.
    ``strict`` (root's boot-time step): none rather than a guess among
    several."""
    clients = [p for p in profiles if not p.is_ap]
    if not clients:
        return None
    for p in clients:
        if p.name == HOME_CONNECTION:
            return p
    if last_ssid:
        for p in clients:
            if p.ssid == last_ssid:
                return p
    for p in clients:
        if p.active:
            return p
    if len(clients) == 1:
        return clients[0]
    if strict:
        return None
    # Several client profiles and nothing to choose by. Any of them is
    # "home" to NetworkManager; the most recently used is the likeliest.
    return max(clients, key=lambda p: p.timestamp)


@dataclass(frozen=True)
class Step:
    label: str
    argv: list[str]


@dataclass
class Plan:
    """What a recovery may run, in order (each through ``sudo -n``), or
    nothing and why."""

    steps: list[Step] = field(default_factory=list)
    reason: str = ""
    home: Profile | None = None
    home_visible: bool = False
    ap_profiles: list[str] = field(default_factory=list)


def connection_up_argv() -> list[str]:
    return [NMCLI, "connection", "up", "id", HOME_CONNECTION]


def device_connect_argv() -> list[str]:
    return [NMCLI, "device", "connect", WIFI_IFACE]


def connection_up_uuid_argv(uuid: str) -> list[str]:
    return [NMCLI, "connection", "up", "uuid", uuid]


def plan_recovery(
    profiles: list[Profile] | None,
    *,
    visible: set[str] | None,
    associated: bool,
    ap_active: bool = False,
    last_ssid: str | None = None,
) -> Plan:
    """Decide how to bring the house network back, or that it must not be
    tried. ``associated``: the radio is a client of some AP right now (the
    link is NOT genuinely down). ``ap_active``: the radio is hosting one."""
    if profiles is None:
        return Plan(reason="NetworkManager would not list its profiles; not guessing")
    home = home_profile(profiles, last_ssid)
    aps = [p.name for p in profiles if p.is_ap]
    if home is None:
        return Plan(reason="no saved house Wi-Fi profile on this unit", ap_profiles=aps)
    client_ssids = {p.ssid for p in profiles if not p.is_ap and p.ssid}
    home_visible = bool(visible) and bool(client_ssids & set(visible or ()))
    plan = Plan(home=home, home_visible=home_visible, ap_profiles=aps)
    if associated and not ap_active and not home_visible:
        plan.reason = (
            f"the house network {home.ssid or home.name!r} is not in the last "
            "scan and the radio is still associated; reconnecting would only "
            "drop a working link"
        )
        return plan
    if home.name == HOME_CONNECTION:
        plan.steps.append(Step(f"connection up id {HOME_CONNECTION}", connection_up_argv()))
    elif home.uuid:
        # An older card's house profile, by its uuid: sudo refuses it
        # unless the owner allowed it, and the next step is tried then.
        plan.steps.append(Step(f"connection up uuid {home.uuid}", connection_up_uuid_argv(home.uuid)))
    # The pre-2026-10-12 command, for units whose sudoers allows nothing
    # else: only where NetworkManager's own choice cannot be an AP. With AP
    # profiles saved it picks the most recently used AVAILABLE profile, so
    # the house network must be in sight AND newer than every AP profile.
    newest_ap = max((p.timestamp for p in profiles if p.is_ap), default=0)
    if not aps or (home_visible and not ap_active and home.timestamp > newest_ap):
        plan.steps.append(Step(f"device connect {WIFI_IFACE}", device_connect_argv()))
    if not plan.steps:
        plan.reason = (
            f"the setup hotspot profile ({', '.join(aps)}) is still on this unit "
            f"and the house network is not in the last scan (or was used less "
            f"recently than it), so `nmcli device connect` could raise the "
            f"hotspot instead; not running it (a reboot after the satellite "
            f"upgrade removes that profile)"
        )
    else:
        plan.reason = (
            f"bringing back {home.name!r}"
            + (" (in the last scan)" if home_visible else " (the link is down)")
        )
    return plan
