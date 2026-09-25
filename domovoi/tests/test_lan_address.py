"""URLs a satellite dials back to this server, derived from the LAN address.

``MPD_HTTP_BASE`` used to default to ``http://localhost``, which a Pi
resolves to itself, so a box that never edited it handed every satellite a
stream URL pointing nowhere. It (and the USB-adoption advertise URL, which
already autodetected when blank) now defaults to ``auto``: the host's LAN
IPv4, found with the UDP-connect trick the web backend already used, looked
up when the URL is built and re-looked-up after a short cache so a DHCP
change doesn't strand satellites on the old address. An explicit value is
used exactly as written — the live Beelink sets its own.

Pure socket/settings checks with the socket layer faked — no DB, no
``requires_db`` — so they can never skip.
"""

from __future__ import annotations

import logging
import socket
from pathlib import Path

import pytest

from domovoi import lan_address
from domovoi.clients import mpd as mpd_client
from domovoi.config import Settings, settings

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = REPO_ROOT / "domovoi" / ".env.example"


@pytest.fixture(autouse=True)
def _fresh_cache():
    lan_address.reset_cache()
    yield
    lan_address.reset_cache()


def _fake_network(
    monkeypatch,
    *,
    routed: str = "192.168.1.50",
    connect_error: OSError | None = None,
    by_hostname: str = "",
    hostname_error: OSError | None = None,
) -> dict:
    """Stand in for the two socket calls the probe makes."""
    seen: dict = {"connect": [], "closed": 0}

    class _UDP:
        def __init__(self, family, kind):
            assert (family, kind) == (socket.AF_INET, socket.SOCK_DGRAM)

        def connect(self, addr):
            seen["connect"].append(addr)
            if connect_error is not None:
                raise connect_error

        def getsockname(self):
            return (routed, 54321)

        def close(self):
            seen["closed"] += 1

    def _gethostbyname(name):
        if hostname_error is not None:
            raise hostname_error
        return by_hostname

    monkeypatch.setattr(lan_address.socket, "socket", _UDP)
    monkeypatch.setattr(lan_address.socket, "gethostbyname", _gethostbyname)
    return seen


def _probe_returns(monkeypatch, *values: str) -> list[str]:
    """Make the uncached probe answer ``values`` in turn (the last one
    repeats); returns the list of answers actually handed out."""
    handed: list[str] = []

    def _probe() -> str:
        value = values[min(len(handed), len(values) - 1)]
        handed.append(value)
        return value

    monkeypatch.setattr(lan_address, "probe_lan_ipv4", _probe)
    return handed


# ─── the probe (moved from the web backend, behaviour unchanged) ──────────


def test_probe_reads_the_source_address_of_the_default_route(monkeypatch):
    seen = _fake_network(monkeypatch, routed="192.168.1.50")
    assert lan_address.probe_lan_ipv4() == "192.168.1.50"
    assert seen["connect"] == [("192.0.2.1", 9)]           # TEST-NET, nothing sent
    assert seen["closed"] == 1


def test_probe_falls_back_to_the_host_name_without_a_route(monkeypatch):
    _fake_network(
        monkeypatch, connect_error=OSError("Network is unreachable"),
        by_hostname="192.168.1.60",
    )
    assert lan_address.probe_lan_ipv4() == "192.168.1.60"


def test_probe_falls_back_to_the_host_name_on_a_loopback_route(monkeypatch):
    _fake_network(monkeypatch, routed="127.0.0.1", by_hostname="10.0.0.5")
    assert lan_address.probe_lan_ipv4() == "10.0.0.5"


def test_probe_is_blank_when_nothing_answers(monkeypatch):
    _fake_network(
        monkeypatch, connect_error=OSError("unreachable"),
        hostname_error=OSError("no such host"),
    )
    assert lan_address.probe_lan_ipv4() == ""


# ─── the cached, never-loopback lookup ────────────────────────────────────


@pytest.mark.parametrize("raw", ["127.0.1.1", "127.0.0.1", "0.0.0.0", "", "not-an-ip"])
def test_lan_ipv4_never_hands_out_loopback_or_junk(monkeypatch, raw):
    """Debian/Ubuntu map the host name to 127.0.1.1, so the host-name
    fallback can come back loopback. A satellite given that dials itself."""
    _probe_returns(monkeypatch, raw)
    assert lan_address.lan_ipv4() is None


