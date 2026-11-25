"""The room drawer's Diagnostics tab (1.5.0): only for a connected device
that declared `diagnostics` (no misleading controls); Run all shows each
probe's result with a timestamp, and a probe can be run again on its own.

Driven through domovoi/tests/jsx_interact_harness.js like
test_web_satellite_supports.py. No DB; needs ``node``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from domovoi.tests.test_web_satellite_supports import FILES, _SETUP, _row

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")

_SCRIPT = r"""
const w = h.global('window'); h.render(); await w.__settle(h);
await h.click((e) => w.__cls(e).includes('sat-card')); await w.__settle(h);
const tab = h.find({ type: 'button', title: 'Diagnostics' });
if (!tab) return { tab: false };
await h.click({ type: 'button', title: 'Diagnostics' }); await w.__settle(h);
const before = h.text().join(' | ');
await h.click((e) => e.type === 'button' && w.__deep(e).includes('Run all')); await w.__settle(h);
const after = h.text().join(' | ');
await h.click((e) => e.type === 'button' && w.__deep(e).includes('Run again')); await w.__settle(h);
return { tab: true, before, after, calls: h.calls.map((c) => `${c.method} ${c.path}`) };
"""

_RESULTS = {
    "room_id": "office",
    "probes": {"network": "Network", "versions": "Versions"},
    "results": [
        {"probe": "network", "label": "Network", "ok": True,
         "text": "wifi network: #1a2b3c4d (name hashed)", "at": 1763800000.0},
        {"probe": "versions", "label": "Versions", "ok": False,
         "text": "the satellite didn't answer in time", "at": 1763800001.0},
    ],
}


def _scenario(row: dict) -> dict:
    room = row["room_id"]
    return {
        "files": FILES, "component": "SatellitesPanel", "setup": _SETUP,
        "api": {
            "GET /api/satellites": [row],
            "GET /api/satellites/approvals": [],
            "GET /api/satellites/pending": [],
            "GET /api/config/version": {"sha": "44f0fff"},
            "GET /api/capabilities": {"host_kind": "linux"},
            f"POST /api/satellites/{room}/diagnostics": _RESULTS,
            f"POST /api/satellites/{room}/diagnostics/network": {
                **_RESULTS, "results": [_RESULTS["results"][0]]},
        },
        "script": _SCRIPT,
    }


@pytest.fixture(scope="module")
def driven(tmp_path_factory) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX"
    declared = _row("office", board=None, supports=None)
    declared["diagnostics"] = True
    offline = dict(declared, status="offline", diagnostics=False)
    scenarios = {
        "declared": _scenario(declared),
        "not_declared": _scenario(_row("office", board=None, supports=None)),
        "offline": _scenario(offline),
    }
    spec = tmp_path_factory.mktemp("sat-diag") / "scenarios.json"
    spec.write_text(json.dumps(scenarios), encoding="utf-8")
    proc = subprocess.run([node, str(HARNESS), str(REPO_ROOT), f"@{spec}"],
                          capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items()
              if isinstance(v, dict) and "__harness_error" in v}
    assert not broken, broken
    return out


def test_no_tab_for_a_device_that_did_not_declare_it(driven):
    assert driven["not_declared"] == {"tab": False}
    assert driven["offline"] == {"tab": False}


def test_run_all_shows_each_probe_with_its_state_and_time(driven):
    r = driven["declared"]
    assert r["tab"] is True
    assert "nothing run yet" in r["before"]
    assert "Network" in r["after"] and "#1a2b3c4d" in r["after"]
    assert "Versions" in r["after"] and "failed" in r["after"]
    assert "didn't answer in time" in r["after"]
    assert "Copy" in r["after"]


def test_run_again_reruns_one_probe_by_name(driven):
    calls = driven["declared"]["calls"]
    assert "POST /api/satellites/office/diagnostics" in calls
    assert "POST /api/satellites/office/diagnostics/network" in calls
