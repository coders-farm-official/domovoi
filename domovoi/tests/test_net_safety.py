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


# ─── The operator's allowlist (OUTBOUND_ALLOW_HOSTS) ──────────────────────
#
# The one escape hatch, and it is server configuration: an endpoint the
# person who owns the box named on purpose. Empty by default, matched on
# the host AS WRITTEN and exactly, port included.


@pytest.fixture
def allowlist(monkeypatch):
    """``allowlist("127.0.0.1:6391")`` sets the setting for one test (and
    pins it to empty otherwise, so a value in this box's .env can't leak
    into the assertions)."""
    from domovoi.config import settings

    def set_to(raw: str) -> None:
        monkeypatch.setattr(settings, "outbound_allow_hosts", raw, raising=False)

    set_to("")
    return set_to


def test_the_allowlist_ships_empty() -> None:
    """A fresh install refuses exactly what it refused before the key
    existed — the default must never become "convenient"."""
    from domovoi.config import Settings

    assert Settings.model_fields["outbound_allow_hosts"].default == ""
    assert net_safety.parse_allow_entries("") == ()


def test_with_no_allowlist_the_loopback_fixture_url_is_still_refused(dns, allowlist) -> None:
    assert net_safety.allow_entries() == ()
    assert not net_safety.is_safe_outbound_url("http://127.0.0.1:6391/podcast/feed.xml")
    assert "non-public" in (
        net_safety.check_outbound_url("http://127.0.0.1:6391/podcast/feed.xml") or ""
    )


def test_an_allowlisted_host_and_port_is_fetchable(dns, allowlist) -> None:
    allowlist("127.0.0.1:6391")
    assert net_safety.allow_entries() == (("127.0.0.1", 6391),)
    assert net_safety.check_outbound_url("http://127.0.0.1:6391/podcast/feed.xml") is None
    assert net_safety.is_safe_outbound_url("http://127.0.0.1:6391/news/tech.xml")
    # …and storing one (the endpoints that only save a URL) agrees.
    assert net_safety.is_safe_outbound_url(
        "http://127.0.0.1:6391/news/tech.xml", require_resolution=False
    )


def test_another_port_on_the_allowlisted_address_is_still_refused(dns, allowlist) -> None:
    allowlist("127.0.0.1:6391")
    for url in (
        "http://127.0.0.1:9999/x",
        "http://127.0.0.1:6370/v1/admin/snapshot",  # the core's own API
        "http://127.0.0.1/x",                       # port 80 by scheme
        "https://127.0.0.1/x",                      # port 443 by scheme
    ):
        assert not net_safety.is_safe_outbound_url(url), url


def test_another_host_in_the_same_blocked_range_is_still_refused(dns, allowlist) -> None:
    allowlist("127.0.0.1:6391")
    for url in (
        "http://127.0.0.2:6391/x",
        "http://127.0.0.10:6391/x",   # not a prefix match
        "http://192.168.1.50:6391/x",
        "http://10.0.0.1:6391/x",
        "http://169.254.169.254:6391/latest/meta-data/",
        "http://[::1]:6391/x",
    ):
        assert not net_safety.is_safe_outbound_url(url), url


def test_a_name_that_merely_resolves_to_the_allowlisted_address_is_refused(dns, allowlist) -> None:
    """The hole a resolved-address allowlist would open: allowlisting
    127.0.0.1:6391 must not hand an attacker-chosen name the same pass
    just because DNS points it at loopback."""
    allowlist("127.0.0.1:6391")
    dns.set({"evil.example.com": ["127.0.0.1"]})
    assert not net_safety.is_safe_outbound_url("http://evil.example.com:6391/x")
    assert not net_safety.is_safe_outbound_url("http://evil.example.com/x")


def test_an_allowlisted_name_is_matched_by_name_and_exactly(dns, allowlist) -> None:
    allowlist("fixtures.example.com:6391")
    # Every name here resolves into the house, so the ONLY thing that can
    # let one through is an exact match on the entry.
    dns.set({
        "fixtures.example.com": ["10.1.2.3"],
        "evil.fixtures.example.com": ["10.1.2.3"],
        "fixtures.example.com.evil.test": ["10.1.2.3"],
        "notfixtures.example.com": ["10.1.2.3"],
    })
    assert net_safety.is_safe_outbound_url("http://fixtures.example.com:6391/rss")
    assert net_safety.is_safe_outbound_url("http://FIXTURES.Example.COM.:6391/rss")
    for url in (
        "http://evil.fixtures.example.com:6391/rss",
        "http://fixtures.example.com.evil.test:6391/rss",
        "http://notfixtures.example.com:6391/rss",
        "http://fixtures.example.com:6392/rss",
    ):
        assert not net_safety.is_safe_outbound_url(url), url


def test_a_bare_host_entry_permits_every_port_on_that_host(dns, allowlist) -> None:
    """Documented as the bigger hammer: no port means no port rule."""
    allowlist("127.0.0.1")
    assert net_safety.allow_entries() == (("127.0.0.1", None),)
    assert net_safety.is_safe_outbound_url("http://127.0.0.1:6391/x")
    assert net_safety.is_safe_outbound_url("http://127.0.0.1:9999/x")
    assert net_safety.is_safe_outbound_url("https://127.0.0.1/x")
    assert not net_safety.is_safe_outbound_url("http://127.0.0.2:6391/x")


