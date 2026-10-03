"""``domovoi.egress`` — the one egress choke point (INTERNET_ACCESS).

DB-free and DNS-free. Covers the answer itself (normalisation, where it is
read from, the cache, the test override), what counts as "on this
network", the URL / no-URL gates, the httpx hooks, the HTTP refusal shape,
``net_safety`` under ``never``, the SDK 1.4 / webkit surface, and the
autouse isolation that keeps a machine's own answer out of the suite.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from domovoi import egress, net_safety

REPO_ROOT = Path(__file__).resolve().parents[2]


# ─── The value ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("always", "always"), ("YES", "always"), (" true ", "always"), ("on", "always"),
        ("online", "always"), ("Enabled", "always"), (True, "always"),
        ("sometimes", "sometimes"), ("limited", "sometimes"), ("METERED", "sometimes"),
        ("intermittent", "sometimes"),
        ("never", "never"), ("no", "never"), ("false", "never"), ("off", "never"),
        ("offline", "never"), ("none", "never"), ("disabled", "never"), ("dark", "never"),
        (False, "never"),
        ("", ""), ("  ", ""), ("unset", ""), ("ASK", ""), (None, ""),
    ],
)
def test_normalize_policy_aliases(raw, expected) -> None:
    assert egress.normalize_policy(raw) == expected


def test_an_unknown_value_is_unanswered_with_exactly_one_warning(caplog) -> None:
    egress._warned_unknown.discard("perhaps-tuesdays")
    with caplog.at_level(logging.WARNING, logger="domovoi.egress"):
        assert egress.normalize_policy("perhaps-tuesdays") == ""
        assert egress.normalize_policy("Perhaps-Tuesdays") == ""
        assert egress.normalize_policy("perhaps-tuesdays") == ""
    warnings = [r for r in caplog.records if "perhaps-tuesdays" in r.getMessage().lower()]
    assert len(warnings) == 1
    assert "not answered" in warnings[0].getMessage()


def test_read_policy_environment_beats_the_env_file(tmp_path) -> None:
    env = tmp_path / ".env"
    env.write_text("INTERNET_ACCESS=never\n", encoding="utf-8")
    assert egress.read_policy(env_path=env, environ={}) == "never"
    assert egress.read_policy(env_path=env, environ={"INTERNET_ACCESS": "always"}) == "always"
    # any case, as pydantic-settings matches it
    assert egress.read_policy(env_path=env, environ={"internet_access": "sometimes"}) == "sometimes"
    # an empty export still shadows .env (pydantic-settings reads it as set)
    assert egress.read_policy(env_path=env, environ={"INTERNET_ACCESS": ""}) == ""


def test_read_policy_ignores_commented_lines_and_missing_files(tmp_path) -> None:
    env = tmp_path / ".env"
    env.write_text("# INTERNET_ACCESS=never\nBOT_NAME=x\n", encoding="utf-8")
    assert egress.read_policy(env_path=env, environ={}) == ""
    assert egress.read_policy(env_path=tmp_path / "absent.env", environ={}) == ""
    env.write_text("internet_access = Sometimes\n", encoding="utf-8")
    assert egress.read_policy(env_path=env, environ={}) == "sometimes"


@pytest.fixture
def live_policy(tmp_path, monkeypatch):
    """policy() reading a tmp .env with no override and no env export —
    the conftest pin lifted for this test only."""
    from domovoi import config_env_writer

    env = tmp_path / ".env"
    monkeypatch.setattr(config_env_writer, "_ENV_FILE", env)
    monkeypatch.delenv("INTERNET_ACCESS", raising=False)
    monkeypatch.setattr(egress, "_overrides", [])
    monkeypatch.setattr(egress, "_cache", None)
    return env


def test_policy_follows_a_rewritten_env_file(live_policy) -> None:
    env = live_policy
    assert egress.policy() == ""
    env.write_text("INTERNET_ACCESS=always\n", encoding="utf-8")
    assert egress.policy() == "always"
    env.write_text("INTERNET_ACCESS=never\n", encoding="utf-8")
    os.utime(env, ns=(1_000_000_000, 1_000_000_000))  # a different stamp, whatever the clock
    assert egress.policy() == "never"
    assert egress.internet_turned_off() and not egress.internet_allowed()


def test_policy_reads_the_environment_first(live_policy, monkeypatch) -> None:
    live_policy.write_text("INTERNET_ACCESS=never\n", encoding="utf-8")
    monkeypatch.setenv("INTERNET_ACCESS", "sometimes")
    assert egress.policy() == "sometimes"
    monkeypatch.delenv("INTERNET_ACCESS")
    assert egress.policy() == "never"


def test_override_policy_nests_and_the_innermost_wins(live_policy) -> None:
    live_policy.write_text("INTERNET_ACCESS=always\n", encoding="utf-8")
    with egress.override_policy("never"):
        assert egress.policy() == "never"
        with egress.override_policy("sometimes"):
            assert egress.policy() == "sometimes"
            with egress.override_policy(None):          # no override at this level
                assert egress.policy() == "sometimes"
        with egress.override_policy("off"):             # normalised
            assert egress.policy() == "never"
        assert egress.policy() == "never"
    assert egress.policy() == "always"


def test_the_autouse_fixture_keeps_the_machine_answer_out(monkeypatch) -> None:
    """D21: the conftest pin holds even with INTERNET_ACCESS exported."""
    monkeypatch.setenv("INTERNET_ACCESS", "never")
    assert egress.policy() == ""
    assert egress.internet_allowed()


# ─── On this network? ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "host",
    ["127.1", "10.0.0.5", "192.168.0.117", "::1", "[::1]", "fe80::1", "localhost",
     "nas", "media.local", "host.docker.internal", "router.lan", "NAS.", "printer.home.arpa",
     "0x7f000001", "100.64.0.9", "fd00::5", "box.localdomain"],
)
def test_is_local_host_true(host) -> None:
    assert egress.is_local_host(host)


@pytest.mark.parametrize(
    "host", ["example.com", "1.1.1.1", "8.8.8.8", "2606:4700::1111", "huggingface.co", "", "lan.example.com"],
)
def test_is_local_host_false(host) -> None:
    assert not egress.is_local_host(host)


def test_is_local_host_never_resolves(monkeypatch) -> None:
    def boom(*a, **k):  # pragma: no cover — the assertion is that it isn't called
        raise AssertionError("DNS lookup")

    monkeypatch.setattr("socket.getaddrinfo", boom)
    monkeypatch.setattr(net_safety, "resolve_host", boom)
    assert not egress.is_local_host("example.com")
    assert egress.is_local_host("nas")


def test_local_suffixes_cover_the_transport_guard_list() -> None:
    from domovoi import transport_guard

    assert set(transport_guard.LOCAL_SUFFIXES) <= set(egress.LOCAL_NAME_SUFFIXES)


# ─── The gates ────────────────────────────────────────────────────────────


def test_check_destination_under_never_and_always() -> None:
    with egress.override_policy("never"):
        assert egress.check_destination("https://example.com/feed") == egress.TURNED_OFF_REASON
        assert egress.check_destination("http://192.168.0.117:6370/v1/health") is None
        assert egress.check_destination("http://localhost:11434/api/tags") is None
        assert egress.check_destination("http://[::1]:8080/") is None
        assert egress.check_destination("not a url") is None       # no host: not ours to judge
    for answer in ("always", "sometimes", ""):
        with egress.override_policy(answer):
            assert egress.check_destination("https://example.com/feed") is None


def test_require_destination_and_require_internet_raise_the_subclass() -> None:
    with egress.override_policy("never"):
        with pytest.raises(egress.InternetTurnedOff) as e:
            egress.require_destination("https://example.com/x")
        assert isinstance(e.value, net_safety.UnsafeOutboundURL)
        assert isinstance(e.value, ValueError)
        assert e.value.reason == egress.TURNED_OFF_REASON
        egress.require_destination("http://10.0.0.5/x")              # local: fine
        with pytest.raises(net_safety.UnsafeOutboundURL) as e2:
            egress.require_internet("web search")
        assert e2.value.what == "web search"
        assert egress.TURNED_OFF_REASON in str(e2.value)
    with egress.override_policy("always"):
        egress.require_internet("web search")
        egress.require_destination("https://example.com/x")


def test_the_first_refusal_per_what_logs_at_info(caplog) -> None:
    egress._refused_whats.discard("git fetch")
    with egress.override_policy("never"), caplog.at_level(logging.DEBUG, logger="domovoi.egress"):
        for _ in range(3):
            with pytest.raises(egress.InternetTurnedOff):
                egress.require_internet("git fetch")
    lines = [r for r in caplog.records if "refused git fetch" in r.getMessage()]
    assert [r.levelno for r in lines] == [logging.INFO, logging.DEBUG, logging.DEBUG]


def _transport(seen: list[str]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, text="ok")

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_async_client_refuses_non_local_under_never_and_keeps_caller_hooks() -> None:
    seen: list[str] = []
    caller_saw: list[str] = []

    async def caller_hook(request):
        caller_saw.append(str(request.url))

    with egress.override_policy("never"):
        async with egress.async_client(
            transport=_transport(seen), event_hooks={"request": [caller_hook]}
        ) as client:
            with pytest.raises(egress.InternetTurnedOff):
                await client.get("https://example.com/feed.xml")
            r = await client.get("http://192.168.0.2:8000/feed.xml")
            assert r.status_code == 200
        assert client.event_hooks["request"][0] is egress.async_request_hook
    assert seen == ["http://192.168.0.2:8000/feed.xml"]
    assert caller_saw == ["http://192.168.0.2:8000/feed.xml"]


@pytest.mark.asyncio
async def test_async_client_refuses_a_redirect_hop_to_the_internet() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "192.168.0.2":
            return httpx.Response(302, headers={"location": "https://example.com/elsewhere"})
        return httpx.Response(200)

    with egress.override_policy("never"):
        async with egress.async_client(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(egress.InternetTurnedOff):
                await client.get("http://192.168.0.2/start", follow_redirects=True)


def test_sync_client_hook() -> None:
    seen: list[str] = []
    with egress.override_policy("never"):
        with egress.sync_client(transport=_transport(seen)) as client:
            with pytest.raises(egress.InternetTurnedOff):
                client.get("https://example.com/")
            assert client.get("http://nas/").status_code == 200
    with egress.override_policy("always"):
        with egress.sync_client(transport=_transport(seen)) as client:
            assert client.get("https://example.com/").status_code == 200
    assert seen == ["http://nas/", "https://example.com/"]


def test_http_exception_shape() -> None:
    exc = egress.http_exception("web search")
    assert exc.status_code == 409
    assert exc.detail == egress.TURNED_OFF_REASON
    assert exc.headers == {"X-Domovoi-Refusal": "internet-off"}


def test_spoken_offline_phrase() -> None:
    with egress.override_policy("never"):
        assert egress.spoken_offline_phrase() == "I'm set to stay off the internet"
    for answer in ("", "always", "sometimes"):
        with egress.override_policy(answer):
            assert egress.spoken_offline_phrase() == "I don't have internet right now"


def test_apply_process_env(monkeypatch) -> None:
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    with egress.override_policy("always"):
        assert egress.apply_process_env() is False
        assert "HF_HUB_OFFLINE" not in os.environ
    with egress.override_policy("never"):
        assert egress.apply_process_env() is True
        assert os.environ["HF_HUB_OFFLINE"] == "1"
        monkeypatch.setenv("HF_HUB_OFFLINE", "")               # empty = unset
        assert egress.apply_process_env() is True
        assert os.environ["HF_HUB_OFFLINE"] == "1"
        monkeypatch.setenv("HF_HUB_OFFLINE", "0")              # set by hand: wins
        assert egress.apply_process_env() is False
        assert os.environ["HF_HUB_OFFLINE"] == "0"


def test_cli_print_policy_and_check(capsys) -> None:
    with egress.override_policy("sometimes"):
        assert egress.main(["--print-policy"]) == 0
    assert capsys.readouterr().out == "sometimes\n"
    with egress.override_policy(""):
        assert egress.main(["--print-policy"]) == 0
    assert capsys.readouterr().out == "\n"
    with egress.override_policy("never"):
        assert egress.main(["--check", "https://example.com/"]) == 1
        assert capsys.readouterr().out.strip() == egress.TURNED_OFF_REASON
        assert egress.main(["--check", "http://10.1.1.1/"]) == 0
        assert capsys.readouterr().out.strip() == "allowed"


def test_cli_as_a_module_reads_the_environment() -> None:
    env = {**os.environ, "INTERNET_ACCESS": "Offline", "PYTHONIOENCODING": "utf-8"}
    out = subprocess.run(
        [sys.executable, "-m", "domovoi.egress", "--print-policy"],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout == "never\n"


def test_the_module_does_not_import_config_at_import_time() -> None:
    src = (REPO_ROOT / "domovoi" / "egress.py").read_text(encoding="utf-8")
    assert not re.search(r"^(from domovoi import .*\bconfig\b(?!_)|import domovoi\.config\b|from domovoi\.config )", src, re.M)
    assert not re.search(r"^import (httpx|fastapi|dotenv)|^from (httpx|fastapi|dotenv)", src, re.M)


def test_the_dashboard_message_is_the_server_reason() -> None:
    src = (REPO_ROOT / "web" / "static" / "components.jsx").read_text(encoding="utf-8")
    m = re.search(r"const INTERNET_OFF_MESSAGE = '([^']*)';", src)
    assert m and m.group(1) == egress.TURNED_OFF_REASON


# ─── net_safety under never ───────────────────────────────────────────────


@pytest.fixture
def no_dns(monkeypatch):
    calls: list[str] = []

    def resolve(host: str):
        calls.append(host)
        raise AssertionError(f"resolved {host!r} under never")

    monkeypatch.setattr(net_safety, "resolve_host", resolve)
    return calls


def test_a_fetch_check_is_refused_without_dns(no_dns) -> None:
    with egress.override_policy("never"):
        assert net_safety.check_outbound_url("https://feeds.example.com/rss") == egress.TURNED_OFF_REASON
        assert net_safety.check_outbound_url("https://1.1.1.1/") == egress.TURNED_OFF_REASON
    assert no_dns == []


def test_a_store_only_check_does_no_dns_and_allows_a_public_name(no_dns) -> None:
    with egress.override_policy("never"):
        assert net_safety.check_outbound_url("https://feeds.example.com/rss", require_resolution=False) is None
        # the existing literal refusals still hold
        assert net_safety.check_outbound_url("file:///etc/passwd", require_resolution=False) is not None
        assert net_safety.check_outbound_url("http://localhost/x", require_resolution=False) == "host is localhost"
        assert "non-public" in net_safety.check_outbound_url("http://10.0.0.1/x", require_resolution=False)
    assert no_dns == []


def test_allowlisted_loopback_survives_never_and_a_public_name_does_not(monkeypatch, no_dns) -> None:
    monkeypatch.setattr(
        net_safety, "configured_allow_hosts", lambda: "127.0.0.1:6391,fixtures.example.com"
    )
    with egress.override_policy("never"):
        assert net_safety.check_outbound_url("http://127.0.0.1:6391/feed.xml") is None
        assert net_safety.check_outbound_url("https://fixtures.example.com/feed.xml") == egress.TURNED_OFF_REASON
    with egress.override_policy("always"):
        assert net_safety.check_outbound_url("https://fixtures.example.com/feed.xml") is None
    assert no_dns == []


@pytest.mark.asyncio
async def test_require_safe_outbound_url_raises_internet_turned_off(no_dns) -> None:
    with egress.override_policy("never"):
        with pytest.raises(egress.InternetTurnedOff):
            net_safety.require_safe_outbound_url("https://feeds.example.com/rss")
        with pytest.raises(egress.InternetTurnedOff):
            await net_safety.arequire_safe_outbound_url("https://feeds.example.com/rss")
        with pytest.raises(egress.InternetTurnedOff):
            await net_safety.fetch_bytes("https://feeds.example.com/rss", max_bytes=10)
    # an unsafe (not turned-off) refusal stays the plain class
    with pytest.raises(net_safety.UnsafeOutboundURL) as e:
        net_safety.require_safe_outbound_url("http://localhost/x")
    assert not isinstance(e.value, egress.InternetTurnedOff)


def test_unanswered_net_safety_is_unchanged(monkeypatch) -> None:
    import ipaddress

    monkeypatch.setattr(
        net_safety, "resolve_host", lambda h: [ipaddress.ip_address("93.184.216.34")]
    )
    assert net_safety.check_outbound_url("https://feeds.example.com/rss") is None


# ─── SDK 1.4 and webkit ───────────────────────────────────────────────────


def test_sdk_and_webkit_reexport_egress() -> None:
    from domovoi import sdk, webkit

    assert sdk.API_VERSION == "1.4.0"
    assert sdk.egress is egress
    assert webkit.egress is egress
    assert "egress" in sdk.__all__ and "egress" in webkit.__all__


def test_connectivity_view_policy_reason_and_allowed(monkeypatch) -> None:
    from domovoi import connectivity
    from domovoi.sdk.facade import ConnectivityView

    class Probe:
        online = True
        reason = "connected"

    view = ConnectivityView()
    monkeypatch.setattr(connectivity, "_current_probe", None)
    assert view.online and view.policy == "" and view.reason == "connected" and view.internet_allowed

    probe = Probe()
    monkeypatch.setattr(connectivity, "_current_probe", probe)
    with egress.override_policy("sometimes"):
        assert view.policy == "sometimes" and view.online and view.reason == "connected"
        probe.online, probe.reason = False, "offline"
        assert not view.online and view.reason == "offline" and view.internet_allowed
    with egress.override_policy("never"):
        probe.online, probe.reason = True, "connected"     # a stale probe can't open the gate
        assert view.policy == "never"
        assert not view.online
        assert view.reason == "turned_off"
        assert not view.internet_allowed


@pytest.mark.asyncio
async def test_sdk_http_factory_refuses_under_never() -> None:
    from domovoi.sdk.http import HttpFactory

    seen: list[str] = []
    client = HttpFactory("9.9.9").client(transport=_transport(seen))
    async with client:
        assert client.headers["User-Agent"].startswith("domovoi/9.9.9")
        with egress.override_policy("never"):
            with pytest.raises(egress.InternetTurnedOff):
                await client.get("https://musicbrainz.org/ws/2/artist")
            assert (await client.get("http://127.0.0.1:6370/v1/health")).status_code == 200
        with egress.override_policy("always"):
            assert (await client.get("https://musicbrainz.org/ws/2/artist")).status_code == 200
    assert seen == ["http://127.0.0.1:6370/v1/health", "https://musicbrainz.org/ws/2/artist"]


@pytest.mark.asyncio
async def test_web_plugin_http_refuses_under_never() -> None:
    from web.backend.plugin_host import WebPluginContext

    seen: list[str] = []
    ctx = WebPluginContext("egresstest")
    async with ctx.http(transport=_transport(seen)) as client:
        with egress.override_policy("never"):
            with pytest.raises(egress.InternetTurnedOff):
                await client.get("https://de1.api.radio-browser.info/json/stations")
            assert (await client.get("http://satellite.local/x")).status_code == 200
    assert seen == ["http://satellite.local/x"]


def test_the_web_process_preloads_egress() -> None:
    from web.backend import plugin_host

    assert "domovoi.egress" in plugin_host._WEB_BACKEND_CORE_IMPORTS
    assert "domovoi.config_env_writer" in plugin_host._WEB_BACKEND_CORE_IMPORTS