def test_lan_ipv4_is_cached_then_follows_a_dhcp_change(monkeypatch, caplog):
    now = [1000.0]
    monkeypatch.setattr(lan_address, "_clock", lambda: now[0])
    handed = _probe_returns(monkeypatch, "192.168.1.50", "192.168.1.77")

    with caplog.at_level(logging.INFO, logger=lan_address.log.name):
        assert lan_address.lan_ipv4() == "192.168.1.50"
        now[0] += lan_address.CACHE_TTL_SEC - 1
        assert lan_address.lan_ipv4() == "192.168.1.50"
        assert len(handed) == 1                            # served from the cache

        now[0] += 2                                        # past the TTL
        assert lan_address.lan_ipv4() == "192.168.1.77"    # the new lease
        assert len(handed) == 2

    assert "changed from 192.168.1.50 to 192.168.1.77" in caplog.text


def test_losing_the_address_is_logged_once_not_per_call(monkeypatch, caplog):
    now = [0.0]
    monkeypatch.setattr(lan_address, "_clock", lambda: now[0])
    _probe_returns(monkeypatch, "192.168.1.50", "")

    with caplog.at_level(logging.INFO, logger=lan_address.log.name):
        lan_address.lan_ipv4()
        for _ in range(3):
            now[0] += lan_address.CACHE_TTL_SEC + 1
            assert lan_address.lan_ipv4() is None

    lost = [r for r in caplog.records if "no LAN IPv4 address found" in r.getMessage()]
    assert len(lost) == 1
    assert "(was 192.168.1.50)" in lost[0].getMessage()


# ─── MPD_HTTP_BASE ────────────────────────────────────────────────────────


def test_mpd_http_base_defaults_to_auto():
    assert Settings.model_fields["mpd_http_base"].default == "auto"


@pytest.mark.parametrize("value", ["auto", "AUTO", " auto ", ""])
def test_auto_builds_the_prefix_from_the_lan_address(monkeypatch, value):
    monkeypatch.setattr(settings, "mpd_http_base", value)
    _probe_returns(monkeypatch, "192.168.1.50")
    assert lan_address.mpd_http_base() == "http://192.168.1.50"


@pytest.mark.parametrize(
    "explicit",
    ["http://beelink.lan", "http://my-domovoi.local", "http://192.168.1.9", "http://beelink.lan/"],
)
def test_an_explicit_prefix_is_used_as_written_without_a_lookup(monkeypatch, explicit):
    """The Beelink sets its own; that value must reach the Pi untouched."""
    monkeypatch.setattr(settings, "mpd_http_base", explicit)

    def _no_probe() -> str:
        raise AssertionError("an explicit MPD_HTTP_BASE must not trigger a lookup")

    monkeypatch.setattr(lan_address, "probe_lan_ipv4", _no_probe)
    assert lan_address.mpd_http_base() == explicit


def test_auto_falls_back_to_the_old_default_when_there_is_no_lan_address(monkeypatch):
    monkeypatch.setattr(settings, "mpd_http_base", "auto")
    _probe_returns(monkeypatch, "")
    assert lan_address.mpd_http_base() == "http://localhost"
    assert lan_address.MPD_HTTP_BASE_FALLBACK == "http://localhost"


@pytest.fixture
def den_room(monkeypatch):
    monkeypatch.setattr(mpd_client, "_room_ports", {"den": (6650, 8050)})


def test_stream_url_follows_the_lan_address(monkeypatch, den_room):
    monkeypatch.setattr(settings, "mpd_http_base", "auto")
    _probe_returns(monkeypatch, "192.168.1.50")
    assert mpd_client.mpd_stream_url_for("den") == "http://192.168.1.50:8050"


def test_stream_url_with_an_explicit_base_is_unchanged(monkeypatch, den_room):
    monkeypatch.setattr(settings, "mpd_http_base", "http://beelink.lan/")
    assert mpd_client.mpd_stream_url_for("den") == "http://beelink.lan:8050"