def test_shorthand_spellings_of_the_allowlisted_address_are_the_same_endpoint(dns, allowlist) -> None:
    allowlist("127.0.0.1:6391")
    assert net_safety.is_safe_outbound_url("http://127.1:6391/x")
    assert net_safety.is_safe_outbound_url("http://0x7f000001:6391/x")
    assert net_safety.is_safe_outbound_url("http://2130706433:6391/x")
    assert not net_safety.is_safe_outbound_url("http://127.1:9999/x")


def test_the_allowlist_does_not_widen_the_scheme_allowlist(dns, allowlist) -> None:
    allowlist("127.0.0.1:6391")
    for url in (
        "file://127.0.0.1:6391/etc/passwd",
        "ftp://127.0.0.1:6391/x",
        "gopher://127.0.0.1:6391/1",
    ):
        assert not net_safety.is_safe_outbound_url(url), url


def test_localhost_by_name_has_to_be_named_before_it_is_allowed(dns, allowlist) -> None:
    allowlist("127.0.0.1:6391")
    assert not net_safety.is_safe_outbound_url("http://localhost:6391/x")
    allowlist("localhost:6391")
    assert net_safety.is_safe_outbound_url("http://localhost:6391/x")
    assert not net_safety.is_safe_outbound_url("http://api.localhost:6391/x")
    assert not net_safety.is_safe_outbound_url("http://localhost:11434/api/tags")


def test_an_allowlisted_endpoint_does_not_have_to_resolve(dns, allowlist) -> None:
    """A fixture server named by a hosts-file entry the checker can't see
    is still the endpoint the operator named."""
    allowlist("fixtures.test:6391")
    dns.set({})
    assert net_safety.is_safe_outbound_url("http://fixtures.test:6391/rss")
    assert not net_safety.is_safe_outbound_url("http://other.test:6391/rss")


def test_several_entries_are_read_and_whitespace_ignored(dns, allowlist) -> None:
    allowlist(" 127.0.0.1:6391 , [::1]:8080 ,fixtures.example.com ")
    assert net_safety.allow_entries() == (
        ("127.0.0.1", 6391),
        ("::1", 8080),
        ("fixtures.example.com", None),
    )
    assert net_safety.is_safe_outbound_url("http://[::1]:8080/x")
    assert not net_safety.is_safe_outbound_url("http://[::1]:8081/x")


def test_unusable_entries_are_dropped_rather_than_guessed_at(dns, allowlist) -> None:
    allowlist(
        "http://127.0.0.1:6391/feed, *.example.com, 127.0.0.1:0, "
        "127.0.0.1:70000, 127.0.0.1:six, 10.0.0.0/8, , 127.0.0.1:6391"
    )
    assert net_safety.allow_entries() == (("127.0.0.1", 6391),)
    assert net_safety.is_safe_outbound_url("http://127.0.0.1:6391/feed")
    dns.set({"anything.example.com": ["10.0.0.1"]})
    assert not net_safety.is_safe_outbound_url("http://anything.example.com/x")


def test_a_change_to_the_setting_takes_effect_without_a_restart(dns, allowlist) -> None:
    assert not net_safety.is_safe_outbound_url("http://127.0.0.1:6391/x")
    allowlist("127.0.0.1:6391")
    assert net_safety.is_safe_outbound_url("http://127.0.0.1:6391/x")
    allowlist("")
    assert not net_safety.is_safe_outbound_url("http://127.0.0.1:6391/x")


@pytest.mark.asyncio
async def test_the_async_form_honours_the_allowlist(dns, allowlist) -> None:
    allowlist("127.0.0.1:6391")
    assert await net_safety.acheck_outbound_url("http://127.0.0.1:6391/x") is None
    assert await net_safety.acheck_outbound_url("http://127.0.0.1:9999/x") is not None
    await net_safety.arequire_safe_outbound_url("http://127.0.0.1:6391/x")
    with pytest.raises(net_safety.UnsafeOutboundURL):
        await net_safety.arequire_safe_outbound_url("http://127.0.0.1:9999/x")


def test_the_allowlist_is_not_an_editable_setting(dns) -> None:
    """It is server configuration, not a parameter: the only way a setting
    becomes writable over HTTP is the FieldSpec registry, and the save
    route answers 'not an editable setting' for anything absent from it."""
    from domovoi.config_schema import FIELD_BY_NAME

    assert "outbound_allow_hosts" not in FIELD_BY_NAME


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
async def test_an_allowlisted_fixture_is_fetched_but_its_redirect_is_re_checked(
    dns, allowlist
) -> None:
    """Each hop is judged by the same rule, so allowlisting the fixture
    server does not let it bounce the fetch onto the core's admin API."""
    allowlist("127.0.0.1:6391")
    transport = _transport(
        {
            "http://127.0.0.1:6391/podcast/feed.xml": httpx.Response(
                200, content=b"<rss/>"
            ),
            "http://127.0.0.1:6391/bounce": httpx.Response(
                302, headers={"location": "http://127.0.0.1:6370/v1/admin/snapshot"}
            ),
        }
    )
    async with httpx.AsyncClient(transport=transport) as client:
        result = await net_safety.fetch_bytes(
            "http://127.0.0.1:6391/podcast/feed.xml", max_bytes=1024, client=client
        )
        assert result.content == b"<rss/>"
        with pytest.raises(net_safety.UnsafeOutboundURL):
            await net_safety.fetch_bytes(
                "http://127.0.0.1:6391/bounce", max_bytes=1024, client=client
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
