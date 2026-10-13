"""Settings → Satellites shows a satellite's Wi-Fi by SIGNAL (2026-10-12).

The old indicator was `iw`'s last-received-frame rate, red below 5 Mbit/s,
and an idle link's last frame is a beacon at 1 Mbit/s: every idle satellite
looked broken. A satellite now reports its received signal (dBm); the card
and the drawer show that first, coloured at or above -60 good, -60 to -70
fair, below -70 weak, with the rate secondary and never a verdict. A
satellite too old to report a signal keeps exactly the old display. Also
here: the "setup hotspot" state and the "reboot needed" note an upgraded,
not-yet-rebooted satellite shows.

Driven through domovoi/tests/jsx_interact_harness.js, like
test_web_satellite_supports.py. No DB; needs ``node``.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from domovoi.tests.test_web_satellite_supports import (
    DEVICES, FILES, HARNESS, REPO_ROOT, _SETUP, _row,
)

_SCRIPT = r"""
const w = h.global('window'); h.render(); await w.__settle(h);
const card = h.find((e) => w.__cls(e).includes('sat-card'));
const cardText = w.__deep(card);
const signalEl = h.find((e) => e.props && typeof e.props.title === 'string' && e.props.title.startsWith('signal '));
await h.click((e) => w.__cls(e).includes('sat-card')); await w.__settle(h, 8);
const overview = h.text().join(' | ');
const tones = {
  good: w.wifiTone({ signal_dbm: -52, rx_mbits: 1.0 }),
  fair: w.wifiTone({ signal_dbm: -65, rx_mbits: 1.0 }),
  edge: w.wifiTone({ signal_dbm: -60, rx_mbits: 1.0 }),
  weak: w.wifiTone({ signal_dbm: -71, rx_mbits: 300.0 }),
  hotspot: w.wifiTone({ mode: 'ap' }),
  oldIdle: w.wifiTone({ rx_mbits: 1.0 }),
  oldNumber: w.wifiTone(1.0),
  oldFast: w.wifiTone(54.0),
  none: w.wifiTone(null),
};
return { cardText, signalTitle: signalEl ? signalEl.props.title : null, overview, tones };
"""


def _scenario(row: dict, sample: dict) -> dict:
    room = row["room_id"]
    return {
        "files": FILES, "component": "SatellitesPanel", "setup": _SETUP,
        "api": {
            "GET /api/satellites": [row],
            "GET /api/satellites/approvals": [],
            "GET /api/satellites/pending": [],
            "GET /api/config/version": {"sha": "44f0fff"},
            "GET /api/capabilities": {"host_kind": "linux"},
            f"GET /api/satellites/{room}/health": {
                "room_id": room, "connected": True, "last_outage": None, "outages": [],
                "history": None,
                "latest": {"received_at": "2026-10-12T15:00:00Z", "sample": sample},
            },
        },
        "script": _SCRIPT,
    }


def _with(wifi: dict, **sample_extra) -> dict:
    row = _row("kitchen", board="raspberry_pi_zero_2_w", full_duplex=True)
    row["wifi"] = wifi
    sample = dict(DEVICES["pi_xvf3800"]["health"]["stored"])
    sample["wifi"] = wifi
    sample.update(sample_extra)
    return _scenario(row, sample)


SCENARIOS = {
    "idle_good": _with({"rx_mbits": 1.0, "tx_mbits": 72.2, "ssid": "home",
                        "signal_dbm": -52, "mode": "managed"}),
    "weak": _with({"rx_mbits": 65.0, "tx_mbits": 72.2, "ssid": "home",
                   "signal_dbm": -78, "mode": "managed"}),
    "old_client": _with({"rx_mbits": 3.0, "tx_mbits": 5.0, "ssid": "home"}),
    "hotspot": _with({"rx_mbits": None, "tx_mbits": None, "ssid": None,
                      "signal_dbm": None, "mode": "ap"}),
    "reboot_needed": _with({"rx_mbits": 30.0, "tx_mbits": 72.2, "ssid": "home",
                            "signal_dbm": -58, "mode": "managed"},
                           reboot_needed="network settings are applied at boot: reboot this satellite once"),
    # What root's boot-time network step did (2026-10-12 follow-up).
    "step_applied": _with({"rx_mbits": 30.0, "signal_dbm": -58, "mode": "managed"},
                          hostname="domovoi-kitchen",
                          net_step={"state": "applied", "when": "boot", "at": 1760000000.0, "version": 2,
                                    "detail": "host name 'raspberrypi' -> 'domovoi-kitchen'",
                                    "early": "early rename unit disabled by the operator; left alone"}),
    "step_skipped": _with({"rx_mbits": 30.0, "signal_dbm": -58, "mode": "managed"},
                          hostname="kamrons-pi",
                          net_step={"state": "skipped", "when": "boot", "version": 2,
                                    "detail": "host name 'kamrons-pi' looks chosen by a person"}),
    "step_failed": _with({"rx_mbits": 30.0, "signal_dbm": -58, "mode": "managed"},
                         hostname="raspberrypi",
                         reboot_needed="the boot-time network settings did not all apply (x); reboot this satellite to retry",
                         net_step={"state": "failed", "when": "boot", "version": 2,
                                   "detail": "set the host name to 'domovoi-kitchen': read-only file system"}),
    "step_not_run": _with({"rx_mbits": 30.0, "signal_dbm": -58, "mode": "managed"},
                          hostname="raspberrypi",
                          reboot_needed="network settings ... are applied at boot: reboot this satellite once",
                          net_step={"state": "not_run", "version": 1,
                                    "detail": "has not run since this code arrived (version 2)"}),
}


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    spec = tmp_path_factory.mktemp("sat-wifi") / "scenarios.json"
    spec.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), f"@{spec}"],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items()
              if isinstance(v, dict) and "__harness_error" in v}
    assert not broken, broken
    return out


def test_an_idle_link_with_a_good_signal_is_good(driven):
    """The 2026-10-12 complaint: 1 Mbit/s idle read red. The signal says
    the link is fine, and the rate is only the secondary line."""
    r = driven["idle_good"]
    assert "-52 dBm" in r["cardText"]
    assert "1 Mbit/s" in r["cardText"]
    assert r["signalTitle"].startswith("signal good")
    assert "-52 dBm |  good" in r["overview"]


def test_a_weak_signal_reads_weak_whatever_the_rate(driven):
    r = driven["weak"]
    assert "-78 dBm" in r["cardText"]
    assert r["signalTitle"].startswith("signal weak")
    assert "-78 dBm |  weak" in r["overview"]


def test_the_bands(driven):
    t = driven["idle_good"]["tones"]
    assert (t["good"], t["edge"], t["fair"], t["weak"]) == ("ok", "ok", "warn", "err")
    assert t["hotspot"] == "err"


def test_an_old_satellite_keeps_todays_display(driven):
    """No signal reported: the rate, coloured as it always was."""
    r = driven["old_client"]
    assert "3 Mbit/s" in r["cardText"] and "dBm" not in r["cardText"]
    assert r["signalTitle"] is None
    t = r["tones"]
    assert (t["oldIdle"], t["oldNumber"], t["oldFast"], t["none"]) == ("err", "err", "ok", "idle")


def test_a_satellite_hosting_its_setup_hotspot_says_so(driven):
    r = driven["hotspot"]
    assert "setup hotspot" in r["cardText"]
    assert "setup hotspot on" in r["overview"]
    assert "not on the house Wi-Fi" in r["overview"]


def test_an_upgraded_satellite_that_needs_a_reboot_says_so(driven):
    r = driven["reboot_needed"]
    assert "reboot needed" in r["overview"]
    assert "Unplug it for a few seconds" in r["overview"]
    assert "reboot needed" not in driven["idle_good"]["overview"]


# ─── the boot network step (2026-10-12 follow-up) ─────────────────────────


def test_the_boot_step_and_host_name_are_shown(driven):
    o = driven["step_applied"]["overview"]
    assert "boot network step" in o and "applied" in o
    assert "host name domovoi-kitchen" in o
    assert "'raspberrypi' -> 'domovoi-kitchen'" in o
    assert "early rename unit disabled by the operator" in o
    assert "early rename unit" not in driven["step_skipped"]["overview"]


def test_a_skipped_rename_says_why(driven):
    o = driven["step_skipped"]["overview"]
    assert "skipped" in o and "looks chosen by a person" in o and "host name kamrons-pi" in o


def test_a_failed_step_asks_for_a_reboot_to_retry(driven):
    o = driven["step_failed"]["overview"]
    assert "reboot needed" in o and "failed" in o
    assert "did not all apply at this boot" in o and "Unplug it for a few seconds" in o
    assert "read-only file system" in o


def test_not_run_since_the_upgrade_asks_for_the_reboot(driven):
    o = driven["step_not_run"]["overview"]
    assert "reboot needed" in o and "not run" in o
    assert "Upgraded, but not rebooted yet" in o


def test_an_older_satellite_shows_no_boot_step(driven):
    assert "boot network step" not in driven["idle_good"]["overview"]
    assert "boot network step" not in driven["reboot_needed"]["overview"]
