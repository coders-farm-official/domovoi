"""Whether a request comes from this PC itself: the rule the Windows tray's
loopback routes share.

The tray on a Windows install reaches the core and the dashboard in WSL
through mirrored networking's loopback (hostAddressLoopback), where a
Windows process's 127.0.0.1 arrives as 127.0.0.1; a phone or a laptop on
the LAN arrives as its own address. So a route that answers only "the tray
on the server's own PC" (the satellite card's one-time pass,
``web/backend/api/satellite_media.py``; the Windows update's state and
results, ``domovoi/windows_side.py``) answers a loopback TCP peer only.

uvicorn rewrites the peer from ``X-Forwarded-For`` when a loopback peer
sends one (its ``--forwarded-allow-ips`` default), and the web process
itself forwards each dashboard caller's address that way, so with any
forwarding header present the peer can't be taken at its word (round 4's
WIN-7: loopback XFF trust on WSL): such a request is never "this PC". The
tray talks to these routes directly and sends none.

Every Windows account on the PC shares that loopback: the shared-PC trust
boundary in docs/WINDOWS_HOST.md. A route that relies on this check alone
must not do more than that boundary allows.

Stdlib only; importable by both processes.
"""

from __future__ import annotations

import ipaddress
from typing import Any

#: Headers a proxy adds to say who it relays for.
FORWARDING_HEADERS = ("x-forwarded-for", "forwarded", "x-real-ip", "x-forwarded-host")


def from_this_pc(request: Any) -> bool:
    """True when the TCP peer is this machine's loopback and nothing in the
    request claims to relay for someone else."""
    if any(request.headers.get(h) for h in FORWARDING_HEADERS):
        return False
    host = request.client.host if request.client else ""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback
