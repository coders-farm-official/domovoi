"""The Plugins page (web/static/plugins.jsx) around upgrades, rendered
outside a browser with domovoi/tests/jsx_render_harness.js (the vendored
Babel, a plain-object React), so the assertions run without Postgres or a
server.

Covers: an upgrade staged for restart — the "restart to finish the
upgrade" card (wired to the dashboard's one restart) and the row pill; the
upgrade controls, which a bundled plugin doesn't get (it updates with the
core).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_render_harness.js")
PLUGINS = "web/static/plugins.jsx"
COMPONENTS = "web/static/components.jsx"

RADIO_WAITING = [{"slug": "radio", "from_version": "1.1.0", "to_version": "1.2.0",
                  "since": "2026-09-28T10:00:00+00:00", "where": ["core", "web"]}]

ROW = {
    "slug": "sleep", "name": "Sleep", "version": "0.4.0", "publisher": "Coders Farm",
    "license": "MIT", "enabled": True, "bundled": False, "install_source": "zip",
    "source_ref": "sleep.zip", "status": "ok", "last_error": None, "permissions": {},
    "provides": [], "consumes": [], "handlers": [], "pages": [], "page_errors": [],
    "android_capabilities": [], "web_load_error": None,
}
ROW_FNS = ["onEnable", "onDisable", "onUninstall", "onUpgradeZip", "onUpgradeUrl"]


def _card(version: dict, pending: list) -> dict:
    return {"file": PLUGINS, "component": "PluginRestartCard", "preload": [COMPONENTS],
            "props": {"version": version, "pending": pending},
            "fnProps": ["fire", "onSettled"]}


def _row(restart_pending: bool, **over) -> dict:
    return {"file": PLUGINS, "component": "PluginRow", "preload": [COMPONENTS],
            "props": {"p": {**ROW, **over}, "restartPending": restart_pending},
            "fnProps": ROW_FNS}


def _controls(**over) -> dict:
    return {"file": PLUGINS, "component": "PluginUpgradeControls", "preload": [COMPONENTS],
            "props": {"p": {**ROW, **over}}, "fnProps": ["onUpgradeZip", "onUpgradeUrl"]}


SCENARIOS = {
    "card": _card({"restart_capable": True, "restart_mode": "restart"}, RADIO_WAITING),
    "card_update_unit": _card({"restart_capable": True, "restart_mode": "update"}, RADIO_WAITING),
    "card_incapable": _card({"restart_capable": False, "restart_mode": "restart",
                             "restart_hint": "no passwordless sudoers grant"}, RADIO_WAITING),
    "card_nothing_waiting": _card({"restart_capable": True}, []),
    "row_waiting": _row(True),
    "row_not_waiting": _row(False),
    "controls_zip": _controls(),
    "controls_github": _controls(install_source="github"),
    "controls_bundled": _controls(slug="radio", bundled=True, install_source="bundled"),
    "controls_bundled_tombstone": _controls(bundled=True, install_source="bundled",
                                            status="uninstalled", enabled=False),
    "controls_dev": _controls(install_source="dev"),
}


@pytest.fixture(scope="module")
def rendered() -> dict:
    node = shutil.which("node")
    assert node, "node is required to render web/static JSX (see jsxcheck)"
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), json.dumps(SCENARIOS)],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _texts(elements: list[dict]) -> list[str]:
    return [e["text"] for e in elements if e.get("text")]


# ─── an upgrade staged for restart ────────────────────────────────────────


def test_the_card_names_what_is_waiting_and_offers_the_restart(rendered) -> None:
    texts = _texts(rendered["card"])
    assert "restart to finish the upgrade" in texts         # the card title
    assert any("radio 1.1.0 → 1.2.0" in t for t in texts), texts
    assert any(t == "Restart to finish the upgrade" for t in texts), texts
    # A host that can restart itself gets the button, not the command.
    assert not any("sudo systemctl" in t for t in texts), texts


def test_the_card_on_an_update_unit_host_still_offers_the_one_restart(rendered) -> None:
    texts = _texts(rendered["card_update_unit"])
    assert any(t == "Restart to finish the upgrade" for t in texts), texts


def test_a_host_that_cannot_restart_itself_gets_the_command(rendered) -> None:
    texts = _texts(rendered["card_incapable"])
    assert any("no passwordless sudoers grant" in t for t in texts), texts
    assert any(t == "sudo systemctl restart domovoi-core domovoi-web" for t in texts), texts
    assert not any(t == "Restart to finish the upgrade" for t in texts), texts


def test_nothing_waiting_renders_no_card(rendered) -> None:
    assert rendered["card_nothing_waiting"] == []


def test_the_row_of_a_staged_upgrade_says_so(rendered) -> None:
    assert "restart to finish the upgrade" in _texts(rendered["row_waiting"])
    assert "restart to finish the upgrade" not in _texts(rendered["row_not_waiting"])


# ─── bundled plugins update with the core ─────────────────────────────────


def test_a_zip_or_github_plugin_offers_both_upgrades(rendered) -> None:
    for case in ("controls_zip", "controls_github"):
        texts = _texts(rendered[case])
        assert "upgrade from zip" in texts, (case, texts)
        assert "upgrade from GitHub" in texts, (case, texts)


def test_a_bundled_plugin_offers_no_upgrade_and_says_why(rendered) -> None:
    texts = _texts(rendered["controls_bundled"])
    assert not any("upgrade from" in t for t in texts), texts
    assert any("bundled with Domovoi" in t and "updates with the Domovoi server" in t
               for t in texts), texts
    assert not any(e["type"] == "input" for e in rendered["controls_bundled"])


def test_tombstones_and_dev_plugins_show_no_upgrade_controls(rendered) -> None:
    assert rendered["controls_bundled_tombstone"] == []
    assert rendered["controls_dev"] == []
