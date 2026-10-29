"""A phone app is a phone app, whichever phone it is.

Two places on the server used to read the single string ``"android"``,
written when Android was the only app there was:

* ``web/backend/api/printers.py`` labels a print or scan job's source
  ``"app"`` or ``"dashboard"``;
* ``web/static/manual.jsx`` ticks the manual's "put the app on your phones"
  line when a phone app has registered.

An iPhone registers honestly as ``platform: "ios"``
(``POST /api/devices/register``), so before 2026-10-28 both read it wrong: a
print job from the iPhone app was labelled as coming from the dashboard, and
a house whose only phone app was an iPhone was told it had not put the app on
its phones.

These tests pin the widened rule, and pin that it is still a rule and not
"anything that registered": a browser must still read "dashboard".
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")

sys.path.insert(0, str(REPO_ROOT))


# ─── the print queue's "app" or "dashboard" label ────────────────────────

def _label(platform: str = "", user_agent: str = "") -> str:
    from web.backend.api.printers import source_label

    return source_label(platform, user_agent)


def test_both_phone_apps_count_as_the_app():
    assert _label(platform="android") == "app"
    assert _label(platform="ios") == "app"


def test_a_browser_is_still_the_dashboard():
    # The dashboard's own requests: a registered browser, and an unregistered
    # caller whose user agent is an ordinary one.
    assert _label(platform="browser") == "dashboard"
    assert _label(platform="", user_agent="mozilla/5.0 (macintosh) safari/605.1.15") == "dashboard"
    assert _label() == "dashboard"


def test_an_unregistered_phone_is_recognised_by_its_user_agent():
    # A device whose row we could not read, or that has not registered yet.
    # OkHttp is Android's HTTP library; the other two are our own product
    # names, which each app sets on its requests.
    assert _label(user_agent="okhttp/4.12.0") == "app"
    assert _label(user_agent="domovoi-android/1.0.0") == "app"
    assert _label(user_agent="domovoi-ios/1.0.0 cfnetwork/1494 darwin/24.0.0") == "app"


def test_the_platform_list_is_the_one_thing_to_change():
    """A third app, if there ever is one, is one line — not a grep."""
    from web.backend.api.printers import PHONE_APP_PLATFORMS

    assert PHONE_APP_PLATFORMS == frozenset({"android", "ios"})


# ─── the manual's "put the app on your phones" tick ──────────────────────

SCRIPT = r"""
const done = h.global('window').UserManual.checklistDone;
const ask = (devices) => done('phone_app', { devices });
return {
  android: ask([{ platform: 'android' }]),
  ios: ask([{ platform: 'ios' }]),
  both: ask([{ platform: 'browser' }, { platform: 'ios' }]),
  browsersOnly: ask([{ platform: 'browser' }, { platform: 'browser' }]),
  none: ask([]),
  // An admin-only read that failed leaves the line unticked, never ticked:
  // the manual says so, and `null` is how the page draws "we don't know".
  unknown: ask(undefined),
};
"""


@pytest.fixture(scope="module")
def checklist() -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to drive web/static JSX")
    scenario = {"d": {"files": ["web/static/components.jsx", "web/static/manual.jsx"],
                      "component": "UserManualPage", "script": SCRIPT}}
    proc = subprocess.run([node, str(HARNESS), str(REPO_ROOT), json.dumps(scenario)],
                          capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)["d"]
    assert "__harness_error" not in out, out.get("__harness_error")
    return out


def test_either_phone_app_ticks_the_manuals_app_line(checklist):
    assert checklist["android"] is True
    assert checklist["ios"] is True
    assert checklist["both"] is True


def test_browsers_alone_do_not_tick_it(checklist):
    assert checklist["browsersOnly"] is False
    assert checklist["none"] is False


def test_a_read_that_failed_leaves_it_unticked_rather_than_ticked(checklist):
    assert checklist["unknown"] is None
