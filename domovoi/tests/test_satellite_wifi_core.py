"""The core's side of the 2026-10-12 satellite Wi-Fi fixes.

* The WebSocket ping timeout is 20 s, not 5: one pong a few seconds late
  on busy Wi-Fi dropped a live satellite, which then reconnected.
* `wifi_status` carries the received signal (dBm) and whether the radio is
  hosting the setup hotspot; the core keeps both and the web roster passes
  them on. An older satellite sends neither and reads as before.
* A health sample may say why the satellite needs a reboot.
* `ready` names this host on the LAN (`<hostname>.local`), the first thing
  a satellite tries when it has to find its core again.
* "How's your wifi?" answers by signal when the satellite reports one.

No DB.
"""

from __future__ import annotations

import pytest

from domovoi import satellite_health, streaming
from domovoi.config import Settings
from domovoi.handlers.wifi import _DIAGNOSTIC_RE, WifiHandler, signal_quality
from domovoi.models import Context
from domovoi.streaming import StreamSession
from domovoi.tests.test_satellite_supports import FakeWS


def test_the_ping_timeout_tolerates_a_late_pong():
    s = Settings()
    assert s.ws_ping_timeout_sec == 20.0
    assert s.ws_ping_interval_sec == 10.0
    # A dead socket is still reclaimed well inside a minute.
    assert s.ws_ping_interval_sec + s.ws_ping_timeout_sec <= 30.0


@pytest.mark.parametrize("hostname, names", [
    ("domovoi", ["domovoi.local"]),
    ("Beelink", ["beelink.local"]),
    ("beelink.lan", ["beelink.local"]),
    ("localhost", []),
    ("", []),
    ("bad_name", []),
])
def test_ready_names_this_host(monkeypatch, hostname, names):
    monkeypatch.setattr(streaming.socket, "gethostname", lambda: hostname)
    assert streaming.advertised_server_names() == names


async def test_wifi_status_keeps_the_signal_and_the_mode():
    ws = FakeWS()
    await StreamSession(ws, "kitchen")._on_control({  # type: ignore[arg-type]
        "type": "wifi_status", "rx_mbits": 1.0, "tx_mbits": 72.2, "ssid": "home",
        "signal_dbm": -67, "mode": "managed",
    })
    assert ws.app.state.wifi_status["kitchen"] == {
        "rx_mbits": 1.0, "tx_mbits": 72.2, "ssid": "home", "signal_dbm": -67, "mode": "managed",
    }


@pytest.mark.parametrize("dbm", [12, -500, "strong", True, float("nan")])
async def test_an_impossible_signal_is_dropped(dbm):
    ws = FakeWS()
    await StreamSession(ws, "kitchen")._on_control({  # type: ignore[arg-type]
        "type": "wifi_status", "rx_mbits": 5.0, "signal_dbm": dbm, "mode": "AP",
    })
    cached = ws.app.state.wifi_status["kitchen"]
    assert cached["signal_dbm"] is None
    assert cached["mode"] == "ap"


async def test_an_older_satellite_reads_as_before():
    ws = FakeWS()
    await StreamSession(ws, "kitchen")._on_control({  # type: ignore[arg-type]
        "type": "wifi_status", "rx_mbits": 39.0, "tx_mbits": 72.0, "ssid": "home",
    })
    cached = ws.app.state.wifi_status["kitchen"]
    assert cached["signal_dbm"] is None and cached["mode"] is None
    assert cached["rx_mbits"] == 39.0


def test_a_health_sample_keeps_signal_mode_and_reboot_needed():
    s = satellite_health.validate_sample({
        "seq": 1, "ts": 1.0,
        "wifi": {"rx_mbits": 1.0, "tx_mbits": 72.2, "ssid": "home", "signal_dbm": -67, "mode": "ap"},
        "reboot_needed": "network settings are applied at boot: reboot this satellite once",
    })
    assert s["wifi"]["signal_dbm"] == -67 and s["wifi"]["mode"] == "ap"
    assert s["reboot_needed"].startswith("network settings")
    bogus = satellite_health.validate_sample({
        "seq": 1, "wifi": {"rx_mbits": 1.0, "signal_dbm": 40}, "reboot_needed": 7,
    })
    assert bogus["wifi"]["signal_dbm"] is None
    assert bogus["reboot_needed"] is None
    # An older satellite's wifi block is exactly what it always was.
    old = satellite_health.validate_sample({"seq": 1, "wifi": {"rx_mbits": 1.0}})
    assert old["wifi"] == {"rx_mbits": 1.0, "tx_mbits": None, "ssid": None}


