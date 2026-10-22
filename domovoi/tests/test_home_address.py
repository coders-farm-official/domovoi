"""The household's own search address: plain http only on the home network.

Docs v1 audit (2026-10-21): the own SearXNG address took ``http://`` to any
host, so a public plain-http address would have carried every open-web
lookup's words in the clear, while the privacy pages said every internet
address uses https. Now (``domovoi/home_address.py``):

* ``https://`` anywhere;
* ``http://`` only to a home-network address as written: RFC 1918,
  link-local 169.254/16, loopback, 100.64/10, IPv6 unique-local fc00::/7,
  link-local fe80::/10 and ``::1``, ``localhost``, and names under .local,
  .home.arpa, .internal, .lan, .home, .localhost. A name is never
  resolved: a public name pointing at a home address still needs https;
* refused where the address is saved (the Settings write and the Test
  button, through ``coerce_and_validate``) and again where it is used (an
  address saved before the rule, or written into ``.env`` by hand), with
  "Use https:// unless the search is on your home network".

The SSRF guard (``net_safety``) is untouched: it still refuses what it
refused, by literal and by what a name resolves to. The phone's
``CleartextPolicy.kt`` applies the same list (its JVM test holds the phone
side; this file holds the two lists together).

DB-free and network-free: the auth dependency is overridden, ``.env``
lives in tmp, and nothing resolves a name.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from domovoi import admin_auth, config_env_writer, egress, home_address, lookup_web, net_safety
from domovoi.config import settings
from domovoi.config_schema import FIELD_BY_NAME, coerce_and_validate
from domovoi.home_address import HTTPS_UNLESS_HOME, own_search_address_problem, plain_http_permitted
from domovoi.main import app as core_app

REPO_ROOT = Path(__file__).resolve().parents[2]
CLEARTEXT_POLICY = REPO_ROOT / "android" / "app" / "src" / "main" / "java" / "com" / "domovoi" / "app" / "net" / "CleartextPolicy.kt"
SPEC = FIELD_BY_NAME["lookup_own_searxng_url"]

HOME_HTTP = [
    "http://192.168.1.20:8888",
    "http://10.0.0.5",
    "http://172.16.4.2:8080/",
    "http://172.31.255.254",
    "http://169.254.10.10:8888",
    "http://searx.local:8888",
    "http://nas.home.arpa",
    "http://box.lan",
    "http://box.internal",
    "http://[fd00::1]:8888",
    "http://[fd12:3456:789a::20]",
    "http://[fe80::1]:8888",
    "http://[fe80::1%25eth0]:8888",
    "http://localhost:8888",
    "http://127.0.0.1:8888",
    "http://[::1]:8888",
    "http://100.100.1.2:8888",

]
PUBLIC_HTTP = [
    "http://8.8.8.8",
    "http://8.8.8.8:8888",
    "http://203.0.113.5:8888",
    "http://search.example.com",
    "http://searx.be/search",
    "http://172.32.0.1",          # just past 172.16/12
    "http://192.169.0.1",
    "http://[2001:db8::1]",
    "http://[2606:4700::1111]",
    "http://[::ffff:192.168.1.20]",  # written as IPv6: judged as written, like the phone
    "http://3232235796",             # 192.168.1.20 as one number: not a home address as written
    "http://192.168.1",              # shorthand
    "http://local",                  # a single label is not on the list
    "http://evil.local.example.com",
    "http://localhost.example.com",
]
HTTPS_ANY = [
    "https://searx.be",
    "https://8.8.8.8",
    "https://search.example.com:8443/searxng",
    "https://192.168.1.20:8888",
    "https://searx.local",
]


@pytest.fixture(autouse=True)
def no_dns(monkeypatch):
    """Nothing here may resolve a name: the rule judges the address as
    written."""

    def refuse(*_a, **_k):
        raise AssertionError("the home-network rule resolved a name")

    monkeypatch.setattr(socket, "getaddrinfo", refuse)


# ─── The rule ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("url", HOME_HTTP)
def test_plain_http_is_allowed_on_the_home_network(url):
    assert own_search_address_problem(url) is None
    assert coerce_and_validate(SPEC, url) == url


@pytest.mark.parametrize("url", PUBLIC_HTTP)
def test_plain_http_anywhere_else_is_refused_in_plain_words(url):
    assert own_search_address_problem(url) == HTTPS_UNLESS_HOME
    with pytest.raises(ValueError) as e:
        coerce_and_validate(SPEC, url)
    assert str(e.value) == "Use https:// unless the search is on your home network"


@pytest.mark.parametrize("url", HTTPS_ANY)
def test_https_is_allowed_anywhere(url):
    assert own_search_address_problem(url) is None
    assert coerce_and_validate(SPEC, url) == url


def test_empty_clears_and_other_schemes_are_refused():
    assert coerce_and_validate(SPEC, "") == ""
    for bad in ("ftp://192.168.1.20", "192.168.1.20:8888", "searx.local", "http://", "javascript:alert(1)"):
        with pytest.raises(ValueError):
            coerce_and_validate(SPEC, bad)
        assert own_search_address_problem(bad) is not None


def test_a_name_is_judged_as_written_never_resolved(monkeypatch):
    """A public name whose DNS answer is a home address is still a public
    name: plain http to it is refused (the fixture fails any lookup)."""
    monkeypatch.setattr(net_safety, "resolve_host", lambda h: [ipaddress.ip_address("192.168.1.20")])
    assert own_search_address_problem("http://searx.example.net:8888") == HTTPS_UNLESS_HOME
    assert own_search_address_problem("https://searx.example.net:8888") is None


def test_the_hosts_the_rule_knows():
    for host in ("192.168.0.1", "10.255.255.255", "172.16.0.0", "169.254.169.254", "127.0.0.1",
                 "100.64.0.1", "100.127.255.255", "::1", "fc00::", "fdff::1", "fe80::", "febf::1",
                 "[fd00::1]", "localhost", "LOCALHOST.", "printer.local", "x.home.arpa", "x.localhost"):
        assert plain_http_permitted(host), host
    for host in ("", None, "8.8.8.8", "100.63.255.255", "100.128.0.0", "fec0::1", "ff02::1",
                 "2001:db8::1", "example.com", "nas", "0.0.0.0", "224.0.0.1"):
        assert not plain_http_permitted(host), host


def test_the_phone_applies_the_same_list():
    """android CleartextPolicy.kt is the phone's half of this rule (it also
    guards the server address); the two lists must not drift apart."""
    kt = CLEARTEXT_POLICY.read_text(encoding="utf-8")
    m = re.search(r"privateNameSuffixes: List<String> =\s*listOf\(([^)]*)\)", kt)
    assert m, "CleartextPolicy.privateNameSuffixes not found"
    assert tuple(re.findall(r'"([^"]+)"', m.group(1))) == home_address.PRIVATE_NAME_SUFFIXES
    for rule in ("o[0] == 10 ->", "o[0] == 172 && o[1] in 16..31", "o[0] == 192 && o[1] == 168",
                 "o[0] == 127 ->", "o[0] == 169 && o[1] == 254", "o[0] == 100 && o[1] in 64..127",
                 "0xfe00) == 0xfc00", "0xffc0) == 0xfe80", 'h == "::1"'):
        assert rule in kt, rule


# ─── Where it is used: an address saved before the rule ───────────────────


def test_a_saved_public_http_address_is_never_used(monkeypatch):
    sent: list[str] = []

    async def fake_searxng(base, query, **_k):
        sent.append(base)
        return "ok", []

    monkeypatch.setattr(lookup_web, "searxng_search", fake_searxng)
    monkeypatch.setattr(settings, "lookup_own_search", "searxng")
    monkeypatch.setattr(settings, "lookup_own_searxng_url", "http://203.0.113.5:8888")
    with pytest.raises(lookup_web.OwnSearchFailed) as e:
        asyncio.run(lookup_web.OpenWebSource().search("red panda"))
    assert "Use https:// unless the search is on your home network" in str(e.value)
    got = asyncio.run(lookup_web.test_own_search("searxng", url="http://203.0.113.5:8888"))
    assert got == {"result": "refused", "detail": "Use https:// unless the search is on your home network."}
    assert sent == []
    # A home address goes out as before.
    monkeypatch.setattr(settings, "lookup_own_searxng_url", "http://192.168.1.20:8888")
    asyncio.run(lookup_web.OpenWebSource().search("red panda"))
    assert sent == ["http://192.168.1.20:8888"]


# ─── The routes: the Test button and the Settings write ───────────────────


def _core() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=core_app, raise_app_exceptions=False), base_url="http://test")


@pytest.fixture
def admin(tmp_path, monkeypatch):
    import domovoi.db.repositories as repos
    import domovoi.main as main_mod

    env = tmp_path / ".env"
    monkeypatch.setattr(config_env_writer, "_ENV_FILE", env)
    monkeypatch.delenv("LOOKUP_OWN_SEARXNG_URL", raising=False)
    monkeypatch.setattr(settings, "lookup_own_searxng_url", "")
    core_app.state.config_apply_lock = asyncio.Lock()

    @asynccontextmanager
    async def fake_scope():
        yield object()

    class FakeLog:
        def __init__(self, _s):
            pass

        async def log(self, **_kw):
            return None

    monkeypatch.setattr(main_mod, "session_scope", fake_scope)
    monkeypatch.setattr(repos, "IntentLogRepository", FakeLog)
    core_app.dependency_overrides[admin_auth.require_admin_security] = lambda: None
    try:
        yield env
    finally:
        core_app.dependency_overrides.pop(admin_auth.require_admin_security, None)


@pytest.mark.asyncio
async def test_the_test_button_refuses_a_public_http_address(admin, monkeypatch):
    sent: list[str] = []

    async def fake_searxng(base, query, **_k):
        sent.append(base)
        return "ok", [{"title": "t", "url": "https://x.example/", "content": "", "engine": "x"}]

    monkeypatch.setattr(lookup_web, "searxng_search", fake_searxng)
    async with _core() as c:
        with egress.override_policy("always"):
            bad = await c.post("/v1/admin/config/search-test", json={"kind": "searxng", "url": "http://8.8.8.8:8888"})
            good = await c.post("/v1/admin/config/search-test", json={"kind": "searxng", "url": "http://searx.local:8888"})
    assert bad.status_code == 422 and "Use https:// unless the search is on your home network" in bad.text
    assert good.status_code == 200 and good.json()["result"] == "works"
    assert sent == ["http://searx.local:8888"]


@pytest.mark.asyncio
async def test_the_settings_write_refuses_a_public_http_address(admin):
    async with _core() as c:
        with egress.override_policy("always"):
            bad = await c.post("/v1/admin/config", json={"changes": {"lookup_own_searxng_url": "http://searx.example.com"}})
            assert settings.lookup_own_searxng_url == ""
            good = await c.post("/v1/admin/config", json={"changes": {"lookup_own_searxng_url": "http://[fd00::20]:8888"}})
    assert "Use https:// unless the search is on your home network" in bad.text
    assert "searx.example.com" not in (admin.read_text(encoding="utf-8") if admin.exists() else "")
    assert good.status_code == 200, good.text
    assert settings.lookup_own_searxng_url == "http://[fd00::20]:8888"


# ─── The SSRF guard still refuses what it refused ─────────────────────────


@pytest.fixture
def dns(monkeypatch):
    table: dict[str, list[str]] = {
        "feeds.example.com": ["93.184.216.34"],
        "rebind.example.com": ["192.168.1.20"],
        "meta.example.com": ["169.254.169.254"],
        "mixed.example.com": ["93.184.216.34", "10.0.0.5"],
        "ula.example.com": ["fd00::1"],
    }
    monkeypatch.setattr(net_safety, "resolve_host",
                        lambda host: [ipaddress.ip_address(a) for a in table.get(host.lower(), [])])


@pytest.mark.parametrize("url", [
    "http://192.168.1.20:8888/search", "https://192.168.1.20/", "http://10.0.0.5", "http://127.0.0.1:6370/",
    "http://[::1]/", "http://169.254.169.254/latest/meta-data/", "http://[fd00::1]/", "http://[fe80::1]/",
    "http://localhost:6369/", "http://foo.localhost/", "http://2130706433/", "http://0x7f.1/",
    "http://rebind.example.com/", "https://meta.example.com/", "http://mixed.example.com/",
    "http://ula.example.com/", "http://nowhere.example.com/", "file:///etc/passwd", "gopher://feeds.example.com/",
])
def test_the_ssrf_guard_still_refuses(dns, url):
    with egress.override_policy("always"):
        with pytest.raises(net_safety.UnsafeOutboundURL):
            net_safety.vet_outbound_url(url)


def test_the_ssrf_guard_still_allows_a_public_name(dns):
    with egress.override_policy("always"):
        target = net_safety.vet_outbound_url("https://feeds.example.com/rss")
    assert [str(a) for a in target.addresses] == ["93.184.216.34"]
