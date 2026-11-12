"""One node of the network-name multicast test (scripts/dev/run-lan-name-test.sh).

Runs inside a throwaway container on a private Docker bridge, with the
repo's own responder (domovoi/lan_name/, standard library only: the
container has python3 and nothing else of Domovoi's).

    python3 lan_name_node.py respond --name domovoi --seconds 20
        announce the name (the core's asyncio runner, real UDP 5353), print
        each state as a JSON line, then stop with a goodbye.
    python3 lan_name_node.py query --name domovoi.local [--legacy] [--wait 2]
        ask once (group mode, or legacy unicast from an ephemeral port) and
        print the A records heard as a JSON line.
    python3 lan_name_node.py goodbye --name domovoi.local --wait 15
        listen on the group until a TTL-0 record for the name is heard.

Never run it on a real home network: it is for the test's bridge only.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import random
import socket
import struct
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from domovoi.lan_name import packet  # noqa: E402
from domovoi.lan_name import service as svc  # noqa: E402


def my_ip() -> str:
    return socket.gethostbyname(socket.gethostname())


def say(**kw) -> None:
    print(json.dumps(kw), flush=True)


async def respond(name: str, seconds: float, ip: str | None = None) -> int:
    os.environ["DOMOVOI_LAN_NAME_DIR"] = tempfile.mkdtemp()
    svc.requested_name = lambda raw=None: name   # no domovoi.config in here
    svc.enabled = lambda: True
    ip = ip or my_ip()

    def iface(addr):
        # A container's eth0 is a veth (under /sys/devices/virtual), which a
        # real server refuses to announce on; here it IS the test's LAN. The
        # interface index and prefix are the real ones (IP_PKTINFO and the
        # subnet check stay on).
        found = svc.lan_interface(addr)
        return dataclasses.replace(found, virtual=None) if found else None

    s = svc.LanNameService(lan_ip=lambda: ip, iface=iface)
    s.start()
    last = None
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        snap = s.snapshot()
        key = (snap["state"], snap["claimed"], s.responder.phase)
        if key != last:
            last = key
            say(event="state", ip=ip, phase=s.responder.phase, **snap)
        await asyncio.sleep(0.1)
    await s.stop()
    say(event="stopped", ip=ip)
    return 0


def _question(fqdn: str, qid: int) -> bytes:
    labels = tuple(x.encode() for x in fqdn.split("."))
    return struct.pack("!HHHHHH", qid, 0, 1, 0, 0, 0) + packet.encode_name(labels) + struct.pack("!HH", packet.T_A, 1)


def _group_socket(ip: str) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("", packet.PORT))
    s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, socket.inet_aton(packet.GROUP) + socket.inet_aton(ip))
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ip))
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
    return s


def query(fqdn: str, legacy: bool, wait: float) -> int:
    ip = my_ip()
    want = tuple(x.encode() for x in fqdn.lower().split("."))
    if legacy:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("", 0))
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ip))
    else:
        s = _group_socket(ip)
    qid = random.randint(1, 0xFFFF) if legacy else 0
    s.sendto(_question(fqdn, qid), (packet.GROUP, packet.PORT))
    s.settimeout(0.2)
    found: list[dict] = []
    end = time.monotonic() + wait
    while time.monotonic() < end:
        try:
            data, src = s.recvfrom(9000)
        except socket.timeout:
            continue
        try:
            m = packet.parse(data)
        except packet.ParseError:
            continue
        if not m.is_response:
            continue
        for r in m.records():
            if r.name == want and r.rtype == packet.T_A and len(r.rdata) == 4:
                found.append({"a": socket.inet_ntoa(r.rdata), "ttl": r.ttl, "from": src[0],
                              "unicast": legacy, "id": m.qid})
    s.close()
    say(event="query", name=fqdn, legacy=legacy, answers=found)
    return 0 if found else 1


def goodbye(fqdn: str, wait: float) -> int:
    ip = my_ip()
    want = tuple(x.encode() for x in fqdn.lower().split("."))
    s = _group_socket(ip)
    s.settimeout(0.2)
    end = time.monotonic() + wait
    say(event="listening", name=fqdn)
    while time.monotonic() < end:
        try:
            data, src = s.recvfrom(9000)
        except socket.timeout:
            continue
        try:
            m = packet.parse(data)
        except packet.ParseError:
            continue
        for r in m.records():
            if m.is_response and r.name == want and r.rtype == packet.T_A and r.ttl == 0:
                say(event="goodbye", name=fqdn, a=socket.inet_ntoa(r.rdata), source=src[0])
                return 0
    say(event="no_goodbye", name=fqdn)
    return 1


def forge(fqdn: str, target: str, seconds: float) -> int:
    """A hostile device: claim ``fqdn`` for 10.9.9.9, from port 5353, sent
    by unicast to ``target`` (or the group with ``group``) every 50 ms."""
    labels = tuple(x.encode() for x in fqdn.lower().split("."))
    pkt = packet.response(labels, socket.inet_aton("10.9.9.9"), 120)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("", packet.PORT))
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(my_ip()))
    dest = (packet.GROUP, packet.PORT) if target == "group" else (target, packet.PORT)
    say(event="forging", name=fqdn, to=target)
    sent = 0
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        s.sendto(pkt, dest)
        sent += 1
        time.sleep(0.05)
    s.close()
    say(event="forged", sent=sent)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=("respond", "query", "goodbye", "forge"))
    ap.add_argument("--name", required=True)
    ap.add_argument("--seconds", type=float, default=20)
    ap.add_argument("--wait", type=float, default=2)
    ap.add_argument("--legacy", action="store_true")
    ap.add_argument("--target", default="group")
    ap.add_argument("--ip", default=None, help="respond: the LAN address (default: the host name's)")
    a = ap.parse_args()
    if a.what == "respond":
        return asyncio.run(respond(a.name, min(a.seconds, 120), a.ip))
    if a.what == "query":
        return query(a.name, a.legacy, a.wait)
    if a.what == "forge":
        return forge(a.name, a.target, min(a.seconds, 30))
    return goodbye(a.name, a.wait)


if __name__ == "__main__":
    sys.exit(main())