def test_a_health_sample_keeps_the_boot_step_and_the_host_name():
    """What root's boot-time network step did this boot, and the unit's
    host name (2026-10-12 follow-up: the rename had to be visible)."""
    s = satellite_health.validate_sample({
        "seq": 1, "ts": 1.0, "hostname": "domovoi-office",
        "net_step": {"state": "skipped", "when": "boot", "at": 1760000000.5, "version": 2,
                     "detail": "host name 'kamrons-pi' looks chosen by a person; not renaming it"},
    })
    assert s["hostname"] == "domovoi-office"
    assert s["net_step"] == {"state": "skipped", "when": "boot", "at": 1760000000.5, "version": 2,
                             "detail": "host name 'kamrons-pi' looks chosen by a person; not renaming it"}
    for state in ("applied", "unchanged", "failed", "not_run", "not_installed", "running"):
        assert satellite_health.validate_sample(
            {"seq": 1, "net_step": {"state": state}})["net_step"]["state"] == state


@pytest.mark.parametrize("net_step, hostname", [
    ({"state": "pwned", "detail": "x"}, "bad name with spaces"),
    ("applied", "-leading-dash"),
    ({"detail": "no state"}, 42),
    (["applied"], "a" * 80),
])
def test_a_bogus_boot_step_or_host_name_is_not_kept(net_step, hostname):
    s = satellite_health.validate_sample({"seq": 1, "load1": 0.5, "net_step": net_step, "hostname": hostname})
    assert s["net_step"] is None and s["hostname"] is None


def test_the_boot_step_detail_is_bounded():
    s = satellite_health.validate_sample({"seq": 1, "net_step": {"state": "applied", "detail": "x" * 1000}})
    assert len(s["net_step"]["detail"]) == 240


def test_an_older_satellite_sends_no_boot_step():
    s = satellite_health.validate_sample({"seq": 1, "load1": 0.5, "reboot_needed": None})
    assert "net_step" not in s and "hostname" not in s


def test_the_journal_line_names_the_signal():
    line = satellite_health.format_health_line("kitchen", {"wifi": {"rx_mbits": 1.0, "signal_dbm": -67}})
    assert "signal=-67dBm" in line


@pytest.mark.parametrize("dbm, word", [(-40, "good"), (-60, "good"), (-61, "fair"),
                                       (-70, "fair"), (-71, "weak")])
def test_the_signal_bands(dbm, word):
    assert signal_quality(dbm) == word


async def test_how_is_the_wifi_answers_by_signal():
    m = _DIAGNOSTIC_RE.match("how's your wifi")
    ctx = Context(room_id="kitchen", online=True,
                  wifi_status={"rx_mbits": 1.0, "tx_mbits": 72.2, "ssid": "home", "signal_dbm": -52})
    r = await WifiHandler()._status_from_match(m, ctx, None)
    assert r.text == "WiFi is connected to home with a good signal."
    ctx = Context(room_id="kitchen", online=True,
                  wifi_status={"rx_mbits": 54.0, "ssid": "home", "signal_dbm": -78})
    r = await WifiHandler()._status_from_match(m, ctx, None)
    assert "weak signal" in r.text and "closer" in r.text


@pytest.mark.parametrize("dbm, shown", [
    (-67, -67.0), (0, 0.0), (-120, -120.0),
    (1, None), (-121, None), (12, None), (True, None), ("strong", None), (None, None),
    (float("nan"), None),
])
async def test_the_web_roster_passes_only_a_real_signal(monkeypatch, dbm, shown):
    """The dashboard colours by this number, so the web hop holds it to the
    range a radio can report, whatever the snapshot carried."""
    from web.backend.api import satellites as web_sats

    row = {"room_id": "kitchen", "control_port": None, "http_port": None, "last_connected_at": None}
    snapshot = {"active_rooms": ["kitchen"], "wifi_status": {"kitchen": {
        "rx_mbits": 1.0, "tx_mbits": 72.2, "ssid": "home", "signal_dbm": dbm, "mode": "managed"}}}
    sat = await web_sats._satellite_for(row, snapshot, {}, {}, {})
    assert sat.wifi.signal_dbm == shown
    assert sat.wifi.mode == "managed"
    assert sat.wifi.rx_mbits == 1.0


async def test_the_web_roster_cuts_a_long_mode():
    from web.backend.api import satellites as web_sats

    row = {"room_id": "kitchen", "control_port": None, "http_port": None, "last_connected_at": None}
    snapshot = {"wifi_status": {"kitchen": {"rx_mbits": None, "mode": "x" * 50}}}
    sat = await web_sats._satellite_for(row, snapshot, {}, {}, {})
    assert sat.wifi.mode == "x" * 16
