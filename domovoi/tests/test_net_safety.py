"""``domovoi.net_safety`` — the one check every server-side fetch of a
caller-chosen URL goes through.

All DB-free (and DNS-free: ``resolve_host`` is patched, so a name resolves
to exactly what a test says it does). Covers the scheme allowlist, the
address ranges the server must never reach, the shorthand IPv4 literals a
resolver accepts, IPv6 forms that embed an IPv4, redirect chains that turn
private mid-way, and the byte cap.
"""

from __future__ import annotations

import ipaddress

import httpx
import pytest

from domovoi import net_safety

PUBLIC_V4 = ipaddress.ip_address("93.184.216.34")
PUBLIC_V6 = ipaddress.ip_address("2606:2800:220:1:248:1893:25c8:1946")


@pytest.fixture
def dns(monkeypatch):
    """A fake resolver: ``dns.set({"host": ["1.2.3.4"]})``. Anything not
    named does not resolve."""

    table: dict[str, list[str]] = {}

    def fake_resolve(host: str):
        return [ipaddress.ip_address(a) for a in table.get(host.lower(), [])]

    monkeypatch.setattr(net_safety, "resolve_host", fake_resolve)

    class DNS:
        def set(self, mapping: dict[str, list[str]]) -> None:
            table.clear()
            table.update({k.lower(): v for k, v in mapping.items()})

    d = DNS()
    d.set({"feeds.example.com": [str(PUBLIC_V4)]})
    return d


# ─── Schemes ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "file://C:/Windows/win.ini",
        "ftp://feeds.example.com/x.rss",
        "gopher://feeds.example.com/1",
        "concat:one.mp3|two.mp3",
        "data:text/plain;base64,aGk=",
        "//feeds.example.com/x.rss",
        "/etc/passwd",
        "",
        "   ",
    ],
)
def test_only_http_and_https_are_fetchable(dns, url) -> None:
    assert not net_safety.is_safe_outbound_url(url)


def test_a_public_http_url_is_fetchable(dns) -> None:
    assert net_safety.is_safe_outbound_url("http://feeds.example.com/show.rss")
    assert net_safety.is_safe_outbound_url("https://feeds.example.com:8443/show.rss")


# ─── Address ranges ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",        # loopback
        "127.1",            # …the shorthand a resolver still reads as loopback
        "127.0.1",
        "0x7f000001",       # …as hex
        "2130706433",       # …as a bare integer
        "0177.0.0.1",       # …as octal
        "0.0.0.0",
        "10.0.0.7",         # RFC 1918
        "172.16.4.2",       # RFC 1918
        "172.31.255.254",
        "192.168.1.10",     # RFC 1918
        "169.254.169.254",  # link-local / metadata
        "100.64.0.1",       # CGNAT
        "198.18.0.1",       # benchmarking
        "224.0.0.1",        # multicast
        "255.255.255.255",
        "[::1]",            # IPv6 loopback
        "[::]",
        "[fc00::1]",        # IPv6 ULA
        "[fd12:3456:789a::1]",
        "[fe80::1]",        # IPv6 link-local
        "[ff02::1]",        # IPv6 multicast
        "[::ffff:10.0.0.1]",    # IPv4-mapped private
        "[::ffff:7f00:1]",      # IPv4-mapped loopback
        "[2002:c0a8:0101::]",   # 6to4 wrapping 192.168.1.1
        "[64:ff9b::7f00:1]",    # NAT64 wrapping 127.0.0.1
    ],
)
def test_the_house_and_the_box_are_never_fetchable(dns, host) -> None:
    assert not net_safety.is_safe_outbound_url(f"http://{host}/anything")


@pytest.mark.parametrize("host", ["localhost", "LOCALHOST", "api.localhost", "localhost."])
def test_localhost_by_name_is_never_fetchable(dns, host) -> None:
    assert not net_safety.is_safe_outbound_url(f"http://{host}:11434/api/tags")


