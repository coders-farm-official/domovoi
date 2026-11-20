"""The household reaches the same ports on every host (mac-installer PLAN
4.2a; Windows PLAN 4.10).

Three lists name the TCP ports other devices on the home network may open:
the Windows installer's Hyper-V firewall rules (``windows/common/Ports.cs``,
the ``PortSpec`` rows with a rule name), the Mac's pf redirect
(``mac/Sources/DomovoiMacKit/PfAnchor.swift``, ``PfRules.lanPorts``) and the
fence inside the Mac's VM (``scripts/linux/provision.sh``,
``VM_GUARD_LAN_PORTS``). A port added to one and not the others is a room or
a plugin that works on one host and not the next, or a port open on one
that is closed elsewhere. They must stay equal, and none of them may be the
database (6432), the AI engine (11434) or a room's control port (6650+).
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _expand(spans: list[tuple[int, int]]) -> set[int]:
    out: set[int] = set()
    for a, b in spans:
        out.update(range(a, b + 1))
    return out


def windows_ports() -> set[int]:
    text = (REPO / "windows" / "common" / "Ports.cs").read_text(encoding="utf-8")
    # A row's sixth argument, when it has one, says the port is optional
    # (the short address, 80): still a firewall row.
    spans = [(int(a), int(b)) for a, b in re.findall(
        r'new PortSpec\((\d+), (\d+), "[^"]*", true, "Domovoi-[^"]+"(?:, (?:true|false))?\)', text)]
    assert spans, "no firewall rows found in Ports.cs"
    return _expand(spans)


def mac_pf_ports() -> set[int]:
    text = (REPO / "mac" / "Sources" / "DomovoiMacKit" / "PfAnchor.swift").read_text(encoding="utf-8")
    m = re.search(r"lanPorts: \[ClosedRange<Int>\] = \[([^\]]+)\]", text)
    assert m, "PfRules.lanPorts not found"
    spans = [(int(a), int(b)) for a, b in re.findall(r"(\d+)\.\.\.(\d+)", m.group(1))]
    return _expand(spans)


def vm_guard_ports() -> set[int]:
    text = (REPO / "scripts" / "linux" / "provision.sh").read_text(encoding="utf-8")
    m = re.search(r'^VM_GUARD_LAN_PORTS="([^"]+)"$', text, re.M)
    assert m, "VM_GUARD_LAN_PORTS not found"
    spans = []
    for part in m.group(1).split(","):
        part = part.strip()
        a, _, b = part.partition("-")
        spans.append((int(a), int(b or a)))
    return _expand(spans)


def test_the_three_lists_are_one() -> None:
    win, mac, vm = windows_ports(), mac_pf_ports(), vm_guard_ports()
    assert mac == win, f"pf redirect vs Windows firewall: {sorted(mac ^ win)}"
    assert vm == win, f"VM fence vs Windows firewall: {sorted(vm ^ win)}"


def test_none_of_them_opens_what_stays_inside() -> None:
    inside = {6432, 11434} | set(range(6650, 6700))
    for name, ports in (("windows", windows_ports()), ("mac pf", mac_pf_ports()), ("vm fence", vm_guard_ports())):
        assert not (ports & inside), f"{name} opens {sorted(ports & inside)}"
        assert {6369, 6370} <= ports, f"{name} lacks the dashboard or the core"