def test_stream_url_when_the_lookup_fails(monkeypatch, den_room):
    monkeypatch.setattr(settings, "mpd_http_base", "auto")
    _probe_returns(monkeypatch, "")
    assert mpd_client.mpd_stream_url_for("den") == "http://localhost:8050"


async def test_the_dashboard_shows_the_url_the_satellite_is_given(monkeypatch):
    """web's now-playing card builds the same URL the core hands the Pi."""
    from web.backend.api import music as music_api

    async def _idle(host, port):
        return "stop", None, None

    monkeypatch.setattr(music_api, "_read_mpd", _idle)
    monkeypatch.setattr(settings, "mpd_http_base", "auto")
    _probe_returns(monkeypatch, "192.168.1.50")

    np = await music_api._now_playing_for(("den", 6650, 8050))
    assert np.stream_url == "http://192.168.1.50:8050"


def test_plugins_read_a_usable_prefix_not_the_word_auto(monkeypatch):
    from domovoi.sdk.coreconfig import CoreConfigView

    monkeypatch.setattr(settings, "mpd_http_base", "auto")
    _probe_returns(monkeypatch, "192.168.1.50")
    assert CoreConfigView().mpd_http_base == "http://192.168.1.50"

    monkeypatch.setattr(settings, "mpd_http_base", "http://beelink.lan")
    assert CoreConfigView().mpd_http_base == "http://beelink.lan"


def test_env_example_ships_auto():
    """A fresh box copies .env.example, so the example is the effective
    default; it used to ship a placeholder hostname that resolved nowhere."""
    values = {}
    for raw in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    assert values.get("MPD_HTTP_BASE") == "auto"


# ─── the USB-adoption advertise URL (web) ─────────────────────────────────


def test_advertise_url_defaults_to_auto():
    assert Settings.model_fields["satellite_adoption_advertise_url"].default == "auto"


@pytest.mark.parametrize("value", ["auto", "Auto", "", "  "])
def test_advertise_auto_and_blank_derive_the_lan_address(monkeypatch, value):
    from web.backend import satellite_adoption as adoption

    monkeypatch.setattr(settings, "satellite_adoption_advertise_url", value)
    _fake_network(monkeypatch, routed="192.168.1.50")
    assert adoption.advertised_domovoi_url() == "ws://192.168.1.50:6370"


@pytest.mark.parametrize(
    "override,expected",
    [
        ("192.168.1.9", "ws://192.168.1.9:6370"),
        ("ws://beelink.lan", "ws://beelink.lan:6370"),
        ("wss://beelink.lan:7443", "wss://beelink.lan:7443"),
        ("http://beelink.lan:6370", "ws://beelink.lan:6370"),
    ],
)
def test_advertise_override_is_unchanged(monkeypatch, override, expected):
    from web.backend import satellite_adoption as adoption

    monkeypatch.setattr(settings, "satellite_adoption_advertise_url", override)
    assert adoption.advertised_domovoi_url() == expected


def test_advertise_fallbacks_are_what_web_always_did(monkeypatch):
    """Moving the probe into the domovoi package kept web's own last
    resorts: the host-name answer as-is, then 127.0.0.1."""
    from web.backend import satellite_adoption as adoption

    monkeypatch.setattr(settings, "satellite_adoption_advertise_url", "auto")
    _fake_network(monkeypatch, routed="127.0.0.1", by_hostname="127.0.1.1")
    assert adoption.advertised_domovoi_url() == "ws://127.0.1.1:6370"

    _fake_network(
        monkeypatch, connect_error=OSError("unreachable"),
        hostname_error=OSError("no such host"),
    )
    assert adoption.advertised_domovoi_url() == "ws://127.0.0.1:6370"


def test_advertise_url_is_looked_up_at_adopt_time(monkeypatch):
    from web.backend import satellite_adoption as adoption

    monkeypatch.setattr(settings, "satellite_adoption_advertise_url", "auto")
    _fake_network(monkeypatch, routed="192.168.1.50")
    assert adoption.advertised_domovoi_url() == "ws://192.168.1.50:6370"
    _fake_network(monkeypatch, routed="192.168.1.77")
    assert adoption.advertised_domovoi_url() == "ws://192.168.1.77:6370"