@pytest.mark.parametrize(
    "literal,expected",
    [
        ("127.1", "127.0.0.1"),
        ("0x7f000001", "127.0.0.1"),
        ("2130706433", "127.0.0.1"),
        ("0177.0.0.1", "127.0.0.1"),
        ("192.168.1", "192.168.0.1"),
        ("93.184.216.34", "93.184.216.34"),
    ],
)
def test_shorthand_literals_are_read_as_the_address_they_denote(literal, expected) -> None:
    assert net_safety.parse_ip_literal(literal) == ipaddress.ip_address(expected)


def test_a_hostname_is_not_an_ip_literal() -> None:
    assert net_safety.parse_ip_literal("feeds.example.com") is None


# ─── Names ────────────────────────────────────────────────────────────────


def test_a_name_resolving_into_the_house_is_refused(dns) -> None:
    dns.set({"evil.example.com": ["192.168.1.50"]})
    assert not net_safety.is_safe_outbound_url("http://evil.example.com/x")


def test_a_name_with_one_private_answer_among_public_ones_is_refused(dns) -> None:
    dns.set({"mixed.example.com": [str(PUBLIC_V4), "10.1.2.3"]})
    assert not net_safety.is_safe_outbound_url("http://mixed.example.com/x")


def test_a_name_resolving_only_to_public_addresses_is_fetchable(dns) -> None:
    dns.set({"news.example.com": [str(PUBLIC_V4), str(PUBLIC_V6)]})
    assert net_safety.is_safe_outbound_url("http://news.example.com/rss")


def test_a_name_that_does_not_resolve_is_refused_for_a_fetch(dns) -> None:
    dns.set({})
    assert not net_safety.is_safe_outbound_url("http://nowhere.example.com/rss")


def test_a_name_that_does_not_resolve_can_still_be_stored(dns) -> None:
    """Saving a subscription must survive a feed whose DNS is down (or a
    household offline); the fetch itself still resolves and re-checks."""
    dns.set({})
    assert net_safety.is_safe_outbound_url(
        "http://nowhere.example.com/rss", require_resolution=False
    )
    assert not net_safety.is_safe_outbound_url(
        "http://127.0.0.1/rss", require_resolution=False
    )
    assert not net_safety.is_safe_outbound_url(
        "file:///etc/passwd", require_resolution=False
    )


def test_the_reason_names_what_was_wrong(dns) -> None:
    assert "not http(s)" in (net_safety.check_outbound_url("file:///etc/passwd") or "")
    assert "localhost" in (net_safety.check_outbound_url("http://localhost/x") or "")
    dns.set({"evil.example.com": ["10.0.0.1"]})
    assert "non-public" in (
        net_safety.check_outbound_url("http://evil.example.com/x") or ""
    )


def test_require_safe_outbound_url_raises_with_the_url_on_it(dns) -> None:
    with pytest.raises(net_safety.UnsafeOutboundURL) as exc:
        net_safety.require_safe_outbound_url("http://127.0.0.1:6370/v1/admin/snapshot")
    assert exc.value.url == "http://127.0.0.1:6370/v1/admin/snapshot"


@pytest.mark.asyncio
async def test_the_async_form_agrees_with_the_sync_one(dns) -> None:
    dns.set({"news.example.com": [str(PUBLIC_V4)]})
    assert await net_safety.acheck_outbound_url("http://news.example.com/x") is None
    assert await net_safety.acheck_outbound_url("http://10.0.0.1/x") is not None
    with pytest.raises(net_safety.UnsafeOutboundURL):
        await net_safety.arequire_safe_outbound_url("http://[::1]/x")


# ─── Redirects ────────────────────────────────────────────────────────────


