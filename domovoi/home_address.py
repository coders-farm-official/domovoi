"""Where plain ``http://`` is allowed: the home network only.

The household's own search address (Settings > Search > Use your own
search > Your own SearXNG) carries the words of every open-web lookup, so
plain http is allowed only to an address on the home network; anything
else must be ``https://``. The phone applies the same rule to its own
search address (``android/.../net/CleartextPolicy.kt``, which it also uses
for the server address); this module is its server-side twin, and
``domovoi/tests/test_home_address.py`` holds the two lists together.

Permitted in the clear (judged as written; a name is never resolved, so a
public name that happens to point at a home address still needs https):

* IPv4 10/8, 172.16/12, 192.168/16 (RFC 1918), link-local 169.254/16,
  loopback 127/8, and 100.64/10 (the shared space Tailscale-style overlays
  use);
* IPv6 loopback ``::1``, unique-local ``fc00::/7``, link-local
  ``fe80::/10``;
* ``localhost`` and names under :data:`PRIVATE_NAME_SUFFIXES`.

Loopback is allowed because plain http there never leaves the machine,
which is how the rest of Domovoi treats it (``egress.is_local_host``,
``transport_guard.host_allowed``, the phone's CleartextPolicy).

This is only the scheme rule. Where the server may connect at all is still
``net_safety`` (public fetches) and ``egress`` (the internet answer); the
own search goes through the egress client, as before.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

#: Names that only resolve on a home network (RFC 6762 .local, RFC 8375
#: .home.arpa, ICANN-reserved .internal, and the never-delegated .lan /
#: .home / .localhost home routers hand out). The phone's
#: CleartextPolicy.privateNameSuffixes is the same list.
PRIVATE_NAME_SUFFIXES: tuple[str, ...] = (".local", ".home.arpa", ".internal", ".lan", ".home", ".localhost")

_PRIVATE_V4 = tuple(ipaddress.IPv4Network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",   # RFC 1918
    "127.0.0.0/8",                                      # loopback
    "169.254.0.0/16",                                   # link-local
    "100.64.0.0/10",                                    # RFC 6598 shared space
))
_PRIVATE_V6 = tuple(ipaddress.IPv6Network(n) for n in (
    "::1/128",      # loopback
    "fc00::/7",     # unique-local
    "fe80::/10",    # link-local
))

#: What a refused address is told, in plain words.
HTTPS_UNLESS_HOME = "Use https:// unless the search is on your home network"


def plain_http_permitted(host: str | None) -> bool:
    """Whether plain http to ``host`` (a name or an IP literal, as a URL
    writes it; brackets and an IPv6 zone id are tolerated) is allowed."""
    h = (host or "").strip().lower().rstrip(".")
    if h.startswith("[") and "]" in h:
        h = h[1:h.index("]")]
    if not h:
        return False
    if h == "localhost" or h.endswith(PRIVATE_NAME_SUFFIXES):
        return True
    try:
        ip = ipaddress.ip_address(h.split("%", 1)[0])
    except ValueError:
        return False  # a name (or a shorthand like 127.1, which is no name a home router hands out)
    nets = _PRIVATE_V4 if ip.version == 4 else _PRIVATE_V6
    return any(ip in net for net in nets)


def own_search_address_problem(url: str) -> str | None:
    """None when ``url`` may be the household's own search address:
    ``https://`` anywhere, ``http://`` on the home network. Otherwise the
    plain sentence to show."""
    text = str(url or "").strip()
    try:
        parts = urlsplit(text)
        host = parts.hostname
    except ValueError:
        return "expected an address starting with http:// or https://"
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https") or not host:
        return "expected an address starting with http:// or https://"
    if scheme == "http" and not plain_http_permitted(host):
        return HTTPS_UNLESS_HOME
    return None


def _ip_is_home(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    nets = _PRIVATE_V4 if ip.version == 4 else _PRIVATE_V6
    return any(ip in net for net in nets)


def resolved_address_problem(url: str) -> str | None:
    """The second half of the rule, for when the address is *used*: a
    home-looking name (``box.lan``, ``nas.home.arpa``) that actually resolves
    to a public address (an ISP's wildcard DNS, a rebinding trick) is not on
    the home network, so plain http to it is refused. https is never
    affected, an IP literal was already judged as written, and a name that
    does not resolve is left to fail when the connection is tried. Blocking
    (a name lookup): call it off the event loop."""
    try:
        parts = urlsplit(str(url or "").strip())
        host = (parts.hostname or "").strip().lower().rstrip(".")
    except ValueError:
        return None
    if parts.scheme.lower() != "http" or not host:
        return None
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
        return None  # a literal: judged as written
    except ValueError:
        pass
    from domovoi import net_safety  # late: net_safety is heavier than this rule

    if any(not _ip_is_home(ip) for ip in net_safety.resolve_host(host)):
        return HTTPS_UNLESS_HOME
    return None