def _transport(routes: dict[str, httpx.Response]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        key = str(request.url)
        assert key in routes, f"unexpected fetch of {key}"
        return routes[key]

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_a_redirect_into_the_house_is_refused(dns) -> None:
    dns.set({"feeds.example.com": [str(PUBLIC_V4)]})
    transport = _transport(
        {
            "http://feeds.example.com/show.rss": httpx.Response(
                302, headers={"location": "http://169.254.169.254/latest/meta-data/"}
            )
        }
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(net_safety.UnsafeOutboundURL):
            await net_safety.fetch_bytes(
                "http://feeds.example.com/show.rss", max_bytes=1024, client=client
            )


@pytest.mark.asyncio
async def test_a_redirect_chain_that_stays_public_is_followed(dns) -> None:
    dns.set({"feeds.example.com": [str(PUBLIC_V4)], "cdn.example.com": [str(PUBLIC_V4)]})
    transport = _transport(
        {
            "http://feeds.example.com/show.rss": httpx.Response(
                301, headers={"location": "https://cdn.example.com/show.rss"}
            ),
            "https://cdn.example.com/show.rss": httpx.Response(200, content=b"<rss/>"),
        }
    )
    async with httpx.AsyncClient(transport=transport) as client:
        result = await net_safety.fetch_bytes(
            "http://feeds.example.com/show.rss", max_bytes=1024, client=client
        )
    assert result.content == b"<rss/>"
    assert result.url == "https://cdn.example.com/show.rss"


@pytest.mark.asyncio
async def test_a_relative_redirect_is_resolved_and_checked(dns) -> None:
    dns.set({"feeds.example.com": [str(PUBLIC_V4)]})
    transport = _transport(
        {
            "http://feeds.example.com/show.rss": httpx.Response(
                302, headers={"location": "/moved.rss"}
            ),
            "http://feeds.example.com/moved.rss": httpx.Response(200, content=b"ok"),
        }
    )
    async with httpx.AsyncClient(transport=transport) as client:
        result = await net_safety.fetch_bytes(
            "http://feeds.example.com/show.rss", max_bytes=1024, client=client
        )
    assert result.content == b"ok"


@pytest.mark.asyncio
async def test_an_endless_redirect_chain_stops(dns) -> None:
    dns.set({"feeds.example.com": [str(PUBLIC_V4)]})
    transport = _transport(
        {
            "http://feeds.example.com/loop": httpx.Response(
                302, headers={"location": "http://feeds.example.com/loop"}
            )
        }
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(net_safety.TooManyRedirects):
            await net_safety.fetch_bytes(
                "http://feeds.example.com/loop", max_bytes=1024, client=client
            )


# ─── Byte cap ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_body_over_the_cap_stops(dns) -> None:
    dns.set({"feeds.example.com": [str(PUBLIC_V4)]})
    transport = _transport(
        {"http://feeds.example.com/big": httpx.Response(200, content=b"x" * 5000)}
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(net_safety.ResponseTooLarge):
            await net_safety.fetch_bytes(
                "http://feeds.example.com/big", max_bytes=1000, client=client
            )


@pytest.mark.asyncio
async def test_a_declared_length_over_the_cap_stops_before_the_body(dns) -> None:
    dns.set({"feeds.example.com": [str(PUBLIC_V4)]})
    transport = _transport(
        {
            "http://feeds.example.com/big": httpx.Response(
                200, content=b"x" * 10, headers={"content-length": "999999999"}
            )
        }
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(net_safety.ResponseTooLarge):
            await net_safety.fetch_bytes(
                "http://feeds.example.com/big", max_bytes=1000, client=client
            )


@pytest.mark.asyncio
async def test_a_body_under_the_cap_comes_back_whole(dns) -> None:
    dns.set({"feeds.example.com": [str(PUBLIC_V4)]})
    transport = _transport(
        {"http://feeds.example.com/ok": httpx.Response(200, content=b"y" * 900)}
    )
    async with httpx.AsyncClient(transport=transport) as client:
        result = await net_safety.fetch_bytes(
            "http://feeds.example.com/ok", max_bytes=1000, client=client
        )
    assert len(result.content) == 900
    assert result.status_code == 200


def test_the_blocking_fetcher_checks_the_same_way(dns) -> None:
    dns.set({"feeds.example.com": [str(PUBLIC_V4)]})
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"<rss/>")
    )
    with httpx.Client(transport=transport) as client:
        result = net_safety.fetch_bytes_sync(
            "http://feeds.example.com/show.rss", max_bytes=1024, client=client
        )
        assert result.content == b"<rss/>"
        with pytest.raises(net_safety.UnsafeOutboundURL):
            net_safety.fetch_bytes_sync(
                "http://127.0.0.1:11434/api/tags", max_bytes=1024, client=client
            )
